"""
trellis.execution.dag — Async DAG executor for Pipeline documents.

Drives wave-by-wave execution of a validated Pipeline, resolving templates,
fanning out parallel_over tasks, handling retries, and collecting outputs
into a ResolutionContext.

Public API
----------

    execute_pipeline(
        pipeline:  Pipeline,
        registry:  AsyncToolRegistry,
        context:   ResolutionContext,
        *,
        options:   ExecutionOptions | None = None,
    ) -> PipelineResult

    class AsyncToolRegistry
        register(name, fn)          # fn may be sync or async
        invoke(name, inputs) → Any  # async; wraps sync fns in executor

    @dataclass ExecutionOptions
        retry_base_delay: float     # default 0.5s
        max_retry_delay:  float     # default 4.0s
        fan_out_concurrency: int | None  # None = unlimited

    @dataclass PipelineResult
        outputs: dict[str, Any]     # task_id → output
        waves_executed: int
        tasks_executed: int         # counts fan-out instances individually

    class TaskError(TrellisError)
        task_id, cause, attempt

Design notes
------------

Wave-by-wave execution
    pipeline_execution_waves() returns tasks grouped into parallel batches.
    All tasks in a wave launch concurrently via asyncio.gather(). The wave
    invariant guarantees no task in wave N references an output produced in
    wave N — only outputs from waves 0..N-1 — so shared-context mutation
    during a wave is safe.

await_ barriers
    Task.upstream_task_ids() already includes explicit await_ ids, so
    pipeline_execution_waves() places awaiting tasks in the correct later
    wave automatically. No special executor logic is needed.

Fan-out
    When task.parallel_over is set, the expression is resolved to a list,
    then one coroutine is spawned per item. Each coroutine runs with a
    derived ResolutionContext where {{item}} is bound to its element.
    Results are gathered in original order and stored as a list output.

Sync tools
    ToolRegistry wraps sync callables with loop.run_in_executor() so they
    don't block the event loop. All tool invocations look async to the
    executor regardless of implementation.

Retry
    Retries use exponential backoff (retry_base_delay * 2^attempt), capped
    at max_retry_delay. TaskError is raised after all attempts are exhausted,
    carrying the task_id, original exception, and attempt count.

Assumptions about template.py
    This module depends on the following public API from
    trellis.execution.template:

        resolve(value: Any, context: ResolutionContext) -> Any
            Recursively resolve {{...}} templates in value. Returns a new
            value with all templates substituted. Non-string values
            (lists, dicts) are walked recursively. Scalars with no templates
            are returned unchanged.

        class ResolutionContext
            Holds the live resolution state for one pipeline execution.
            Constructed externally (by the orchestrator) with pipeline inputs
            and available session keys before being passed to execute_pipeline.

            .with_item(item: Any) -> ResolutionContext
                Return a shallow copy of this context with {{item}} bound to
                `item`. Used to create per-element contexts for fan-out tasks.
                The original context is NOT modified.

            .set_task_output(task_id: str, output: Any) -> None
                Record a completed task's output. This mutates the context
                in-place. Called by the executor after each task completes.
                Safe to call concurrently for different task_ids within the
                same wave because the wave invariant ensures no reader-writer
                conflict: tasks in wave N only read outputs from waves 0..N-1.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from trellis.exceptions import TrellisError
from trellis.models.pipeline import Pipeline, Task
from trellis.validation.graph import pipeline_execution_waves
from trellis.execution.template import ResolutionContext, resolve
from trellis.tools.registry import AsyncToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TaskError(TrellisError):
    """
    Raised when a task fails after all retry attempts are exhausted.

    Attributes:
        task_id:  The id of the task that failed.
        cause:    The original exception from the last attempt.
        attempt:  The 1-based attempt number on which the final failure occurred.
    """

    def __init__(self, task_id: str, cause: BaseException, attempt: int) -> None:
        self.task_id = task_id
        self.cause = cause
        self.attempt = attempt
        super().__init__(
            f"Task {task_id!r} failed on attempt {attempt}: "
            f"{type(cause).__name__}: {cause}"
        )


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """
    Registry mapping tool names to sync or async callables.

    Sync callables are wrapped with loop.run_in_executor() so they don't
    block the event loop during execution.

    Usage::

        registry = ToolRegistry()
        registry.register("llm_job", my_async_llm_fn)
        registry.register("fetch_data", my_sync_fetch_fn)

        output = await registry.invoke("llm_job", {"prompt": "...", "data": ...})
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        """
        Register a tool implementation.

        Args:
            name: Tool name as used in pipeline YAML (must match KNOWN_TOOLS).
            fn:   Callable accepting keyword arguments. May be sync or async.
        """
        self._tools[name] = fn

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def registered_tools(self) -> list[str]:
        """Return a sorted list of registered tool names."""
        return sorted(self._tools)

    async def invoke(self, name: str, inputs: dict[str, Any]) -> Any:
        """
        Invoke a registered tool with resolved inputs.

        Sync tools are dispatched to a thread-pool executor. Async tools are
        awaited directly.

        Args:
            name:   Tool name.
            inputs: Fully resolved keyword arguments for the tool.

        Returns:
            The tool's output value (any type).

        Raises:
            KeyError: if the tool is not registered.
        """
        if name not in self._tools:
            raise KeyError(
                f"Tool {name!r} is not registered. "
                f"Registered tools: {self.registered_tools()}"
            )
        fn = self._tools[name]
        if asyncio.iscoroutinefunction(fn):
            return await fn(**inputs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(**inputs))


