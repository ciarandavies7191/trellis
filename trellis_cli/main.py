"""
Trellis CLI — validate, run, and compile pipelines.

Commands:
  trellis validate PATH
  trellis run PATH [--inputs INPUTS_JSON] [--params PARAMS_JSON] [--session SESSION_JSON]
                  [--session-file PATH]
                  [--timeout SECONDS] [--concurrency N]
                  [--jitter FRACTION] [--json]
                  [--llm-provider NAME]
                  [--llm-model NAME]
                  [--openai-api-key KEY] [--openai-model NAME]
                  [--anthropic-api-key KEY] [--anthropic-model NAME]
                  [--ollama-host URL] [--ollama-model NAME]
                  [--extract-model NAME]
                  [--env-file PATH]
  trellis compile [PROMPT] [--prompt-file PATH] [--output PATH]
                  [--model NAME] [--max-repairs N] [--json]
                  [--env-file PATH]

Examples (PowerShell):
  trellis validate .\pipelines\example.yaml
  trellis validate .\pipelines\spreads\plan.yaml
  trellis run .\pipelines\example.yaml --inputs '{"param":"value"}' --timeout 30 --concurrency 5 --json
  trellis run .\pipelines\spreads\data_acquisition.yaml --session-file .\session.json
  trellis run .\pipelines\spreads\plan.yaml --env-file .env
  trellis run .\pipelines\example.yaml --llm-provider openai --openai-api-key $env:OPENAI_API_KEY --openai-model gpt-4o
  trellis run .\pipelines\example.yaml --env-file .env
  trellis compile "Fetch Apple 10-K from SEC EDGAR and summarise key risks" --output pipeline.yaml
  trellis compile --prompt-file my_prompt.txt --model anthropic/claude-haiku-4-5-20251001
  trellis compile "Summarise a PDF" --json > pipeline.yaml
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Any
import dataclasses
import logging

import yaml
import typer
from dotenv import load_dotenv  # type: ignore

from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan
from trellis.execution.orchestrator import Orchestrator, PlanRunResult
from trellis.execution.dag import ExecutionOptions
from trellis.validation.graph import pipeline_execution_waves, plan_execution_waves
from trellis.validation.contract import validate_contract
from trellis.compiler import PipelineCompiler, CompilerError

app = typer.Typer(help="Trellis CLI — validate and run pipelines", no_args_is_help=True)


def _configure_logging() -> None:
    """Configure root logging once at DEBUG level with a concise format."""
    if logging.getLogger().handlers:
        # Respect existing configuration (e.g., tests)
        return
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    # Make sure our package logs are visible
    logging.getLogger("trellis").setLevel(logging.DEBUG)


def _load_pipeline(path: Path) -> Pipeline:
    text = path.read_text(encoding="utf-8")
    return Pipeline.from_yaml(text)


def _detect_yaml_kind(path: Path) -> str:
    """Return 'plan' or 'pipeline' based on the YAML root key."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        if "plan" in doc:
            return "plan"
        if "pipeline" in doc:
            return "pipeline"
    keys = list(doc.keys()) if isinstance(doc, dict) else type(doc).__name__
    raise ValueError(
        f"YAML must have a top-level `plan:` or `pipeline:` key. Got: {keys}"
    )


def _validate_plan(path: Path) -> dict[str, Any]:
    """
    Validate a plan YAML and all co-located sub-pipeline YAMLs.

    For each sub-pipeline entry whose {id}.yaml sibling exists, loads the
    pipeline and runs validate_contract to catch stores/reads/inputs violations.

    Returns a stats dict suitable for display.
    """
    plan = Plan.from_yaml(path.read_text(encoding="utf-8"))
    waves = plan_execution_waves(plan)

    # Locate sibling pipeline files (same directory, named {id}.yaml)
    plan_dir = path.parent
    contract_results: list[dict[str, Any]] = []

    for sp in plan.sub_pipelines:
        sibling = plan_dir / f"{sp.id}.yaml"
        if not sibling.exists():
            contract_results.append({
                "id": sp.id,
                "file": str(sibling),
                "status": "not_found",
                "violations": [],
            })
            continue

        try:
            pipeline = Pipeline.from_yaml(sibling.read_text(encoding="utf-8"))
        except Exception as exc:
            contract_results.append({
                "id": sp.id,
                "file": str(sibling),
                "status": "parse_error",
                "error": str(exc),
                "violations": [],
            })
            continue

        violations = validate_contract(pipeline, sp)
        contract_results.append({
            "id": sp.id,
            "file": str(sibling),
            "status": "ok" if not violations else "violations",
            "violations": [
                {
                    "kind": v.kind.value,
                    "key": v.key,
                    "task_id": v.task_id,
                    "message": v.message,
                }
                for v in violations
            ],
        })

    return {
        "id": plan.id,
        "goal": plan.goal,
        "sub_pipelines": len(plan.sub_pipelines),
        "waves": len(waves),
        "wave_ids": [[sp.id for sp in wave] for wave in waves],
        "contract_checks": contract_results,
    }


