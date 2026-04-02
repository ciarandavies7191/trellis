# Pipeline DSL v1 — Reference Specification

## Overview

Pipeline DSL v1 is a declarative, YAML-based language for describing agentic execution workflows as directed acyclic graphs (DAGs). It is designed to be:

- **Generatable by a small fine-tuned language model** from a natural language goal description
- **Executable by a DAG runtime** (e.g. Prefect, Airflow, or a custom executor)
- **Minimal by design** — logic and reasoning are delegated to `llm_job` tasks, not encoded in the DSL itself

The DSL does not include conditionals, loops, or decorators. Control flow complexity lives inside `llm_job` tool calls, not in the pipeline structure.

---

## Top-Level Structure

```yaml
pipeline:
  id: <string>        # unique identifier, snake_case
  goal: <string>      # natural language description of the overall intent
  tasks: <list>       # ordered list of task definitions (order is cosmetic only)
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique pipeline identifier, snake_case |
| `goal` | string | yes | Human-readable description of the pipeline's intent |
| `tasks` | list | yes | List of task objects (see below) |

---

## Task Structure

```yaml
- id: <string>
  tool: <string>
  inputs:
    <key>: <value | template>
  parallel_over: <template>   # optional
  retry: <int>                # optional
  await: [<task_id>, ...]     # optional, escape hatch only
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique task identifier within the pipeline, snake_case |
| `tool` | string | yes | Name of the tool or skill to invoke (see Tool Registry) |
| `inputs` | map | yes | Key-value pairs passed to the tool. Values may be literals or templates |
| `parallel_over` | template | no | Fan-out: runs the task once per item in the referenced list. Item is bound to `{{item}}` |
| `retry` | int | no | Number of retry attempts on failure. Default: 0 |
| `await` | list[string] | no | Explicit barrier — wait for listed task IDs without consuming their output. Use only when no input reference exists |

---

## Dependency Resolution

**Dependencies are implicit and inferred from input templates.** There is no `depends_on` field.

The runtime parses all `{{task_id.output}}` references in `inputs` and `parallel_over` fields to construct the dependency graph. Tasks with no upstream references are roots and execute immediately. Tasks sharing the same upstream dependency execute in parallel once that dependency resolves.

### Rules

- A task may not reference its own output
- Circular references are invalid and should be rejected at parse time
- The `await` field is the only mechanism for expressing a dependency that produces no consumed output

---

## Template Syntax

Templates use double-brace syntax: `{{expression}}`

### Reference forms

| Template | Resolves to |
|---|---|
| `{{task_id.output}}` | The full output of a completed task |
| `{{task_id.output.field}}` | A named field within a task's output |
| `{{item}}` | The current element when inside a `parallel_over` task |
| `{{pipeline.goal}}` | The top-level pipeline goal string |

Templates may appear in any input value and in the `parallel_over` field. They may not appear in `id`, `tool`, or structural fields.

---

## Built-in Tools

These are the standard tools available to the DSL. Additional domain tools may be registered in the accompanying Tool Registry.

### `llm_job`

Delegates a reasoning, extraction, transformation, or generation task to an LLM.

```yaml
- id: summarize
  tool: llm_job
  inputs:
    document: "{{ingest.output}}"
    prompt: "Summarize the key financial figures in this document"
```

| Input | Type | Description |
|---|---|---|
| `prompt` | string | Instruction to the LLM. Should be focused and single-purpose |
| `*` | any | Any additional inputs are injected into the LLM's context |

`llm_job` is the primary workhorse of the DSL. Complex logic, conditionals, validation, and multi-step reasoning should be expressed inside a `prompt`, not in the pipeline structure.

---

### `load_document`

Loads a document into working memory and returns a handle for downstream tasks.

```yaml
- id: ingest_pdf
  tool: load_document
  inputs:
    path: "Q3_earnings.pdf"
    format: pdf          # optional: pdf | xlsx | csv | auto (default: auto)
```

| Input | Type | Description |
|---|---|---|
| `path` | string | File path or URI |
| `format` | enum | `pdf`, `xlsx`, `csv`, `auto`. Default: `auto` |

---

### `extract_table`

Extracts structured tabular data from a loaded document.

```yaml
- id: extract_tables
  tool: extract_table
  inputs:
    document: "{{ingest_pdf.output}}"
    selector: "financial_statements"    # optional hint
```

