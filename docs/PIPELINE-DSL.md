# Pipeline DSL Reference

A **pipeline** is a flat, ordered list of tasks that form a directed acyclic graph (DAG). Dependencies are inferred automatically from template references — there is no `depends_on` field. The executor groups tasks into parallel **waves** and runs each wave concurrently before advancing.

---

## Document structure

```yaml
pipeline:
  id: <snake_case_id>          # required
  goal: <human-readable goal>  # required
  params:                      # optional: typed invocation-time parameters
    <name>:
      type: string | integer | number | boolean | list | object
      description: <text>
      default: <value>         # omit to make the param required
  inputs:                      # optional: legacy untyped input block
    <key>: <value>
  tasks:                       # required: at least one task
    - id: <snake_case_id>
      tool: <tool_name>
      inputs:
        <key>: <value or template>
      parallel_over: "{{<expr>}}"   # optional fan-out
      retry: <int>                  # optional, default 0
      await:                        # optional explicit barrier
        - <task_id>
```

---

## Pipeline-level fields

| Field | Required | Type | Description |
|---|---|---|---|
| `id` | Yes | string | Unique identifier, snake_case, alphanumeric, starts with a letter |
| `goal` | Yes | string | Human-readable statement of intent; may contain `{{params.key}}` references |
| `params` | No | map | Typed pipeline parameters resolved before any task runs |
| `inputs` | No | map | Untyped inputs, referenced via `{{pipeline.inputs.key}}` |
| `tasks` | Yes | list | One or more task definitions |

---

## Task fields

| Field | Required | Default | Description |
|---|---|---|---|
| `id` | Yes | — | Unique within the pipeline; snake_case |
| `tool` | Yes | — | Registered tool name |
| `inputs` | No | `{}` | Key-value pairs passed to the tool; values may be literals or templates |
| `parallel_over` | No | `null` | Fan-out expression; must be a `{{template}}` resolving to a list |
| `retry` | No | `0` | Number of additional attempts after the first failure |
| `await` | No | `[]` | Explicit barrier: wait for listed task IDs without consuming their output |

---

## Template expressions

All `{{expr}}` references in task `inputs`, `parallel_over`, and the pipeline `goal` are resolved at runtime before the tool is called.

### Namespaces

| Expression | Resolves to |
|---|---|
| `{{task_id.output}}` | Full output of completed task `task_id` |
| `{{task_id.output.field}}` | Named field within the task's output dict |
| `{{task_id.output.list.first}}` | First element of a list field |
| `{{task_id.output.list.last}}` | Last element of a list field |
| `{{params.key}}` | Typed pipeline parameter value |
| `{{pipeline.inputs.key}}` | Untyped pipeline input value |
| `{{pipeline.goal}}` | The pipeline's goal string |
| `{{session.key}}` | Value from the session/blackboard |
| `{{item}}` | Current element in a `parallel_over` fan-out |
| `{{item.field}}` | Named field within the current fan-out element |

### Resolution rules

**Whole-value template** — when the entire input value is a single `{{expr}}` and nothing else, the resolved Python object is passed directly to the tool, preserving its type (list, dict, int, etc.):

```yaml
inputs:
  document: "{{ingest.output}}"      # passes the DocumentHandle object, not a string
  pages: "{{select.output.pages}}"   # passes the list, not a string repr
```

**Embedded template** — when `{{expr}}` appears alongside other text, all expressions are stringified and interpolated into the surrounding string:

```yaml
inputs:
  filename: "{{params.ticker}}_{{params.period_end}}_extraction"
  prompt: "Summarize the report for {{params.company}} (FY {{params.fiscal_year}})."
```

Objects without a meaningful string representation (e.g., raw dataclasses) will raise a `ResolutionError` in embedded context — access a specific text field instead.

### Path traversal

After resolving the root value, additional dot-separated segments are walked:

