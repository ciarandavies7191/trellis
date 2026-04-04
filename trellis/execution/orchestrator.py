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
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trellis.execution.blackboard import Blackboard, InMemoryBlackboard
from trellis.execution.dag import ExecutionOptions, PipelineResult, execute_pipeline
from trellis.execution.template import ResolutionContext
from trellis.models.pipeline import Pipeline
from trellis.tools.registry import AsyncToolRegistry, build_default_registry

logger = logging.getLogger(__name__)


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
        logger.debug(
            "Orchestrator initialized (tenant_id=%r, tools=%d)",
            self.tenant_id,
            len(self.registry.registered_tools()),
        )

    def cancel(self) -> None:
        """Request cooperative cancellation before scheduling the next wave."""
        logger.info("Cancellation requested for tenant_id=%r", self.tenant_id)
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
        input_keys = sorted(list((inputs or pipeline.inputs or {}).keys()))
        session_keys = sorted(list((session or {}).keys()))
        logger.info(
            "Starting pipeline %r (tenant_id=%r)",
            pipeline.id,
            self.tenant_id,
        )
        logger.debug(
            "Pipeline %r context seeds: inputs_keys=%s, session_keys=%s",
            pipeline.id,
            input_keys,
            session_keys,
        )

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

        logger.debug(
            "Execution options: timeout=%s, retry_base_delay=%.2f, max_retry_delay=%.2f, fan_out_concurrency=%s, jitter=%.2f",
            getattr(options, "per_task_timeout", None),
            options.retry_base_delay,
            options.max_retry_delay,
            options.fan_out_concurrency,
            options.backoff_jitter,
        )

        events: List[Dict[str, Any]] = [] if collect_events else []
        event_sink = events if collect_events else None

        result: PipelineResult = await execute_pipeline(
            pipeline,
            self.registry,
            ctx,
            options=options,
            event_sink=event_sink,
        )

        logger.info(
            "Pipeline %r finished (waves=%d, tasks=%d)",
            pipeline.id,
            result.waves_executed,
            result.tasks_executed,
        )

        return RunResult(
            outputs=result.outputs,
            waves_executed=result.waves_executed,
            tasks_executed=result.tasks_executed,
            events=events,
        )
