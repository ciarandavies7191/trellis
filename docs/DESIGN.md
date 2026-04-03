# Trellis — Design Reference

**DSL Version:** Pipeline DSL v1.3  
**Runtime Package:** `trellis`  
**Status:** Active development  
**Audience:** IDE coding agents, contributors

---

## 1. Purpose

Trellis is a runtime execution engine and fine-tuning pipeline for **Pipeline DSL v1.3** — a YAML-based declarative language for expressing deterministic agentic workflows as directed acyclic graphs (DAGs).

The system has two primary goals:

1. **Fine-tune a small language model** to generate valid DSL from natural-language prompts, analogous to text-to-SQL but targeting a YAML DAG schema.
2. **Execute generated pipelines deterministically**, resolving dependencies, honouring blackboard contracts, and dispatching tool calls.

---

## 2. Non-Goals

- No general-purpose LLM agent loop; the DAG is fully pre-planned and deterministic.
- No conditionals, loops, or branching in the DSL itself — all control-flow complexity lives inside `llm_job` tool calls.
- No implicit blackboard writes — every persistence operation requires an explicit `store` task.
- No heavyweight agent frameworks — the runtime uses thin, domain-specific abstractions.

---

## 3. Package Structure

```
trellis/
├── __init__.py
├── exceptions.py                    # TrellisError, CycleError, ContractError
│
├── models/
│   ├── __init__.py
│   ├── pipeline.py                  # Pipeline, Task, KNOWN_TOOLS, template ref utils
│   ├── plan.py                      # Plan, SubPipeline
│   └── document.py                  # DocFormat, Page, DocumentHandle, PageList
│
├── validation/
│   ├── __init__.py
│   ├── graph.py                     # Kahn topo-sort, cycle detection (pipeline/plan)
│   └── contract.py                  # stores/reads/inputs contract validation
│
├── execution/
│   ├── __init__.py
│   ├── template.py                  # ResolutionContext, resolve*, {{}} template engine
│   ├── dag.py                       # Async DAG executor (waves, retries, fan-out)
│   ├── orchestrator.py              # Orchestrator (registry, context, cancel, events)
│   ├── blackboard.py                # Tenant-scoped Blackboard + InMemoryBlackboard impl
│   ├── run_queue.py                 # In-memory async run manager (submit/get/cancel)
│   └── prefect_adapter.py           # Prefect executor skeleton (pluggable backend)
│
├── tools/
│   ├── __init__.py
│   ├── base.py                      # BaseTool, ToolInput, ToolOutput
│   ├── registry.py                  # AsyncToolRegistry, discovery, build_default_registry
│   └── impls/                       # Built-in tool implementations
│       ├── document.py              # load_document → DocumentHandle/PageList
│       ├── extract.py               # extract_text (litellm OCR/selector) + extract_table (stub)
│       ├── llm.py                   # llm_job (provider-agnostic)
│       ├── mock.py                  # mock tool (dev/testing)
│       └── store.py                 # store (echo; persistence handled by executor)
│
├── trellis_api/                     # FastAPI server
├── trellis_cli/                     # Typer CLI (validate/run)
├── trellis_mcp/                     # MCP server
└── tests/                           # Unit & integration tests
```

---

## 4. Architecture

### 4.1 Two-Level Generation Architecture

```
[PLAN]    → Plan YAML       (decompose complex goal into sub-pipelines)
[PIPELINE] → Pipeline YAML  (generate a single sub-pipeline's DAG)
```

Generation and execution loop:

```
[PLAN] <goal>
  └──► plan_execution_waves(plan)
        └──► per sub-pipeline in wave order:
              [PIPELINE] prompt → Pipeline YAML
                └──► validate (structural → graph → contract)
                      └──► Orchestrator.run_pipeline(...)
                            └──► execute_pipeline(...) (waves, retries, fan-out)
                                  └──► store outputs to blackboard (session)
```

### 4.2 Validation Stack

