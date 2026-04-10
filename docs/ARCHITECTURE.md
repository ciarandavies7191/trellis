# Trellis Architecture

## Overview

Trellis is a modular runtime execution engine and fine-tuning pipeline for
**Pipeline DSL v1.4** — a YAML-based declarative language for expressing deterministic
agentic workflows as DAGs. The codebase is organized into a single `trellis/` core
library consumed by three thin entry points: REST API, CLI, and MCP server.

---

## Package Layout

```
trellis/                          ← Core library (all domain logic lives here)
├── models/                       ← Pydantic + dataclass data models
│   ├── pipeline.py               ← Pipeline, Task, KNOWN_TOOLS
│   ├── plan.py                   ← Plan, SubPipeline
│   ├── document.py               ← DocFormat, Page, DocumentHandle, PageList
│   └── handles.py                ← SchemaHandle, FieldDefinition, PeriodDescriptor, FIELD_NOT_FOUND
│
├── registry/                     ← Deploy-time name → object registries
│   ├── schema.py                 ← SchemaRegistry (name → SchemaHandle)
│   ├── functions.py              ← FunctionRegistry, RegisteredFunction
│   └── finance_functions.py      ← Built-in finance functions + build_finance_registry()
│
├── validation/                   ← Parse-time and contract validation
│   ├── graph.py                  ← Kahn topo-sort, cycle detection
│   └── contract.py               ← stores/reads/inputs contract validator
│
├── execution/                    ← Async runtime
│   ├── template.py               ← ResolutionContext, resolve*(), {{}} engine
│   ├── dag.py                    ← Wave executor (asyncio, retries, fan-out)
│   ├── orchestrator.py           ← Orchestrator (registry wiring, cancel, events)
│   ├── blackboard.py             ← Tenant-scoped session store
│   ├── run_queue.py              ← In-memory async run manager
│   └── prefect_adapter.py        ← Pluggable Prefect backend skeleton
│
├── tools/                        ← Tool abstraction and implementations
│   ├── base.py                   ← BaseTool, ToolInput, ToolOutput
│   ├── registry.py               ← AsyncToolRegistry, build_default_registry()
│   └── impls/
│       ├── document.py           ← ingest_document
│       ├── select.py             ← select
│       ├── extract.py            ← extract_from_texts, extract_from_tables, extract_chart
│       ├── extract_fields.py     ← extract_fields (schema-bound)
│       ├── load_schema.py        ← load_schema
│       ├── compute.py            ← compute (FunctionRegistry dispatch)
│       ├── llm.py                ← llm_job
│       ├── fetch.py              ← fetch_data
│       ├── search.py             ← search_web
│       ├── store.py              ← store
│       ├── export.py             ← export (schema-aware in v1.4)
│       └── mock.py               ← mock / test tools
│
trellis_api/                      ← FastAPI REST entry point (thin layer)
trellis_cli/                      ← Typer CLI entry point (thin layer)
trellis_mcp/                      ← MCP server entry point (thin layer)
data/                             ← Dataset generation pipeline
tests/                            ← Unit and integration tests
```

---

## Layers and Responsibilities

### 1. Models (`trellis/models/`)

Pure data — no I/O, no side effects. All models validate on construction.

| Module | Key types |
|---|---|
| `pipeline.py` | `Pipeline`, `Task`, `KNOWN_TOOLS` — DSL model with Pydantic v2 validators |
| `plan.py` | `Plan`, `SubPipeline` — two-level decomposition model |
| `document.py` | `DocumentHandle`, `Page`, `PageList` — document and page containers |
| `handles.py` | `SchemaHandle`, `FieldDefinition`, `PeriodDescriptor`, `FIELD_NOT_FOUND` — typed pipeline data objects |

`SchemaHandle` is to structured output what `DocumentHandle` is to document content:
a typed, first-class object that flows through the DAG as a task output.

### 2. Registries (`trellis/registry/`)

Deploy-time name → object mappings. Operators register entries at startup; the DSL
references names only.

| Registry | Maps | Used by |
|---|---|---|
| `SchemaRegistry` | name → `SchemaHandle` | `load_schema` tool |
| `FunctionRegistry` | name → `RegisteredFunction` | `compute` tool |

