"""
In-memory run queue and status store for background pipeline execution.

This is a simple fallback executor suitable for local/dev. It can be replaced by
Prefect/Airflow adapters without changing API callers.
"""
from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import Any, Dict, Optional, Literal

from trellis.execution.dag import ExecutionOptions
from trellis.execution.orchestrator import Orchestrator, RunResult
from trellis.models.pipeline import Pipeline


RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclasses.dataclass
class RunRecord:
    run_id: str
    status: RunStatus = "queued"
    tenant_id: str = "default"
    result: Dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] | None = None
    _task: asyncio.Task | None = None
    _orch: Orchestrator | None = None


class InMemoryRunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    def _new_id(self) -> str:
        return uuid.uuid4().hex

    async def submit(
        self,
        pipeline: Pipeline,
        *,
        inputs: Dict[str, Any] | None = None,
        session: Dict[str, Any] | None = None,
        options: Optional[ExecutionOptions] = None,
        tenant_id: str = "default",
        collect_events: bool = True,
    ) -> str:
        run_id = self._new_id()
        rec = RunRecord(run_id=run_id, status="queued", tenant_id=tenant_id)
        async with self._lock:
            self._runs[run_id] = rec

        async def _worker() -> None:
            rec.status = "running"
            orch = Orchestrator(tenant_id=tenant_id)
            rec._orch = orch
            try:
                result: RunResult = await orch.run_pipeline(
                    pipeline,
                    inputs=inputs,
                    session=session,
                    options=options,
                    collect_events=collect_events,
                )
            except Exception as exc:  # noqa: BLE001
                rec.status = "failed"
                rec.error = str(exc)
                rec._orch = None
                return
            # Success
            rec.status = "succeeded"
            rec.result = {
                "outputs": result.outputs,
                "waves_executed": result.waves_executed,
                "tasks_executed": result.tasks_executed,
            }
            rec.events = result.events
            rec._orch = None

        task = asyncio.create_task(_worker())
        rec._task = task
        return run_id

    async def get(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def cancel(self, run_id: str) -> bool:
        rec = await self.get(run_id)
        if not rec:
            return False
        if rec.status not in ("queued", "running"):
            return False
        if rec._orch is not None:
            rec._orch.cancel()
            rec.status = "cancelled"
            return True
        # If still queued with a scheduled task, cancel it
        if rec._task and not rec._task.done():
            rec._task.cancel()
            rec.status = "cancelled"
            return True
        return False


# Global singleton for API module
run_manager = InMemoryRunManager()