- Structural validation via Pydantic models (`models/`)
- Graph validation via `validation/graph.py` (cycles, waves)
- Contract validation via `validation/contract.py` (stores/reads/inputs)

### 4.3 Runtime Execution Model

- `execution.template.ResolutionContext` holds execution state: task outputs, pipeline inputs, goal, session, current `{{item}}`, and now also `tenant_id` and a `blackboard` handle.
- `execution.dag.execute_pipeline(...)` runs tasks wave-by-wave (async), resolves templates, fans out over `parallel_over`, applies retries with backoff and optional jitter, supports per-task timeout.
- `store` persistence: when a `store` task completes, the executor writes the value to the tenant-scoped blackboard (`blackboard.write(tenant_id, key, value, append=...)`) and mirrors it into the in-run `session` so downstream tasks can immediately read `{{session.key}}` in the same run.
- `execution.orchestrator.Orchestrator` constructs `ResolutionContext` (injects `tenant_id` and `blackboard`), builds the default async tool registry, executes, collects stats/events, and exposes `cancel()` for cooperative cancellation.

### 4.4 Background Execution (Queue Fallback)

- For production-style async runs, an in-memory queue is provided: `execution/run_queue.py` with `submit/get/cancel`.
- The API exposes:
  - `POST /pipelines/run_async` → `{ run_id, status: "queued" }`
  - `GET /pipelines/runs/{id}` → `{ status, result?, error?, events? }` (JSON-sanitized)
  - `POST /pipelines/runs/{id}/cancel`
- This queue is a dev/local fallback and can be replaced by Prefect or other backends without changing callers.

### 4.5 Pluggable Execution Backends (Prefect)

- `execution/prefect_adapter.py` contains a skeleton for a Prefect-backed executor. It will map waves/tasks to a Prefect Flow/Tasks graph, propagate `tenant_id`, enforce retries/timeouts, and use a durable blackboard (e.g., Redis or Prefect Blocks).

---

## 5. Data Models

All core models use Pydantic v2; document models use dataclasses for efficiency and tool ergonomics.

### 5.1 Task (`trellis.models.pipeline`)
- Implicit dependencies from `{{task_id.output}}` in `inputs`/`parallel_over`
- Optional `await` (escape hatch) as `await_`
- `retry`, `parallel_over` supported

### 5.2 Pipeline (`trellis.models.pipeline`)
- `id`, `goal`, `inputs`, `tasks`
- Helpers: `task_map()`, `store_keys()`, `from_yaml()`

### 5.3 SubPipeline (`trellis.models.plan`)
- `id`, `goal`, `reads`, `stores`, `inputs`

### 5.4 Plan (`trellis.models.plan`)
- `id`, `goal`, `inputs`, `sub_pipelines`

### 5.5 ContractViolation (`trellis.validation.contract`)
- Captures violation kind, key, task, and message

### 5.6 Document Model (`trellis.models.document`)
- `DocFormat` enum: `PDF`, `XLSX`, `CSV`, `DOCX`, `TEXT`, `IMAGE`, `UNKNOWN`
- `Page` dataclass: number, text, image_bytes/mime (for OCR), is_scanned, sheet_name, metadata
- `DocumentHandle` dataclass: source, format, pages[], page_count, is_scanned, source_url, metadata
- `PageList` dataclass: reduced view produced by `select` (subset of pages with provenance)
- `DocumentInput` type alias: `DocumentHandle | PageList | list[DocumentHandle] | str`

---

## 6. Exceptions

- `TrellisError`, `CycleError`, `ContractError` in `trellis.exceptions`

---

## 7. Template System (`trellis.execution.template`)

- `ResolutionContext`: task_outputs, pipeline_inputs, pipeline_goal, session, item, tenant_id, blackboard
- `resolve(value, ctx)`: recursively resolves `{{...}}` in str/list/dict; whole-string templates return native types
- `resolve_inputs(inputs, ctx)`: convenience wrapper for dicts
- `resolve_parallel_over(expr, ctx)`: ensures a list (errors on strings/non-iterables)
- Supported references: `{{task_id.output[.field...]}}`, `{{pipeline.inputs.key}}`, `{{pipeline.goal}}`, `{{session.key}}`, `{{item}}`