| Input | Type | Description |
|---|---|---|
| `document` | DocumentHandle | Output of a `load_document` task |
| `selector` | string | Optional hint for which tables to target |

---

### `fetch_data`

Retrieves external data such as market prices, reference data, or API responses.

```yaml
- id: get_prices
  tool: fetch_data
  inputs:
    source: market_data
    ticker: "AAPL"
    fields: ["close", "volume"]
    date_range: "2024-Q3"
```

| Input | Type | Description |
|---|---|---|
| `source` | string | Named data source from the registry |
| `*` | any | Source-specific parameters |

---

## Fan-out with `parallel_over`

When a task should run once per element in a list, use `parallel_over`. The runtime fans out into parallel executions and collects results into a list before passing to downstream tasks.

```yaml
- id: extract_per_page
  tool: llm_job
  parallel_over: "{{ingest_pdf.output.pages}}"
  inputs:
    page: "{{item}}"
    prompt: "Extract any tables or financial figures from this page"
```

The output of a `parallel_over` task is always a list, one element per input item.

---

## Explicit Barrier with `await`

For the rare case where a task must wait on another without consuming its output:

```yaml
- id: generate_report
  tool: llm_job
  await: [audit_log]
  inputs:
    data: "{{reconcile.output}}"
    prompt: "Generate the final analyst report"
```

Use sparingly. If you find yourself using `await` frequently, the pipeline structure likely needs revisiting.

---

## Full Example

```yaml
pipeline:
  id: q3_earnings_analysis
  goal: "Extract and reconcile revenue figures from the Q3 earnings package"

  tasks:

    - id: ingest_pdf
      tool: load_document
      inputs:
        path: "Q3_earnings.pdf"

    - id: ingest_supplement
      tool: load_document
      inputs:
        path: "supplement.xlsx"

    - id: extract_tables
      tool: extract_table
      inputs:
        document: "{{ingest_pdf.output}}"

    - id: summarize_supplement
      tool: llm_job
      inputs:
        document: "{{ingest_supplement.output}}"
        prompt: "Extract all numeric financial figures with their labels and units"

    - id: reconcile
      tool: llm_job
      inputs:
        tables: "{{extract_tables.output}}"
        supplement_data: "{{summarize_supplement.output}}"
        prompt: "Identify any discrepancies between the two sources for revenue line items"

    - id: final_report
      tool: llm_job
      inputs:
        findings: "{{reconcile.output}}"
        prompt: "Produce a concise analyst summary of findings and any anomalies"
```

### Inferred DAG for the above

```
ingest_pdf  ──► extract_tables ──┐
                                  ├──► reconcile ──► final_report
ingest_supplement ──► summarize_supplement ──┘
```

`ingest_pdf` and `ingest_supplement` are roots — they run in parallel immediately.
`extract_tables` and `summarize_supplement` run in parallel once their respective roots complete.
`reconcile` runs once both are done.
`final_report` runs last.

---

## Design Principles

1. **Topology only.** The DSL encodes execution order and data flow, not logic or reasoning.
2. **Dependencies are implicit.** Derived from `{{template}}` references. No `depends_on`.
3. **Logic lives in `llm_job`.** Conditionals, validation, and decisions are expressed in natural language prompts, not DSL constructs.
4. **Flat by default.** Avoid nesting. A linear list of tasks with reference wiring is the normal form.
5. **Parallelism is automatic.** Any tasks whose inputs are all satisfied run concurrently. No explicit parallel blocks.
6. **Minimal vocabulary.** If a concept can be handled by an `llm_job` prompt, it should not be a DSL construct.

---

## What This DSL Intentionally Omits

| Concept | Rationale |
|---|---|
| Conditionals / branching | Handled inside `llm_job` prompts |
| Loops / iteration (non-fan-out) | Handled inside `llm_job` or modelled as `parallel_over` |
| Error handling / fallback paths | Runtime concern, not DSL concern |
| Variable assignment | Outputs are referenced directly via templates |
| Subpipelines / nesting | Out of scope for v1 |

---

## Version

**DSL Version:** 1.0  
**Status:** Draft  
**Intended consumers:** Fine-tuned small language model (generator), DAG runtime executor (consumer)