# ---------------------------------------------------------------------------
# Execution options
# ---------------------------------------------------------------------------


@dataclass
class ExecutionOptions:
    """
    Tuneable knobs for the DAG executor.

    All fields have sensible defaults and may be overridden per pipeline run.
    """

    retry_base_delay: float = 0.5
    """
    Initial wait in seconds before the first retry.
    Subsequent delays are doubled up to max_retry_delay.
    """

    max_retry_delay: float = 4.0
    """Maximum wait between retries (exponential backoff ceiling)."""

    fan_out_concurrency: int | None = None
    """
    Maximum simultaneous task instances within a single parallel_over fan-out.
    None means unlimited — all items are launched concurrently.
    Set to a small integer (e.g. 5) to throttle API-bound fan-outs.
    """

    per_task_timeout: float | None = None
    """Maximum seconds allowed per tool invocation attempt. None disables timeout."""

    backoff_jitter: float = 0.0
    """Fractional jitter added to retry delay (e.g., 0.2 = ±20%)."""

    cancel_event: asyncio.Event | None = None
    """When set, stops scheduling further waves; in-flight tasks complete."""


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """
    Summary of a completed pipeline execution.

    Attributes:
        outputs:        Mapping of task_id → output for every completed task.
                        For fan-out tasks this is the collected list of
                        per-item outputs, in original item order.
        waves_executed: Number of execution waves processed.
        tasks_executed: Total tool invocations, counting fan-out items
                        individually (so a parallel_over over 4 items = 4).
    """

    outputs: dict[str, Any] = field(default_factory=dict)
    waves_executed: int = 0
    tasks_executed: int = 0


# ---------------------------------------------------------------------------
# Internal: single-attempt tool call
# ---------------------------------------------------------------------------


async def _invoke_once(
    task: Task,
    resolved_inputs: dict[str, Any],
    registry: AsyncToolRegistry,
) -> Any:
    """Invoke a tool exactly once. No retry logic. Pure delegation."""
    return await registry.invoke(task.tool, resolved_inputs)


# ---------------------------------------------------------------------------
# Internal: retry wrapper
# ---------------------------------------------------------------------------


async def _invoke_with_retry(
    task: Task,
    resolved_inputs: dict[str, Any],
    registry: AsyncToolRegistry,
    options: ExecutionOptions,
) -> Any:
    """
    Invoke a tool, retrying up to task.retry additional times on failure.

    Delay between attempts uses exponential backoff starting at
    options.retry_base_delay, doubling each attempt, capped at
    options.max_retry_delay.

    Args:
        task:            The Task being executed.
        resolved_inputs: Fully resolved input dict (templates already substituted).
        registry:        Tool registry.
        options:         Execution options controlling retry timing.

    Raises:
        TaskError: if all attempts are exhausted.
    """
    max_attempts = task.retry + 1  # retry=0 → 1 attempt, retry=2 → 3 attempts
    delay = options.retry_base_delay

    for attempt in range(1, max_attempts + 1):
        try:
            coro = _invoke_once(task, resolved_inputs, registry)
            if options.per_task_timeout is not None:
                return await asyncio.wait_for(coro, timeout=options.per_task_timeout)
            return await coro
        except Exception as exc:  # noqa: BLE001
            if attempt == max_attempts:
                raise TaskError(task.id, exc, attempt) from exc
            logger.warning(
                "Task %r failed on attempt %d/%d — retrying in %.2fs. Error: %s: %s",
                task.id,
                attempt,
                max_attempts,
                delay,
                type(exc).__name__,
                exc,
            )
            # Apply jitter to delay
            if options.backoff_jitter:
                jitter = 1.0 + random.uniform(-options.backoff_jitter, options.backoff_jitter)
                sleep_for = min(delay * jitter, options.max_retry_delay)
            else:
                sleep_for = delay
            await asyncio.sleep(sleep_for)
            delay = min(delay * 2.0, options.max_retry_delay)

    # Unreachable — loop always raises on last attempt — but satisfies type checker.
    raise AssertionError("retry loop exited without result or exception")  # pragma: no cover