```yaml
# If fetch.output is:
# {"results": [{"url": "https://...", "text": "..."}]}

inputs:
  url: "{{fetch.output.results.first.url}}"   # → the URL string
```

**`.first`** — shorthand for `[0]` on any list; raises if the list is empty.  
**`.last`** — shorthand for `[-1]` on any list; raises if the list is empty.

When a segment matches a dict key, the dict lookup takes priority over `.first` / `.last`.

LLM outputs that return JSON as a plain string are auto-parsed — so `{{task.output.field}}` works even when the tool returned `'{"field": 42}'`.

### Dependency inference

The executor reads every `{{expr}}` in `inputs` and `parallel_over` and extracts the leading identifier. References in the `params`, `pipeline`, `session`, and `item` namespaces are non-task references and create no dependency. Everything else is treated as a task ID:

```yaml
inputs:
  document: "{{ingest.output}}"        # → depends on task `ingest`
  schema: "{{schema.output}}"          # → depends on task `schema`
  ticker: "{{params.ticker}}"          # → no task dependency
  auth: "{{session.token}}"            # → no task dependency
```

Explicit `await` entries are also added as dependencies.

---

## Params block

The `params` block declares typed, named parameters that callers supply at invocation time. They are resolved before any task runs and are available everywhere via `{{params.key}}`.

```yaml
pipeline:
  id: sec_extraction
  goal: "Extract {{params.company}} {{params.form_type}} for {{params.period_end}}"

  params:
    ticker:
      type: string
      description: "Stock ticker (e.g. AAPL)"
    company:
      type: string
      description: "Full company name for prompts"
    period_end:
      type: string
      description: "Period end date YYYY-MM-DD"
    fiscal_year:
      type: integer
      default: 2024
    audited:
      type: boolean
      default: true
```

### Param types

| Type | Coercion |
|---|---|
| `string` | `str(value)` |
| `integer` | `int(value)` |
| `number` | `float(value)` |
| `boolean` | `bool`, or `"true"` / `"1"` / `"yes"` → `True` |
| `list` | must already be a list; no coercion |
| `object` | must already be a dict; no coercion |

### Required vs optional

A param with **no `default` key** is required — the run fails with `PipelineParamError` if the caller does not provide it.

```yaml
params:
  ticker:               # required — no default
    type: string
  fiscal_year:
    type: integer
    default: 2024       # optional — falls back to 2024
  label:
    type: string
    default: null       # optional — explicitly defaults to null
```

### Supplying params at invocation

=== "Python SDK"

    ```python
    result = await orch.run_pipeline(
        pipeline,
        params={
            "ticker": "AAPL",
            "company": "Apple Inc.",
            "period_end": "2024-09-30",
        },
    )
    ```

=== "CLI"

    ```bash
    trellis run pipelines/sec_extraction.yaml \
      --params '{"ticker": "AAPL", "company": "Apple Inc.", "period_end": "2024-09-30"}'
    ```

=== "REST API"

    ```json
    {
      "pipeline": { ... },
      "inputs": { "ticker": "AAPL", "period_end": "2024-09-30" }
    }
    ```

    !!! note
        The REST API passes params through the `inputs` field for now.

---

## Wave-based execution

The executor uses **Kahn's algorithm** to partition tasks into dependency layers (waves). All tasks in a wave run concurrently; the executor waits for the entire wave before starting the next.

```
Wave 1  →  [schema, fetch]        (no dependencies — start immediately)
Wave 2  →  [ingest]               (depends on fetch)
Wave 3  →  [select_pages]         (depends on ingest)
Wave 4  →  [extract]              (depends on select_pages + schema)
Wave 5  →  [export_json]          (depends on extract)
```

The wave structure is derived entirely from template references. Adding or removing a `{{task_id.output}}` reference is the only thing that changes the dependency graph.

### Cycle detection

