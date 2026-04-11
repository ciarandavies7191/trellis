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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trellis.execution.blackboard import Blackboard, InMemoryBlackboard
from trellis.execution.dag import ExecutionOptions, PipelineResult, execute_pipeline
from trellis.execution.template import ResolutionContext
from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan, SubPipeline
from trellis.tools.registry import AsyncToolRegistry, build_default_registry
from trellis.validation.graph import plan_execution_waves

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

    async def run_plan(
        self,
        plan: Plan,
        plan_dir: Path,
        *,
        inputs: Dict[str, Any] | None = None,
        session: Dict[str, Any] | None = None,
        options: Optional[ExecutionOptions] = None,
        collect_events: bool = True,
        progress_cb: Callable[[str, int, int, int, int], None] | None = None,
    ) -> "PlanRunResult":
        """
        Execute all sub-pipelines in a Plan in topological order.

        Sub-pipelines within the same wave have no inter-dependencies and are
        executed sequentially (parallel wave execution is a future enhancement).

        Args:
            plan:        Validated Plan model.
            plan_dir:    Directory containing the sibling sub-pipeline YAMLs.
            inputs:      Override / supplement plan.inputs at call time.
            session:     Seed values for the session blackboard before execution.
            options:     Execution options forwarded to each sub-pipeline run.
            collect_events: Whether to collect task events.
            progress_cb: Optional callback called before each sub-pipeline:
                         (sub_pipeline_id, wave_idx, wave_total,
                          sp_idx_in_wave, wave_size).
        """
        # Merge call-time inputs on top of plan-declared defaults.
        plan_inputs: Dict[str, Any] = {**plan.inputs, **(inputs or {})}

        # Seed the shared blackboard with any caller-provided session values.
        if session:
            for k, v in session.items():
                self.blackboard.write(self.tenant_id, k, v)

        waves = plan_execution_waves(plan)
        total_waves = len(waves)

        sub_results: Dict[str, RunResult] = {}
        total_tasks_executed = 0
        all_events: List[Dict[str, Any]] = []

        logger.info(
            "Starting plan %r: %d sub-pipeline(s) across %d wave(s)",
            plan.id, len(plan.sub_pipelines), total_waves,
        )

        for wave_idx, wave in enumerate(waves):
            for sp_idx, sp in enumerate(wave):
                if progress_cb:
                    progress_cb(sp.id, wave_idx, total_waves, sp_idx, len(wave))

                # Locate the sub-pipeline YAML (sibling of the plan file).
                pipeline_path = plan_dir / f"{sp.id}.yaml"
                if not pipeline_path.exists():
                    raise FileNotFoundError(
                        f"Sub-pipeline file not found: {pipeline_path}. "
                        f"Expected a sibling YAML named '{sp.id}.yaml' next to the plan."
                    )

                pipeline = Pipeline.from_yaml(pipeline_path.read_text(encoding="utf-8"))

                # Forward only the plan input keys declared in sp.inputs,
                # overriding the pipeline YAML's own defaults for those keys.
                sp_inputs = {**pipeline.inputs}
                for key in sp.inputs:
                    if key in plan_inputs:
                        sp_inputs[key] = plan_inputs[key]

                # Expose the full accumulated blackboard as this pipeline's session.
                current_session = self.blackboard.get_all(self.tenant_id)

                logger.info(
                    "Plan %r — wave %d/%d — running sub-pipeline %r",
                    plan.id, wave_idx + 1, total_waves, sp.id,
                )

                result = await self.run_pipeline(
                    pipeline,
                    inputs=sp_inputs,
                    session=current_session,
                    options=options,
                    collect_events=collect_events,
                )

                sub_results[sp.id] = result
                total_tasks_executed += result.tasks_executed
                if collect_events:
                    all_events.extend(result.events or [])

        final_blackboard = self.blackboard.get_all(self.tenant_id)
        logger.info(
            "Plan %r complete: %d sub-pipeline(s), %d task(s), blackboard keys: %s",
            plan.id, len(sub_results), total_tasks_executed,
            sorted(final_blackboard.keys()),
        )

        return PlanRunResult(
            plan_id=plan.id,
            sub_results=sub_results,
            blackboard=final_blackboard,
            total_tasks_executed=total_tasks_executed,
            total_waves=total_waves,
            events=all_events,
        )


@dataclass
class PlanRunResult:
    """Structured result from an orchestrated plan run."""

    plan_id: str
    sub_results: Dict[str, RunResult]        # sub_pipeline_id → its RunResult
    blackboard: Dict[str, Any]               # full session blackboard after all sub-pipelines
    total_tasks_executed: int
    total_waves: int
    events: List[Dict[str, Any]] = field(default_factory=list)
