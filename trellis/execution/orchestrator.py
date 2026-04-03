"""
Async Orchestrator for Trellis pipelines.

Responsibilities:
- Build ResolutionContext from Pipeline + inputs + session
- Build AsyncToolRegistry via discovery
- Call execute_pipeline with options and optional event sink
- Return structured RunResult with outputs, stats, and collected events
- Provide cancel() to request cooperative cancellation
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trellis.execution.blackboard import Blackboard, InMemoryBlackboard
from trellis.execution.dag import ExecutionOptions, PipelineResult, execute_pipeline
from trellis.execution.template import ResolutionContext
from trellis.models.pipeline import Pipeline
from trellis.tools.registry import AsyncToolRegistry, build_default_registry


@dataclass
class RunResult:
    """Structured result from an orchestrated pipeline run."""

    outputs: Dict[str, Any]
    waves_executed: int
    tasks_executed: int
    events: List[Dict[str, Any]] = field(default_factory=list)


class Orchestrator:
    """High-level runner for executing pipelines."""

    def __init__(
        self,
        registry: Optional[AsyncToolRegistry] = None,
        *,
        blackboard: Blackboard | None = None,
        tenant_id: str = "default",
    ) -> None:
        self.registry = registry or build_default_registry()
        self._cancel_event: asyncio.Event = asyncio.Event()
        self.blackboard: Blackboard = blackboard or InMemoryBlackboard()
        self.tenant_id: str = tenant_id

    def cancel(self) -> None:
        """Request cooperative cancellation before scheduling the next wave."""
        self._cancel_event.set()

    async def run_pipeline(
        self,
        pipeline: Pipeline,
        *,
        inputs: Dict[str, Any] | None = None,
        session: Dict[str, Any] | None = None,
        options: Optional[ExecutionOptions] = None,
        collect_events: bool = True,
    ) -> RunResult:
        """
        Execute a pipeline and return a structured RunResult.
        """
        ctx = ResolutionContext(
            task_outputs={},
            pipeline_inputs=inputs or pipeline.inputs or {},
            pipeline_goal=pipeline.goal,
            session=session or {},
            tenant_id=self.tenant_id,
            blackboard=self.blackboard,
        )

        if options is None:
            options = ExecutionOptions()
        # Inject cancel event for cooperative cancellation
        options.cancel_event = self._cancel_event

        events: List[Dict[str, Any]] = [] if collect_events else []
        event_sink = events if collect_events else None

        result: PipelineResult = await execute_pipeline(
            pipeline,
            self.registry,
            ctx,
            options=options,
            event_sink=event_sink,
        )

        return RunResult(
            outputs=result.outputs,
            waves_executed=result.waves_executed,
            tasks_executed=result.tasks_executed,
            events=events,
        )