If the dependency graph contains a cycle, `pipeline_execution_waves()` raises `CycleError` with the cycle path. Since dependencies are inferred from templates rather than declared explicitly, cycles can only occur if two tasks reference each other's outputs — which is a modelling error.

---

## `await` barriers

`await` declares an explicit dependency without consuming the upstream task's output. Use it when a task must run after another task for side-effect reasons, but does not use that task's output in its inputs:

```yaml
- id: write_cache
  tool: store
  inputs:
    key: filing_text
    value: "{{ingest.output}}"

- id: notify_complete
  tool: llm_job
  inputs:
    prompt: "Summarize what was stored."
    context: "{{session.filing_text}}"
  await:
    - write_cache    # ← must run after write_cache, but doesn't reference write_cache.output
```

Without the `await`, `notify_complete` would be placed in the same wave as `write_cache` since it has no template reference to it, and might read a stale session value.

`await` IDs must refer to tasks that exist in the same pipeline. The validator enforces this.

---

## `parallel_over` fan-out

`parallel_over` runs a task once per element in a list, concurrently. The current element is bound to `{{item}}` inside that task's `inputs`.

```yaml
params:
  pdf_paths:
    type: list

tasks:
  - id: ingest_all
    tool: ingest_document
    parallel_over: "{{params.pdf_paths}}"
    inputs:
      path: "{{item}}"
```

`result.outputs["ingest_all"]` is a list of outputs in the same order as `pdf_paths`.

### Rules

- `parallel_over` **must** be a `{{template}}` string — bare lists are not accepted.
- The expression must resolve to a non-string iterable at runtime.
- The task's `inputs` **must** reference `{{item}}` — a `parallel_over` with no `{{item}}` usage is rejected at validation time.
- `{{item}}` in `inputs` is only valid when `parallel_over` is set — using it outside fan-out tasks is a validation error.

### Nested field access on items

When the list contains dicts, access fields via `{{item.field}}`:

```yaml
tasks:
  - id: process_all
    tool: llm_job
    parallel_over: "{{fetch.output.results}}"
    inputs:
      prompt: "Summarize this filing: {{item.url}}"
      context: "{{item.text}}"
```

### Concurrency limit

By default all items run concurrently. Set `fan_out_concurrency` in `ExecutionOptions` (or `--concurrency` in the CLI) to cap simultaneous workers:

```python
from trellis.execution.dag import ExecutionOptions

options = ExecutionOptions(fan_out_concurrency=5)
result = await orch.run_pipeline(pipeline, options=options)
```

```bash
trellis run pipelines/ingest_all.yaml --params '...' --concurrency 5
```

---

## Retries

`retry` sets the number of **additional** attempts after an initial failure. A task with `retry: 2` will be attempted up to 3 times total.

```yaml
- id: fetch
  tool: fetch_data
  inputs:
    source: sec_edgar
    ticker: "{{params.ticker}}"
    period_end: "{{params.period_end}}"
  retry: 2
```

### Backoff

Retry delays use exponential backoff:

| Attempt | Delay |
|---|---|
| 1 (initial) | fails immediately |
| 2 (retry 1) | `retry_base_delay` (default 0.5 s) |
| 3 (retry 2) | `min(delay * 2, max_retry_delay)` (default cap: 4.0 s) |

Configure backoff via `ExecutionOptions`:

```python
options = ExecutionOptions(
    retry_base_delay=1.0,    # first retry waits 1 s
    max_retry_delay=10.0,    # cap at 10 s
    backoff_jitter=0.2,      # ±20% random jitter
)
```

Or via CLI:

```bash
trellis run pipelines/fetch.yaml --jitter 0.2
```

After all attempts are exhausted, `TaskError` is raised with `task_id`, the original exception, and the attempt count. No subsequent waves execute.

---

## Per-task timeout

Set `per_task_timeout` in `ExecutionOptions` (or `--timeout` in the CLI) to limit how long a single tool invocation may run. The timeout applies **per attempt** — a task with `retry: 2` and `per_task_timeout: 30` may spend up to 90 s in total tool invocations (plus backoff time between retries).