def _json_sanitize(value: Any) -> Any:
    """Make values JSON-serializable (mirror API sanitizer)."""
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": True, "size": len(value)}
    if dataclasses.is_dataclass(value):
        try:
            value = dataclasses.asdict(value)
        except Exception:
            value = dict(value.__dict__)  # type: ignore[attr-defined]
    if isinstance(value, dict):
        return {k: _json_sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_sanitize(v) for v in list(value)]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return str(value)
    except Exception:
        return repr(value)


def _load_env_file(env_file: Optional[Path]) -> None:
    """Load environment variables from a .env file.

    Precedence: existing environment variables are preserved; .env only fills missing keys.
    If env_file is None, load from "./.env" when present.
    """
    try:
        if env_file is not None:
            if env_file.exists():
                load_dotenv(dotenv_path=env_file, override=False)
                logging.getLogger(__name__).debug("Loaded environment from %s", str(env_file))
            else:
                logging.getLogger(__name__).warning("--env-file %s not found — skipping.", str(env_file))
        else:
            default_path = Path.cwd() / ".env"
            if default_path.exists():
                load_dotenv(dotenv_path=default_path, override=False)
                logging.getLogger(__name__).debug("Loaded environment from %s", str(default_path))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("Failed to load .env: %s", exc)


@app.command()
def validate(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to pipeline or plan YAML"),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        exists=False,
        help="Path to a .env file to load (default: ./.env if present)",
    ),
) -> None:
    """Validate a pipeline or plan YAML file and print basic stats.

    Detects whether the file is a plan (top-level `plan:` key) or a pipeline
    (`pipeline:` key) and validates accordingly.

    For plans, also locates co-located sub-pipeline YAMLs (same directory,
    named <id>.yaml) and validates each against its contract: required session
    keys stored, session refs declared in reads, inputs declared in pipeline.inputs.
    """
    _configure_logging()
    _load_env_file(env_file)
    try:
        kind = _detect_yaml_kind(path)
    except Exception as exc:
        typer.secho(f"Validation failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if kind == "plan":
        _run_plan_validate(path)
    else:
        _run_pipeline_validate(path)


def _run_pipeline_validate(path: Path) -> None:
    try:
        pipeline = _load_pipeline(path)
        waves = pipeline_execution_waves(pipeline)
        stats = {
            "id": pipeline.id,
            "goal": pipeline.goal,
            "tasks": len(pipeline.tasks),
            "tools": sorted({t.tool for t in pipeline.tasks}),
            "inputs_count": len(pipeline.inputs or {}),
            "store_keys": pipeline.store_keys(),
            "waves": len(waves),
            "wave_sizes": [len(w) for w in waves],
            "fan_out_tasks": sum(1 for t in pipeline.tasks if t.parallel_over is not None),
            "total_retries": sum(int(getattr(t, "retry", 0) or 0) for t in pipeline.tasks),
        }
        typer.secho("Pipeline is valid.", fg=typer.colors.GREEN)
        typer.secho("Stats:", fg=typer.colors.GREEN)
        typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Validation failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


def _run_plan_validate(path: Path) -> None:
    try:
        stats = _validate_plan(path)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Plan validation failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    checks = stats.pop("contract_checks")
    all_ok = all(c["status"] in ("ok", "not_found") for c in checks)
    has_violations = any(c["status"] == "violations" for c in checks)
    has_errors = any(c["status"] == "parse_error" for c in checks)

    color = typer.colors.GREEN if all_ok else typer.colors.RED
    label = "Plan is valid." if all_ok else "Plan has contract violations."
    typer.secho(label, fg=color)
    typer.secho("Stats:", fg=typer.colors.GREEN)
    typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))

    typer.secho("\nContract checks:", fg=typer.colors.GREEN)
    for check in checks:
        status = check["status"]
        sid = check["id"]
        if status == "ok":
            typer.secho(f"  ✓ {sid}", fg=typer.colors.GREEN)
        elif status == "not_found":
            typer.secho(f"  ? {sid}  (no sibling {sid}.yaml found — skipped)", fg=typer.colors.YELLOW)
        elif status == "parse_error":
            typer.secho(f"  ✗ {sid}  parse error: {check['error']}", fg=typer.colors.RED)
        else:
            typer.secho(f"  ✗ {sid}  ({len(check['violations'])} violation(s))", fg=typer.colors.RED)
            for v in check["violations"]:
                task_hint = f" [task: {v['task_id']}]" if v["task_id"] else ""
                typer.secho(f"      [{v['kind']}]{task_hint} {v['message']}", fg=typer.colors.RED)

    if has_violations or has_errors:
        raise typer.Exit(code=1)