The trust boundary mirrors `fetch_data`'s source registry: the model can reference
registered names but cannot define or inject implementations.

`finance_functions.py` provides the default finance function set and
`build_finance_registry()` to construct an isolated registry for testing.

### 3. Validation (`trellis/validation/`)

Two-phase validation on every generated pipeline:

1. **Structural** — Pydantic models enforce field types, snake_case ids, known tool
   names, template syntax, and the `compute`→`function` key invariant.
2. **Graph** — `graph.py` runs Kahn's algorithm to detect cycles and partition tasks
   into parallel execution waves.
3. **Contract** — `contract.py` cross-checks `store` task keys against the plan's
   `stores` list, verifies all `{{session.*}}` references are in `reads`, and verifies
   all `{{pipeline.inputs.*}}` references exist in `inputs`.

### 4. Execution (`trellis/execution/`)

The async runtime that runs a validated pipeline.

```
Orchestrator.run_pipeline(pipeline, inputs, session)
    └──► ResolutionContext constructed (outputs, inputs, goal, session, item, tenant, blackboard)
          └──► pipeline_execution_waves(pipeline) → list[list[Task]]
                └──► per wave: asyncio.gather(*[_execute_task(t) for t in wave])
                      └──► resolve_inputs(task.inputs, ctx) — {{}} substitution
                            └──► tool_registry.invoke(task.tool, resolved_inputs)
                                  └──► if parallel_over: _execute_fan_out(task, ctx)
                                        └──► [blackboard.write(...) for store tasks]
```

Key design points:

- **Wave invariant**: a task in wave N only reads outputs from tasks in waves 0..N-1.
  The DAG executor never needs a lock on `ResolutionContext` slot writes.
- **Fan-out**: `parallel_over` resolves to a list at execution time (supporting
  runtime-computed lists from `compute` or `llm_job` outputs). Each item runs as an
  independent invocation; results are collected into an ordered list.
- **Retries**: exponential backoff with optional jitter, bounded by `max_retry_delay`.
- **Cancellation**: cooperative via `asyncio.Event` threaded through `ExecutionOptions`.
- **Sync tool wrapping**: `AsyncToolRegistry.invoke` wraps sync `execute()` in
  `asyncio.to_thread`; `FunctionRegistry.invoke` wraps sync functions in
  `loop.run_in_executor`.

### 5. Tools (`trellis/tools/`)

Tools are stateless callables registered in `AsyncToolRegistry`. The registry supports
both sync and async implementations, both `BaseTool` instances and bare callables.

`BaseTool` provides: `execute(**kwargs)`, `get_inputs()`, `get_output()`,
`validate_inputs()`. The `discover_impls()` method auto-registers all `BaseTool`
subclasses found in `trellis.tools.impls`.

**v1.4 new tools:**

| Tool | Class | Notes |
|---|---|---|
| `load_schema` | `LoadSchemaTool` | Pluggable `SchemaRegistry` injected at construction |
| `extract_fields` | `ExtractFieldsTool` | Pluggable LLM client; stubs to sentinel without one |
| `compute` | `ComputeTool` | Pluggable `FunctionRegistry` injected at construction |

**v1.4 extended tools:**

| Tool | Change |
|---|---|
| `export` | Optional `schema: SchemaHandle` input; conformance validation; populate mode |

---

## Data Flow

### Unstructured Document Pipeline

```
ingest_document(path) → DocumentHandle
    └──► select(document, prompt) → PageList
              ├──► extract_from_texts(document, prompt) → dict
              └──► extract_from_tables(document, selector) → list[TableResult]
```

### Schema-Guided Extraction Pipeline (Financial Spreading)

```
ingest_document(template_path) → DocumentHandle (template)
    └──► load_schema(source=template_handle) → SchemaHandle
              └──► extract_fields(document=pages, schema=schema, rules=manual) → {field: value}
                        └──► export(data=fields, schema=schema, format=xlsx) → artifact

compute(fiscal_period_logic, as_of_date, company) → list[PeriodDescriptor]
    └──► fetch_data(sec_edgar, parallel_over=periods) → list[filing]
```

