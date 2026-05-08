# Execution Backends & Run Queue

Trellis separates the **pipeline model** (what to run) from the **executor** (how to run it). The default path is a pure-asyncio local executor that runs pipelines in-process. An in-memory background run queue wraps this for the REST API's async endpoint. A Prefect adapter is on the roadmap.

---

## Local executor

The local executor (`trellis.execution.dag.execute_pipeline`) runs pipelines directly in the calling process using Python's `asyncio`. It is the only production executor today.

### How it works

1. `pipeline_execution_waves(pipeline)` — uses Kahn's algorithm to partition the task list into dependency layers. Each layer (wave) contains tasks with no unresolved upstream dependencies.
2. Wave execution — all tasks in a wave are launched concurrently via `asyncio.gather()`. The executor waits for the entire wave to settle before starting the next.
3. Template resolution — before each tool call, `resolve(inputs, ctx)` substitutes `{{expr}}` references against the live `ResolutionContext`, which holds outputs from all completed tasks.
4. Output publication — after a task succeeds, its output is written into the `ResolutionContext` so downstream tasks in later waves can read it.

```
Wave 1  ──┬── schema        (no deps)
           └── fetch         (no deps)
Wave 2  ──── ingest          (depends on: fetch)
Wave 3  ──── select_pages    (depends on: ingest)
Wave 4  ──── extract         (depends on: select_pages, schema)
Wave 5  ──── export_json     (depends on: extract)
```

### Orchestrator

`Orchestrator` is the high-level entry point. It builds the `ResolutionContext`, discovers tools via `build_default_registry()`, and delegates to `execute_pipeline`:

```python
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

pipeline = Pipeline.from_yaml_file("pipelines/sec_extraction.yaml")
orch = Orchestrator()
result = await orch.run_pipeline(
    pipeline,
    params={"ticker": "AAPL", "period_end": "2024-09-30"},
)
print(result.outputs)
print(f"{result.waves_executed} waves, {result.tasks_executed} tasks")
```

**Constructor parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `registry` | `AsyncToolRegistry` | `build_default_registry()` | Tool registry; pass a custom one to override or extend tools |
| `blackboard` | `Blackboard` | `InMemoryBlackboard()` | Persistence backend for the `store` tool and plan session |
| `tenant_id` | `str` | `"default"` | Tenant namespace for blackboard isolation |

**`run_pipeline` parameters:**