@app.command()
def run(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to pipeline or plan YAML"),
    inputs: Optional[str] = typer.Option(
        None,
        "--inputs",
        help="JSON string of pipeline inputs (e.g., '{\"param\":\"value\"}')",
    ),
    params: Optional[str] = typer.Option(
        None,
        "--params",
        help="JSON string of typed pipeline params (e.g., '{\"ticker\":\"AAPL\",\"fiscal_year\":2023}')",
    ),
    session: Optional[str] = typer.Option(
        None,
        "--session",
        help="JSON string of session values (e.g., '{\"token\":\"...\"}')",
    ),
    session_file: Optional[Path] = typer.Option(
        None,
        "--session-file",
        exists=False,
        help="Path to a JSON file containing session values. Merged with --session; --session takes precedence on key conflicts.",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        min=0.0,
        help="Per-task timeout in seconds (applies to each attempt)",
    ),
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        min=1,
        help="Fan-out concurrency limit for parallel_over tasks",
    ),
    jitter: float = typer.Option(
        0.0,
        "--jitter",
        min=0.0,
        max=1.0,
        help="Jitter fraction applied to retry backoff (0.2 = ±20%)",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Print only the final outputs as JSON",
    ),
    # LLM configuration overrides
    llm_provider: Optional[str] = typer.Option(
        None,
        "--llm-provider",
        help="Default LLM provider for llm_job (openai|ollama|anthropic)",
    ),
    llm_model: Optional[str] = typer.Option(
        None,
        "--llm-model",
        help="Default LLM model for tools that support a single 'model' (also sets EXTRACT_TEXT_MODEL)",
    ),
    openai_api_key: Optional[str] = typer.Option(
        None,
        "--openai-api-key",
        envvar=None,
        help="OPENAI_API_KEY value to export for this run",
    ),
    openai_model: Optional[str] = typer.Option(
        None,
        "--openai-model",
        help="OPENAI_MODEL override (e.g., gpt-4o)",
    ),
    anthropic_api_key: Optional[str] = typer.Option(
        None,
        "--anthropic-api-key",
        help="ANTHROPIC_API_KEY value to export for this run",
    ),
    anthropic_model: Optional[str] = typer.Option(
        None,
        "--anthropic-model",
        help="ANTHROPIC_MODEL override (e.g., claude-3-haiku-20240307)",
    ),
    ollama_host: Optional[str] = typer.Option(
        None,
        "--ollama-host",
        help="OLLAMA_HOST base URL (e.g., http://localhost:11434)",
    ),
    ollama_model: Optional[str] = typer.Option(
        None,
        "--ollama-model",
        help="OLLAMA_MODEL override (e.g., llama3)",
    ),
    extract_model: Optional[str] = typer.Option(
        None,
        "--extract-model",
        help="Default model for extract_text (EXTRACT_TEXT_MODEL; litellm model string)",
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        exists=False,
        help="Path to a .env file to load (default: ./.env if present)",
    ),
) -> None:
    """Run a pipeline or plan YAML with options."""
    _configure_logging()
    _load_env_file(env_file)
    logging.getLogger(__name__).debug(
        "CLI run invoked: path=%s, timeout=%s, concurrency=%s, jitter=%.2f, json=%s",
        str(path), timeout, concurrency, jitter, output_json,
    )

    # Detect kind before loading
    try:
        kind = _detect_yaml_kind(path)
    except Exception as exc:
        typer.secho(f"Failed to detect YAML kind: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if kind == "plan":
        try:
            plan_obj = Plan.from_yaml(path.read_text(encoding="utf-8"))
        except Exception as exc:
            typer.secho(f"Failed to load plan: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
    else:
        try:
            pipeline = _load_pipeline(path)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"Failed to load pipeline: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)

    # Apply LLM/env overrides for this run only
    if llm_provider:
        os.environ["TRELLIS_LLM_PROVIDER"] = llm_provider
    if llm_model:
        # Generic default for tools that look for a single model value (e.g., extract_text)
        os.environ["EXTRACT_TEXT_MODEL"] = llm_model
    if openai_api_key:
        os.environ["OPENAI_API_KEY"] = openai_api_key
    if openai_model:
        os.environ["OPENAI_MODEL"] = openai_model
    if anthropic_api_key:
        os.environ["ANTHROPIC_API_KEY"] = anthropic_api_key
    if anthropic_model:
        os.environ["ANTHROPIC_MODEL"] = anthropic_model
    if ollama_host:
        os.environ["OLLAMA_HOST"] = ollama_host
    if ollama_model:
        os.environ["OLLAMA_MODEL"] = ollama_model
    if extract_model:
        os.environ["EXTRACT_TEXT_MODEL"] = extract_model

    try:
        inputs_obj = json.loads(inputs) if inputs else None
        params_obj = json.loads(params) if params else None
        # Build session: start from file (if any), then overlay inline --session values.
        session_obj: dict | None = None
        if session_file is not None:
            if not session_file.exists():
                typer.secho(f"--session-file {session_file} not found.", fg=typer.colors.RED)
                raise typer.Exit(code=2)
            session_obj = json.loads(session_file.read_text(encoding="utf-8"))
            if not isinstance(session_obj, dict):
                typer.secho("--session-file must contain a JSON object.", fg=typer.colors.RED)
                raise typer.Exit(code=2)
        if session:
            inline = json.loads(session)
            session_obj = {**(session_obj or {}), **inline}
    except json.JSONDecodeError as exc:
        typer.secho(f"Invalid JSON: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    options = ExecutionOptions(
        per_task_timeout=timeout,
        fan_out_concurrency=concurrency,
        backoff_jitter=jitter,
    )

    orch = Orchestrator()

    if kind == "plan":
        def _progress_cb(sp_id: str, wave_idx: int, total_waves: int, sp_idx: int, wave_size: int) -> None:
            typer.secho(
                f"  [{wave_idx + 1}/{total_waves}] Running sub-pipeline: {sp_id}",
                fg=typer.colors.CYAN,
            )

        async def _run_plan() -> None:
            if not output_json:
                typer.secho(f"Running plan: {plan_obj.id}", fg=typer.colors.BRIGHT_WHITE)
            result: PlanRunResult = await orch.run_plan(
                plan_obj,
                path.parent,
                inputs=inputs_obj,
                session=session_obj,
                options=options,
                collect_events=not output_json,
                progress_cb=None if output_json else _progress_cb,
            )
            safe_bb = _json_sanitize(result.blackboard)
            safe_events = _json_sanitize(result.events) if (not output_json and result.events) else None
            if output_json:
                typer.echo(json.dumps(safe_bb, ensure_ascii=False, indent=2))
            else:
                typer.secho("\nFinal blackboard:", fg=typer.colors.GREEN)
                typer.echo(json.dumps(safe_bb, ensure_ascii=False, indent=2))
                typer.secho("\nStats:", fg=typer.colors.GREEN)
                typer.echo(json.dumps({
                    "plan_id": result.plan_id,
                    "total_waves": result.total_waves,
                    "total_tasks_executed": result.total_tasks_executed,
                    "sub_pipelines": list(result.sub_results.keys()),
                }, ensure_ascii=False, indent=2))
                if safe_events:
                    typer.secho("\nEvents:", fg=typer.colors.GREEN)
                    typer.echo(json.dumps(safe_events, ensure_ascii=False, indent=2))

        try:
            asyncio.run(_run_plan())
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"Run failed: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=3)

    else:
        async def _run() -> None:
            result = await orch.run_pipeline(
                pipeline,
                inputs=inputs_obj,
                params=params_obj,
                session=session_obj,
                options=options,
                collect_events=not output_json,
            )
            safe_outputs = _json_sanitize(result.outputs)
            safe_events = _json_sanitize(result.events) if (not output_json and result.events) else None
            if output_json:
                typer.echo(json.dumps(safe_outputs, ensure_ascii=False, indent=2))
            else:
                typer.secho("Outputs:", fg=typer.colors.GREEN)
                typer.echo(json.dumps(safe_outputs, ensure_ascii=False, indent=2))
                typer.secho("\nStats:", fg=typer.colors.GREEN)
                typer.echo(json.dumps({
                    "waves_executed": result.waves_executed,
                    "tasks_executed": result.tasks_executed,
                }, ensure_ascii=False, indent=2))
                if safe_events:
                    typer.secho("\nEvents:", fg=typer.colors.GREEN)
                    typer.echo(json.dumps(safe_events, ensure_ascii=False, indent=2))

        try:
            asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"Run failed: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=3)


@app.command()
def compile(
    prompt: Optional[str] = typer.Argument(
        None,
        help="Natural-language description of the pipeline to compile. "
             "Pass either this argument or --prompt-file.",
    ),
    prompt_file: Optional[Path] = typer.Option(
        None,
        "--prompt-file",
        exists=True,
        readable=True,
        help="Path to a text file whose contents are used as the prompt.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the compiled YAML to this file. "
             "If omitted, the YAML is printed to stdout.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="litellm model string to use for compilation "
             "(e.g. 'anthropic/claude-haiku-4-5-20251001'). "
             "Falls back to TRELLIS_COMPILER_MODEL → TRELLIS_LLM_MODEL.",
    ),
    max_repairs: int = typer.Option(
        2,
        "--max-repairs",
        min=0,
        help="Maximum number of repair attempts after a validation failure.",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Print only the compiled YAML to stdout, with no decorative output. "
             "Useful for piping: trellis compile '...' --json > pipeline.yaml",
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        exists=False,
        help="Path to a .env file to load (default: ./.env if present)",
    ),
) -> None:
    """Compile a natural-language prompt into a validated pipeline YAML.

    The prompt can be supplied as a positional argument or read from a file
    with --prompt-file.  Exactly one of these must be provided.

    The compiler calls an LLM (configured via TRELLIS_COMPILER_MODEL or
    TRELLIS_LLM_MODEL) and validates the response against the Pipeline/Plan
    models.  If the first attempt fails validation, it retries with the
    error context up to --max-repairs times.

    By default the compiled YAML is printed to stdout.  Use --output to
    write it to a file instead.  Use --json to suppress all decorative
    output and emit only the raw YAML (good for shell pipelines).
    """
    _configure_logging()
    _load_env_file(env_file)

    # --- Resolve prompt text -------------------------------------------
    if prompt and prompt_file:
        typer.secho(
            "Provide either a PROMPT argument or --prompt-file, not both.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    if not prompt and not prompt_file:
        typer.secho(
            "A prompt is required. Pass it as an argument or use --prompt-file.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    if prompt_file:
        prompt_text = prompt_file.read_text(encoding="utf-8").strip()
        if not prompt_text:
            typer.secho(f"Prompt file {prompt_file} is empty.", fg=typer.colors.RED)
            raise typer.Exit(code=2)
    else:
        prompt_text = (prompt or "").strip()
        if not prompt_text:
            typer.secho("Prompt must not be empty.", fg=typer.colors.RED)
            raise typer.Exit(code=2)

    # --- Run compiler --------------------------------------------------
    compiler_instance = PipelineCompiler(model=model or None)

    if not output_json:
        typer.secho("Compiling…", fg=typer.colors.CYAN)

    async def _compile() -> None:
        return await compiler_instance.compile(
            prompt_text,
            max_repair_attempts=max_repairs,
        )

    try:
        result = asyncio.run(_compile())
    except CompilerError as exc:
        typer.secho(f"Compilation failed after {exc.attempts} attempt(s):", fg=typer.colors.RED)
        typer.secho(f"  {exc.last_error}", fg=typer.colors.RED)
        if exc.last_yaml and not output_json:
            typer.secho("\nLast LLM output:", fg=typer.colors.YELLOW)
            typer.echo(exc.last_yaml)
        raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Compilation error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    # --- Output --------------------------------------------------------
    yaml_text = result.yaml_text

    if output_json:
        # Machine-readable mode: raw YAML only, no decoration.
        typer.echo(yaml_text)
        return

    # Identify what was compiled for the summary line.
    artifact = result.artifact
    kind = "pipeline" if result.is_pipeline else "plan"
    artifact_id = getattr(artifact, "id", "?")

    repair_note = ""
    if result.attempts > 1:
        repair_note = f" ({result.attempts - 1} repair(s) needed)"

    if output:
        output.write_text(yaml_text, encoding="utf-8")
        typer.secho(
            f"Compiled {kind} '{artifact_id}'{repair_note} -> {output}",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"Compiled {kind} '{artifact_id}'{repair_note}:",
            fg=typer.colors.GREEN,
        )
        typer.echo(yaml_text)


def main() -> None:
    _configure_logging()
    app()


if __name__ == "__main__":
    main()

