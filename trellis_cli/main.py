"""
Trellis CLI — validate and run pipelines.

Commands:
  trellis validate PATH
  trellis run PATH [--inputs INPUTS_JSON] [--session SESSION_JSON]
                  [--timeout SECONDS] [--concurrency N]
                  [--jitter FRACTION] [--json]

Examples (PowerShell):
  trellis validate .\pipelines\example.yaml
  trellis run .\pipelines\example.yaml --inputs '{"param":"value"}' --timeout 30 --concurrency 5 --json
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator
from trellis.execution.dag import ExecutionOptions

app = typer.Typer(help="Trellis CLI — validate and run pipelines")


def _load_pipeline(path: Path) -> Pipeline:
    text = path.read_text(encoding="utf-8")
    return Pipeline.from_yaml(text)


@app.command()
def validate(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to pipeline YAML"),
) -> None:
    """Validate a pipeline YAML file."""
    try:
        _ = _load_pipeline(path)
        typer.secho("Pipeline is valid.", fg=typer.colors.GREEN)
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
) -> None:
    """Run a pipeline YAML with options."""
    try:
        pipeline = _load_pipeline(path)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Failed to load pipeline: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

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
        if output_json:
            typer.echo(json.dumps(result.outputs, ensure_ascii=False, indent=2))
        else:
            typer.secho("Outputs:", fg=typer.colors.GREEN)
            typer.echo(json.dumps(result.outputs, ensure_ascii=False, indent=2))
            typer.secho("\nStats:", fg=typer.colors.GREEN)
            typer.echo(json.dumps({
                "waves_executed": result.waves_executed,
                "tasks_executed": result.tasks_executed,
            }, ensure_ascii=False, indent=2))
            if result.events:
                typer.secho("\nEvents:", fg=typer.colors.GREEN)
                typer.echo(json.dumps(result.events, ensure_ascii=False, indent=2))

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Run failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=3)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