# ---------------------------------------------------------------------------
# Internal: fan-out execution
# ---------------------------------------------------------------------------


async def _execute_fan_out(
    task: Task,
    context: ResolutionContext,
    registry: AsyncToolRegistry,
    options: ExecutionOptions,
    result: PipelineResult,
) -> list[Any]:
    """
    Resolve parallel_over, fan out across items, gather results.

    Each item gets its own derived context with {{item}} bound to that
    element. All item-level coroutines run concurrently (subject to
    options.fan_out_concurrency). Results are returned in original item
    order via asyncio.gather().

    Args:
        task:    A Task with parallel_over set.
        context: Current resolution context (not mutated; per-item copies used).
        registry, options, result: passed through to _invoke_with_retry.

    Returns:
        Ordered list of per-item outputs.
    """
    # Resolve the parallel_over expression to obtain the item list.
    items_raw = resolve(task.parallel_over, context)
    items: list[Any] = list(items_raw) if not isinstance(items_raw, list) else items_raw

    if not items:
        logger.debug("Task %r parallel_over resolved to empty list — skipping.", task.id)
        return []

    semaphore: asyncio.Semaphore | None = (
        asyncio.Semaphore(options.fan_out_concurrency)
        if options.fan_out_concurrency is not None
        else None
    )

    async def run_item(item: Any) -> Any:
        # Bind {{item}} in a derived context; original is not modified.
        item_context = context.with_item(item)
        resolved_inputs = {
            key: resolve(value, item_context)
            for key, value in task.inputs.items()
        }
        if semaphore is not None:
            async with semaphore:
                output = await _invoke_with_retry(task, resolved_inputs, registry, options)
        else:
            output = await _invoke_with_retry(task, resolved_inputs, registry, options)
        # Special-case: persist to tenant blackboard for `store` tool
        if task.tool == "store":
            key = resolved_inputs.get("key")
            value = resolved_inputs.get("value")
            append = bool(resolved_inputs.get("append", False))
            if isinstance(key, str):
                try:
                    item_context.blackboard.write(item_context.tenant_id, key, value, append=append)
                    # Make immediately visible within this pipeline execution
                    item_context.session[key] = value if not append else item_context.session.get(key, []) + [value]
                except Exception:
                    pass
        result.tasks_executed += 1
        return output

    # Preserve order: asyncio.gather maintains result ordering.
    return list(await asyncio.gather(*[run_item(item) for item in items]))


# ---------------------------------------------------------------------------
# Internal: single-task dispatch (fan-out vs. normal)
# ---------------------------------------------------------------------------


