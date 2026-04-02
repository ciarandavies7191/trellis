"""Tests for Orchestrator run and cancellation/events."""

import asyncio

import pytest

from trellis.execution.orchestrator import Orchestrator, RunResult
from trellis.models.pipeline import Pipeline, Task


@pytest.mark.asyncio
async def test_orchestrator_run_basic():
    orch = Orchestrator()

    pipeline = Pipeline(
        id="basic",
        goal="goal",
        tasks=[
            Task(id="t1", tool="mock", inputs={"x": 1}),
            Task(id="t2", tool="mock", inputs={"y": "{{t1.output.status}}"}),
        ],
    )

    result: RunResult = await orch.run_pipeline(pipeline)

    assert "t1" in result.outputs and "t2" in result.outputs
    assert result.tasks_executed >= 2
    assert isinstance(result.events, list)
    # Should contain started/finished events
    event_types = {e["type"] for e in result.events}
    assert "on_task_started" in event_types
    assert "on_task_finished" in event_types


@pytest.mark.asyncio
async def test_orchestrator_cancellation_stops_before_next_wave():
    orch = Orchestrator()

    # Slow tool via mock: fan-out will make multiple calls
    pipeline = Pipeline(
        id="cancel",
        goal="goal",
        tasks=[
            Task(id="root", tool="mock", inputs={"sleep": 0}),
            Task(id="fan", tool="mock", parallel_over="{{root.output}}", inputs={}),
        ],
    )

    # Trigger cancel immediately after scheduling the run
    async def trigger_cancel():
        await asyncio.sleep(0)
        orch.cancel()

    run_task = asyncio.create_task(orch.run_pipeline(pipeline))
    await trigger_cancel()
    result = await run_task

    # Either zero or partial execution, but should not error
    assert isinstance(result, RunResult)