### Two-Level Plan Execution

```
[PLAN] prompt → Plan YAML
    └──► plan_execution_waves(plan) → list[list[SubPipeline]]
              └──► per sub-pipeline:
                    [PIPELINE] prompt → Pipeline YAML
                        └──► validate (structural → graph → contract)
                              └──► execute_pipeline(pipeline, inputs, blackboard)
                                    └──► store tasks → blackboard.write(tenant, key, value)
```

---

## Extension Points

### Adding a New Tool

1. Create `trellis/tools/impls/my_tool.py` with a `BaseTool` subclass.
2. Export it from `trellis/tools/impls/__init__.py`.
3. Add the tool name to `KNOWN_TOOLS` in `trellis/models/pipeline.py`.
4. Add tests under `tests/unit/tools/`.

The tool will be auto-discovered and registered by `AsyncToolRegistry.discover_impls()`.

### Registering a Compute Function

```python
from trellis.registry.functions import FunctionRegistry, RegisteredFunction

registry = FunctionRegistry()
registry.register(RegisteredFunction(
    name="my_function",
    fn=my_callable,               # sync or async
    input_schema={"x": "str"},
    output_schema="str",
    description="What it does",
))
```

Pass the registry to `ComputeTool(function_registry=registry)` at startup.

### Registering a Named Schema

```python
from trellis.registry.schema import SchemaRegistry
from trellis.models.handles import SchemaHandle, FieldDefinition

schema_registry = SchemaRegistry()
schema_registry.register("my_schema", SchemaHandle(
    fields=[FieldDefinition(name="revenue", type_hint="number")],
    source="my_schema",
))
```

Pass the registry to `LoadSchemaTool(schema_registry=schema_registry)` at startup.

### Swapping the Blackboard Backend

Implement the `Blackboard` abstract interface (`trellis/execution/blackboard.py`) and
inject it into the `Orchestrator`. The `InMemoryBlackboard` is the default for local
runs; Redis or Prefect Blocks are the intended production replacements.

### Pluggable Execution Backend (Prefect)

`execution/prefect_adapter.py` contains a skeleton that maps pipeline waves to a
Prefect Flow/Task graph, propagates `tenant_id`, and uses a durable blackboard.

---

## Entry Points

All three entry points import from `trellis/`. They contain no domain logic.

| Entry point | Package | Start command |
|---|---|---|
| REST API | `trellis_api/` | `python -m trellis_api.main` (localhost:8000) |
| CLI | `trellis_cli/` | `trellis validate <path>` / `trellis run <path>` |
| MCP server | `trellis_mcp/` | MCP protocol via `trellis_mcp/server.py` |

---

## Test Layout

```
tests/
├── conftest.py
├── unit/
│   ├── models/
│   │   ├── test_pipeline.py         ← Pipeline/Task model validators
│   │   ├── test_plan.py             ← Plan/SubPipeline validators
│   │   └── test_handles.py          ← SchemaHandle, FieldDefinition, PeriodDescriptor
│   ├── validation/
│   │   ├── test_graph.py            ← Cycle detection, wave partitioning
│   │   └── test_contract.py         ← stores/reads/inputs contract
│   ├── execution/
│   │   ├── test_dag.py              ← Wave executor, retries, fan-out
│   │   ├── test_template.py         ← Template resolution
│   │   └── test_orchestrator.py     ← Orchestrator integration
│   └── tools/
│       ├── test_ingest_document.py
│       ├── test_extract_from_texts.py
│       ├── test_llm_tool.py
│       ├── test_fetch_data.py
│       ├── test_search_web.py
│       ├── test_registry.py
│       ├── test_schema_registry.py  ← SchemaRegistry
│       ├── test_function_registry.py ← FunctionRegistry sync/async dispatch
│       ├── test_finance_functions.py ← All 5 built-in finance functions
│       ├── test_compute_tool.py     ← ComputeTool integration
│       ├── test_load_schema_tool.py ← LoadSchemaTool against all source types
│       └── test_extract_fields_tool.py ← ExtractFieldsTool with/without LLM
└── integration/
    └── pipelines/                   ← Full pipeline execution tests
```