async def _execute_task(
    task: Task,
    context: ResolutionContext,
    registry: AsyncToolRegistry,
    options: ExecutionOptions,
    result: PipelineResult,
    event_sink: Any | None = None,
) -> None:
    """
    Execute one task, update the shared context and result in-place.
    """
    start = time.perf_counter()

    def _emit(event: str, **data: Any) -> None:
        if event_sink is None:
            return
        # Support simple list collector or object with handler methods
        if hasattr(event_sink, event):
            try:
                getattr(event_sink, event)(**data)
            except Exception:  # best-effort
                pass
        elif hasattr(event_sink, "append"):
            try:
                event_sink.append({"type": event, **data})  # type: ignore[arg-type]
            except Exception:
                pass

    _emit("on_task_started", task_id=task.id, tool=task.tool)

    logger.debug(
        "Starting task %r (tool=%s, parallel_over=%s, retry=%d)",
        task.id,
        task.tool,
        task.parallel_over,
        task.retry,
    )

    try:
        if task.parallel_over is not None:
            output = await _execute_fan_out(task, context, registry, options, result)
        else:
            resolved_inputs = {key: resolve(value, context) for key, value in task.inputs.items()}
            output = await _invoke_with_retry(task, resolved_inputs, registry, options)
            # Special-case: persist to tenant blackboard for `store` tool
            if task.tool == "store":
                key = resolved_inputs.get("key")
                value = resolved_inputs.get("value")
                append = bool(resolved_inputs.get("append", False))
                if isinstance(key, str):
                    try:
                        context.blackboard.write(context.tenant_id, key, value, append=append)
                        context.session[key] = value if not append else context.session.get(key, []) + [value]
                    except Exception:
                        pass
            result.tasks_executed += 1
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000.0
        _emit("on_task_failed", task_id=task.id, tool=task.tool, error=str(exc), duration_ms=duration_ms)
        raise

    # Publish this task's output so downstream tasks can reference it.
    context.set_task_output(task.id, output)
    result.outputs[task.id] = output

    duration_ms = (time.perf_counter() - start) * 1000.0
    _emit("on_task_finished", task_id=task.id, tool=task.tool, duration_ms=duration_ms)

    logger.debug("Task %r completed (tool=%s).", task.id, task.tool)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def execute_pipeline(
    pipeline: Pipeline,
    registry: AsyncToolRegistry,
    context: ResolutionContext,
    *,
    options: ExecutionOptions | None = None,
    event_sink: Any | None = None,
) -> PipelineResult:
    """
    Execute a validated Pipeline wave by wave.

    Resolves templates, fans out parallel_over tasks, retries on transient
    failures, and collects all task outputs into the returned PipelineResult.
    The ResolutionContext is updated in-place as tasks complete, making
    outputs immediately available to downstream tasks in later waves.

    Wave semantics
    ~~~~~~~~~~~~~~
    pipeline_execution_waves() partitions the task graph into dependency
    layers. All tasks in a wave are launched concurrently with
    asyncio.gather(). The executor waits for every task in wave N to
    complete before starting wave N+1, so downstream tasks always see
    fully-settled upstream outputs.

    Error behavior
    ~~~~~~~~~~~~~~~
    If any task in a wave raises (after retries), the exception propagates
    out of asyncio.gather() immediately. Other tasks in the same wave that
    have already started will run to completion (asyncio.gather default);
    tasks in subsequent waves are not started. The caller is responsible for
    cleanup or partial-result handling.

    Args:
        pipeline: A structurally valid, contract-checked Pipeline instance.
        registry: AsyncToolRegistry containing implementations for all tools the
                  pipeline references.
        context:  ResolutionContext seeded with pipeline.inputs and any
                  session keys listed in the pipeline's sub-pipeline reads
                  declaration.
        options:  Optional execution tuning. Defaults are used if None.

    Returns:
        PipelineResult with all task outputs and execution statistics.

    Raises:
        TaskError:  if any task exhausts its retry budget.
        KeyError:   if a task references a tool not present in the registry.
        CycleError: if pipeline_execution_waves() detects a cycle (should be
                    impossible for a pipeline that passed graph validation,
                    but included for completeness).

    Example::

        context = ResolutionContext(
            pipeline_inputs={"companies": ["AAPL", "GOOG"], "year": 2025},
            session={"company_financials": financials_data},
        )
        result = await execute_pipeline(pipeline, registry, context)
        final_report = result.outputs["produce_report"]
    """
    options = options or ExecutionOptions()
    result = PipelineResult()

    waves = pipeline_execution_waves(pipeline)
    total_waves = len(waves)

    logger.info(
        "Executing pipeline %r: %d task(s) across %d wave(s).",
        pipeline.id,
        sum(len(w) for w in waves),
        total_waves,
    )

    for wave_idx, wave in enumerate(waves):
        if options.cancel_event is not None and options.cancel_event.is_set():
            logger.info("Cancellation requested — stopping before wave %d", wave_idx + 1)
            break
        logger.debug(
            "Pipeline %r — wave %d/%d: [%s]",
            pipeline.id,
            wave_idx + 1,
            total_waves,
            ", ".join(t.id for t in wave),
        )

        # All tasks in the wave run concurrently. asyncio.gather preserves
        # insertion order for results, but we don't use them directly here —
        # each coroutine mutates `result` and `context` directly.
        await asyncio.gather(*[
            _execute_task(task, context, registry, options, result, event_sink)
            for task in wave
        ])

        result.waves_executed += 1

    logger.info(
        "Pipeline %r finished: %d task invocation(s) across %d wave(s).",
        pipeline.id,
        result.tasks_executed,
        result.waves_executed,
    )

    return result