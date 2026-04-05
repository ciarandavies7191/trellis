"""
Trellis CLI — validate and run pipelines.

Commands:
  trellis validate PATH
  trellis run PATH [--inputs INPUTS_JSON] [--session SESSION_JSON]
                  [--timeout SECONDS] [--concurrency N]
                  [--jitter FRACTION] [--json]
                  [--llm-provider NAME]
                  [--llm-model NAME]
                  [--openai-api-key KEY] [--openai-model NAME]
                  [--anthropic-api-key KEY] [--anthropic-model NAME]
                  [--ollama-host URL] [--ollama-model NAME]
                  [--extract-model NAME]
                  [--env-file PATH]

Examples (PowerShell):
  trellis validate .\pipelines\example.yaml
  trellis run .\pipelines\example.yaml --inputs '{"param":"value"}' --timeout 30 --concurrency 5 --json
  trellis run .\pipelines\example.yaml --llm-provider openai --openai-api-key $env:OPENAI_API_KEY --openai-model gpt-4o
  trellis run .\pipelines\example.yaml --env-file .env
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Any
import dataclasses
import logging

import typer
from dotenv import load_dotenv  # type: ignore

from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator
from trellis.execution.dag import ExecutionOptions
from trellis.validation.graph import pipeline_execution_waves

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
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to pipeline YAML"),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        exists=False,
        help="Path to a .env file to load (default: ./.env if present)",
    ),
) -> None:
    """Validate a pipeline YAML file and print basic stats."""
    _configure_logging()
    _load_env_file(env_file)
    try:
        pipeline = _load_pipeline(path)
        # Build execution waves (validates acyclicity)
        waves = pipeline_execution_waves(pipeline)
        stats = {
            "id": pipeline.id,
            "title": pipeline.goal,
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


@app.command()
def run(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to pipeline YAML"),
    inputs: Optional[str] = typer.Option(
        None,
        "--inputs",
        help="JSON string of pipeline inputs (e.g., '{\"param\":\"value\"}')",
    ),
    session: Optional[str] = typer.Option(
        None,
        "--session",
        help="JSON string of session values (e.g., '{\"token\":\"...\"}')",
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
    """Run a pipeline YAML with options."""
    _configure_logging()
    _load_env_file(env_file)
    logging.getLogger(__name__).debug(
        "CLI run invoked: path=%s, timeout=%s, concurrency=%s, jitter=%.2f, json=%s",
        str(path), timeout, concurrency, jitter, output_json,
    )
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
        session_obj = json.loads(session) if session else None
    except json.JSONDecodeError as exc:
        typer.secho(f"Invalid JSON: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    options = ExecutionOptions(
        per_task_timeout=timeout,
        fan_out_concurrency=concurrency,
        backoff_jitter=jitter,
    )

    orch = Orchestrator()

    async def _run() -> None:
        result = await orch.run_pipeline(
            pipeline,
            inputs=inputs_obj,
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


def main() -> None:
    _configure_logging()
    app()


if __name__ == "__main__":
    main()