| Parameter | Type | Description |
|---|---|---|
| `pipeline` | `Pipeline` | Validated pipeline model |
| `params` | `dict` | Typed param values (see [Pipeline DSL — params](PIPELINE-DSL.md#params-block)) |
| `inputs` | `dict` | Legacy untyped inputs (`{{pipeline.inputs.key}}`) |
| `session` | `dict` | Pre-seeded session/blackboard values available as `{{session.key}}` |
| `options` | `ExecutionOptions` | Execution tuning knobs (see below) |
| `collect_events` | `bool` | Collect per-task start/finish/fail events in `result.events` |

**`RunResult` fields:**

| Field | Type | Description |
|---|---|---|
| `outputs` | `dict[str, Any]` | `task_id → output` for every completed task |
| `waves_executed` | `int` | Number of waves processed |
| `tasks_executed` | `int` | Total tool invocations (fan-out items counted individually) |
| `events` | `list[dict]` | Per-task events when `collect_events=True` |

---

## Execution options

`ExecutionOptions` is a dataclass that controls timeouts, retries, fan-out concurrency, and cancellation. Pass it to `run_pipeline` or `run_plan`:

```python
from trellis.execution.dag import ExecutionOptions

options = ExecutionOptions(
    per_task_timeout=60.0,        # seconds per tool invocation attempt
    retry_base_delay=1.0,         # first retry waits 1 s
    max_retry_delay=10.0,         # cap at 10 s
    backoff_jitter=0.2,           # ±20% random jitter on retry delay
    fan_out_concurrency=5,        # max parallel workers for parallel_over
)
result = await orch.run_pipeline(pipeline, params={...}, options=options)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `per_task_timeout` | `float \| None` | `None` | Max seconds per tool invocation attempt. Applies per attempt, not total. `None` = no timeout |
| `retry_base_delay` | `float` | `0.5` | Initial backoff delay in seconds before the first retry |
| `max_retry_delay` | `float` | `4.0` | Backoff ceiling — delays never exceed this |
| `backoff_jitter` | `float` | `0.0` | Fractional jitter applied to retry delay (0.2 = ±20%) |
| `fan_out_concurrency` | `int \| None` | `None` | Max simultaneous items in a `parallel_over` fan-out. `None` = unlimited |
| `cancel_event` | `asyncio.Event \| None` | `None` | Injected automatically by `Orchestrator.cancel()` — do not set manually |

**CLI equivalents:**

| `ExecutionOptions` field | CLI flag |
|---|---|
| `per_task_timeout` | `--timeout SECONDS` |
| `fan_out_concurrency` | `--concurrency N` |
| `backoff_jitter` | `--jitter FRACTION` |

---

## Cancellation

Call `orch.cancel()` to request cooperative cancellation. The cancel event is checked between waves — any wave that has already started runs to completion, and no further waves are scheduled:

```python
import asyncio
from trellis.execution.orchestrator import Orchestrator

orch = Orchestrator()
task = asyncio.create_task(orch.run_pipeline(pipeline, params={...}))

await asyncio.sleep(5)
orch.cancel()          # stop after the current wave finishes
result = await task
```

The REST API's `POST /pipelines/runs/{run_id}/cancel` endpoint calls `orch.cancel()` on the background run's orchestrator instance.

---

## In-memory background run queue

`InMemoryRunManager` (`trellis.execution.run_queue`) backs the REST API's async endpoint (`POST /pipelines/run_async`). It accepts a pipeline, submits it as a background `asyncio.Task`, and returns a `run_id` immediately. Callers poll `GET /pipelines/runs/{run_id}` for the result.

### Architecture

```
POST /pipelines/run_async
        │
        ▼
InMemoryRunManager.submit()  ─── creates asyncio.Task(worker)
        │                              │
        │                              ▼
        │                    Orchestrator.run_pipeline()
        │                              │
        │                    RunRecord.status: queued → running → succeeded/failed
        │
        ▼  (immediate response)
  { "run_id": "a3f7...", "status": "queued" }

GET /pipelines/runs/{run_id}
        │
        ▼
InMemoryRunManager.get()  ─── returns RunRecord
  { "status": "succeeded", "result": { "outputs": {...} } }
```

### Run states

| Status | Meaning |
|---|---|
| `queued` | Task created but execution has not started yet |
| `running` | `Orchestrator.run_pipeline()` is executing |
| `succeeded` | Pipeline completed — `result` is populated |
| `failed` | Execution raised an exception — `error` contains the message |
| `cancelled` | `cancel()` was called before or during execution |

### Limitations

- **In-process only** — the queue lives in the same Python process as the API server. Restarting the server clears all runs.
- **No persistence** — completed results are held in memory; there is no database or external store backing them.
- **No distributed workers** — all tasks execute in the same event loop. For CPU-heavy fan-outs or cross-machine distribution, use the Prefect adapter (see below).

### Using the queue directly in Python

```python
from trellis.execution.run_queue import run_manager
from trellis.models.pipeline import Pipeline

pipeline = Pipeline.from_yaml_file("pipelines/extract.yaml")
run_id = await run_manager.submit(pipeline, inputs={"ticker": "AAPL"})

import asyncio
while True:
    rec = await run_manager.get(run_id)
    if rec.status in ("succeeded", "failed", "cancelled"):
        break
    await asyncio.sleep(1)

print(rec.result)   # {"outputs": {...}, "waves_executed": 5, ...}
```

---

## Prefect adapter (roadmap)

`PrefectExecutor` (`trellis.execution.prefect_adapter`) is a skeleton that exposes the same interface as `InMemoryRunManager` — `submit`, `get_result`, `cancel` — but will map a Trellis pipeline onto a Prefect Flow and tasks for remote, distributed execution.

**Planned interface:**

```python
from trellis.execution.prefect_adapter import PrefectExecutor

executor = PrefectExecutor(
    work_pool="my-work-pool",
    deployment_name="trellis-pipeline",
)
run_id = await executor.submit(pipeline, inputs={"ticker": "AAPL"})
result = await executor.get_result(run_id)
```

**What it will provide:**

- Each pipeline wave becomes a Prefect task group, preserving the wave-level concurrency model
- Results and events stored in Prefect's backend — survives server restarts
- Cancelation via Prefect flow cancellation
- Work pool routing — direct pipeline runs to specific infrastructure (Kubernetes, ECS, local processes)

**Status:** `PrefectExecutor.submit()`, `get_result()`, and `cancel()` all raise `NotImplementedError`. The public interface is stabilized so callers can code against it today. Track the implementation in the project issue tracker.

---

## Choosing an execution path

| Scenario | Recommended path |
|---|---|
| Local development and testing | `Orchestrator.run_pipeline()` directly |
| REST API synchronous endpoint | `POST /pipelines/run` → `Orchestrator` in-request |
| REST API fire-and-forget with polling | `POST /pipelines/run_async` → `InMemoryRunManager` |
| Production with persistence and distribution | Prefect adapter (roadmap) |
| Multi-step plan with shared session state | `Orchestrator.run_plan()` |

---

## Next steps

- [Persistence & Multi-tenancy](operations-blackboard.md) — blackboard storage, tenant isolation, and the `store` tool
- [Configuration & Environment](operations-configuration.md) — environment variables and per-run model overrides
- [API reference](interfaces-api.md) — async run, polling, and cancel endpoints