```python
options = ExecutionOptions(per_task_timeout=30.0)
```

```bash
trellis run pipelines/extract.yaml --timeout 30
```

When the timeout fires, `asyncio.TimeoutError` is raised for that attempt. If retries remain, the backoff-and-retry cycle continues; otherwise `TaskError` wraps the timeout.

---

## Validation rules

The following are enforced at parse time (when `Pipeline.from_yaml()` is called):

| Rule | Error |
|---|---|
| `id` and task `id` must be snake_case, alphanumeric, starting with a letter | `ValidationError` |
| Task IDs must be unique within a pipeline | `ValidationError` |
| All `{{task_id.output}}` references must resolve to a task in the same pipeline | `ValidationError` |
| All `{{params.key}}` references must be declared in the `params` block | `ValidationError` |
| `await` entries must name tasks that exist in the pipeline | `ValidationError` |
| `parallel_over` must contain a `{{template}}` expression | `ValidationError` |
| `parallel_over` tasks must reference `{{item}}` in inputs | `ValidationError` |
| `{{item}}` in inputs is only valid when `parallel_over` is set | `ValidationError` |
| `compute` tasks must declare a `function` input | `ValidationError` |
| The task graph must be acyclic | `CycleError` |
| All `{{params.key}}` references must be declared params | `ValidationError` |

Required-param checking and type coercion happen at **invocation time** (when params are passed to `run_pipeline`), not at parse time. A pipeline with undeclared-default params can be parsed successfully and will fail at runtime with `PipelineParamError` if required values are not supplied.

---

## Full example

```yaml title="pipelines/sec_extraction.yaml"
pipeline:
  id: sec_extraction
  goal: >
    Fetch the {{params.company}} {{params.form_type}} for {{params.period_end}}
    from SEC EDGAR and extract income statement face fields.

  params:
    ticker:
      type: string
      description: "Stock ticker (e.g. AAPL)"
    company:
      type: string
      description: "Full company name"
    period_end:
      type: string
      description: "Period end date YYYY-MM-DD"
    period_type:
      type: string
      default: annual
    form_type:
      type: string
      default: "10-K"
    schema_path:
      type: string
      default: "schemas/income_statement.json"
    section_filter:
      type: string
      default: face

  tasks:
    # Wave 1 — no dependencies
    - id: schema
      tool: load_schema
      inputs:
        source: "{{params.schema_path}}"

    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
        ticker: "{{params.ticker}}"
        period_end: "{{params.period_end}}"
        period_type: "{{params.period_type}}"
        count: 1
      retry: 2

    # Wave 2 — depends on fetch
    - id: ingest
      tool: ingest_document
      inputs:
        path: "{{fetch.output}}"

    # Wave 3 — depends on ingest
    - id: select_pages
      tool: select
      inputs:
        document: "{{ingest.output}}"
        prompt: >
          Select pages containing the consolidated income statement.
          Exclude balance sheet, cash flow, and cover pages.

    # Wave 4 — depends on select_pages + schema (both from earlier waves)
    - id: extract
      tool: extract_fields
      inputs:
        document: "{{select_pages.output}}"
        schema: "{{schema.output}}"
        period_end: "{{params.period_end}}"
        section_filter: "{{params.section_filter}}"

    # Wave 5 — depends on extract
    - id: export_json
      tool: export
      inputs:
        data: "{{extract.output}}"
        format: json
        filename: "{{params.ticker}}_{{params.period_end}}_extraction"
        output_dir: outputs
```

---

## Next steps

- [Tools & Registry](tools-index.md) — reference for all built-in tools and their inputs
- [Tutorials](tutorials/index.md) — end-to-end examples with YAML and expected outputs
- [CLI](interfaces-cli.md) — `trellis validate` and `trellis run` with all flags
