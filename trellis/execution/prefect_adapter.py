"""
Prefect adapter (skeleton).

This module will provide a Prefect-backed executor that maps a Trellis Pipeline
onto a Prefect Flow/Tasks graph. Not implemented yet; kept as a placeholder to
stabilize the public surface for future integration.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from trellis.execution.dag import ExecutionOptions
from trellis.execution.orchestrator import RunResult
from trellis.models.pipeline import Pipeline


class PrefectExecutor:
    def __init__(self, *, work_pool: str | None = None, deployment_name: str | None = None) -> None:
        self.work_pool = work_pool
        self.deployment_name = deployment_name

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
        raise NotImplementedError("Prefect integration not yet implemented")

    async def get_result(self, run_id: str) -> RunResult:
        raise NotImplementedError

    async def cancel(self, run_id: str) -> bool:
        raise NotImplementedError