---

## 8. Tooling

### 8.1 Base Classes (`trellis.tools.base`)
- `BaseTool` abstract class with `execute(**kwargs) -> Any`
- `ToolInput`/`ToolOutput` dataclasses for optional metadata

### 8.2 Registry (`trellis.tools.registry`)
- `AsyncToolRegistry`: register callables or `BaseTool` instances; invokes sync/async implementations uniformly (wraps sync in thread pool)
- Discovery via `AsyncToolRegistry.discover_impls()`; convenience `build_default_registry()` auto-registers tools in `tools.impls`
- `registered_tools()`, `invoke(name, inputs)`

### 8.3 Built-in Tools (`trellis.tools.impls`)
- `document.load_document`: emits `DocumentHandle` (or list) from path/URL; PDFs parsed via PyPDF2; images emit `Page` with `image_bytes` for OCR
- `extract.extract_text`: LLM-enhanced extraction; OCR via litellm vision models when needed; optional selector; returns a dataclass with `__str__` → combined text
- `extract.extract_table`: stub implementation (extensible)
- `llm.llm_job`: provider-agnostic LLM calls
- `store.store`: echo tool; persistence handled by executor’s blackboard integration
- `mock.mock`: test helper tool

---

## 9. CLI & API

- CLI (`trellis_cli/main.py`): `trellis validate PATH`; `trellis run PATH [--inputs JSON] [--session JSON] [--timeout SECONDS] [--concurrency N] [--jitter FRACTION] [--json]`
- API (`trellis_api/main.py`): FastAPI app with routers for pipelines/plans
  - Sync: `POST /pipelines/run`
  - Async (queue fallback):
    - `POST /pipelines/run_async` (returns `run_id`)
    - `GET /pipelines/runs/{run_id}` (status/result/events)
    - `POST /pipelines/runs/{run_id}/cancel`
- MCP (`trellis_mcp/server.py`): exposes tools to MCP clients

---

## 10. Import Paths Reference

```python
# Models
from trellis.models.pipeline import Pipeline, Task, KNOWN_TOOLS, extract_template_refs
from trellis.models.plan import Plan, SubPipeline
from trellis.models.document import DocFormat, Page, DocumentHandle, PageList

# Validation
from trellis.validation.graph import pipeline_execution_waves, plan_execution_waves, find_cycle
from trellis.validation.contract import validate_contract, assert_contract, ContractViolation, ViolationKind

# Execution
from trellis.execution.template import ResolutionContext, resolve, resolve_inputs, resolve_parallel_over
from trellis.execution.dag import execute_pipeline, ExecutionOptions, PipelineResult
from trellis.execution.orchestrator import Orchestrator
from trellis.execution.blackboard import Blackboard, InMemoryBlackboard
from trellis.execution.run_queue import InMemoryRunManager

# Tools
from trellis.tools.base import BaseTool
from trellis.tools.registry import AsyncToolRegistry, build_default_registry
```

---

## 11. Invariants

1. All task ids within a `Pipeline` are unique; all sub-pipeline ids within a `Plan` are unique.
2. Every `{{task_id.*}}` reference resolves to a task that exists in that pipeline.
3. Every id in a task's `await_` list refers to a task that exists in the pipeline.
4. `parallel_over`, if set, contains at least one `{{...}}` expression.
5. `Task.tool` is always a member of `KNOWN_TOOLS`.
6. `Task.id` and `Pipeline.id` always satisfy snake_case constraints.
7. Contract invariants: declared stores are written exactly once; `{{session.*}}` and `{{pipeline.inputs.*}}` references resolve to declared keys.
8. Tenant invariants: persisted blackboard reads/writes are isolated by `tenant_id`.

---

## 12. Notes on Dataset & Prompts

- Dataset scripts live under `data/` (not `dataset/`).
- Prompts and archetypes used for generation live under `data/prompts/` and `data/archetypes/`.

