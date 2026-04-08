# Pipeline DSL v1.3 — Reference Specification

## Overview

Pipeline DSL v1.3 is a declarative, YAML-based language for describing agentic
execution workflows as directed acyclic graphs (DAGs). It is designed to be:

- **Generatable by a single fine-tuned small language model** operating in two modes:
  Planner and Generator
- **Executable by a DAG runtime** (e.g. Prefect, Airflow, or a custom executor)
- **Minimal by design** — logic and reasoning are delegated to `llm_job` tasks, not
  encoded in the DSL itself
- **Scalable to 20-30+ task workflows** via plan-level decomposition into sub-pipelines

The DSL does not include conditionals, loops, or decorators. Control flow complexity
lives inside `llm_job` tool calls, not in the pipeline structure.

---

## Two-Level Generation Architecture

Complex workflows exceeding ~10 tasks are handled by decomposing them into a **plan**
of named sub-pipelines, each of which is then generated independently. This keeps
individual pipelines within the reliable generation window of a small fine-tuned model
while supporting arbitrarily large overall workflows.

A single fine-tuned model handles both levels, distinguished by a **task token**
prepended to every prompt:

| Token | Output schema | Purpose |
|---|---|---|
| `[PLAN]` | Plan YAML | Decompose a complex goal into named sub-pipelines |
| `[PIPELINE]` | Pipeline YAML | Generate a single sub-pipeline's DAG |

The two output schemas share structural similarity (both YAML, both `id`/`goal`-rooted,
both express dependency) but have no overlapping field names, giving the model a clean
discriminative signal from the root key (`plan:` vs `pipeline:`).

### Generation and Execution Loop

```
[PLAN] <user prompt>
    └──► model generates Plan YAML
              └──► orchestrator topological-sorts sub-pipelines by reads/stores
                        └──► for each sub-pipeline in order:
                                  [PIPELINE] goal: <goal> | reads: <keys> | inputs: <params>
                                      └──► model generates Pipeline YAML
                                                └──► validate
                                                          └──► execute
                                                                    └──► store outputs to blackboard
```

The `reads` list passed to each `[PIPELINE]` prompt reflects what is currently
available on the session blackboard at the point of generation, preventing hallucinated
`{{session.*}}` references.

### Complexity Budget

| Level | Ceiling | Rationale |
|---|---|---|
| Tasks per sub-pipeline | ~10 | Reliable generation window for a 3-4B model |
| Sub-pipelines per plan | ~8 | Keeps plan document tractable |
| Effective total tasks | ~60-80 | Well beyond the 20-30 task requirement |

---

## Plan Document

The plan is the intermediate representation between a complex user goal and the
individual pipeline YAMLs. It is produced by the model in `[PLAN]` mode and consumed
by the orchestrator.

```yaml
plan:
  id: <string>          # unique identifier, snake_case
  goal: <string>        # natural language restatement of the overall intent
  inputs:               # optional — top-level parameters passed into the plan
    <key>: <value>
  sub_pipelines: <list>
```

### Sub-Pipeline Entry

```yaml
- id: <string>          # unique sub-pipeline identifier, snake_case
  goal: <string>        # natural language goal for this sub-pipeline
  reads: [<key>, ...]   # session blackboard keys this sub-pipeline will read
  stores: [<key>, ...]  # session blackboard keys this sub-pipeline will write
  inputs: [<key>, ...]  # optional — pipeline.inputs keys forwarded from plan.inputs
```

### Fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique sub-pipeline identifier, snake_case |
| `goal` | yes | Passed verbatim as the goal of the generated pipeline |
| `reads` | yes | Blackboard keys consumed. Drives topological ordering. Empty list `[]` for root sub-pipelines |
| `stores` | yes | Blackboard keys produced. Must match `store` task keys in the generated pipeline |
| `inputs` | no | Plan-level input keys forwarded to this sub-pipeline |

### Dependency Resolution in Plans

Sub-pipeline execution order is derived from `reads`/`stores` relationships, exactly
as task dependencies are derived from `{{ref}}` templates within a pipeline. A
sub-pipeline whose `reads` list is empty or fully satisfied runs immediately. Multiple
sub-pipelines with satisfied reads run in parallel.

### Plan Example

```yaml
plan:
  id: gulf_disruption_assessment
  goal: >
    Assess disruption, impact, and credit risk across three energy disruption
    scenarios. Report on liquidity, supply-chain, and energy-cost sensitivity.
  inputs:
    companies: [Google, Apple, Microsoft, Agilent]
    year: 2025

  sub_pipelines:

    - id: data_acquisition
      goal: "Fetch company financials, energy exposure, market data, and web context"
      reads: []
      stores: [company_financials, energy_exposure, energy_markets, disruption_context]
      inputs: [companies, year]

    - id: short_term_assessment
      goal: "Assess disruption, impact and credit for 1-month risk premium shock"
      reads: [company_financials, energy_exposure, energy_markets, disruption_context]
      stores: [credit_short, impact_short]
      inputs: [companies]

    - id: medium_term_assessment
      goal: >
        Assess disruption, impact and credit for 1-3 month supply constraint scenario,
        building on short-term credit assessment
      reads: [company_financials, energy_exposure, energy_markets,
              disruption_context, credit_short]
      stores: [credit_medium, impact_medium]
      inputs: [companies]

    - id: long_term_assessment
      goal: >
        Assess disruption, impact and credit for >6 month sustained disruption,
        building on medium-term credit assessment
      reads: [company_financials, energy_exposure, energy_markets,
              disruption_context, credit_medium]
      stores: [credit_long, impact_long]
      inputs: [companies]

    - id: synthesis
      goal: >
        Synthesise liquidity, supply-chain, and energy-cost sensitivity analysis
        across all three scenario horizons for all companies
      reads: [company_financials, energy_exposure,
              credit_short, credit_medium, credit_long,
              impact_short, impact_medium, impact_long]
      stores: [liquidity_analysis, supply_chain_analysis, energy_cost_analysis]
      inputs: [companies]

    - id: final_report
      goal: "Produce executive credit assessment report across all scenarios"
      reads: [liquidity_analysis, supply_chain_analysis, energy_cost_analysis]
      stores: []
```

### Inferred Sub-Pipeline DAG

```
data_acquisition
    └──► short_term_assessment
               └──► medium_term_assessment
                          └──► long_term_assessment ──┐
                                                       ├──► synthesis ──► final_report
    └──────────────────────────────────────────────────┘
```

`data_acquisition` is the sole root. The three assessment sub-pipelines are sequential
(credit assessments chain). `synthesis` waits on all three. `final_report` waits on
`synthesis`.

---

## Pipeline Document

A pipeline is the executable unit. Each sub-pipeline entry in the plan produces one
pipeline document when the model runs in `[PIPELINE]` mode.

### Top-Level Structure

```yaml
pipeline:
  id: <string>        # matches the sub-pipeline id from the plan
  goal: <string>      # matches the sub-pipeline goal from the plan
  inputs:             # optional — named parameters provided by the orchestrator
    <key>: <value>
  tasks: <list>
```

### Fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique pipeline identifier, snake_case. Should match plan sub-pipeline id |
| `goal` | yes | Human-readable description of this pipeline's intent |
| `inputs` | no | Named input parameters. Referenced as `{{pipeline.inputs.key}}` |
| `tasks` | yes | List of task objects |

### Pipeline Inputs

The `inputs` block declares parameters provided by the orchestrator at execution time.
These are values that vary per invocation and should not be hardcoded in task
definitions.

```yaml
pipeline:
  id: data_acquisition
  goal: "Fetch company financials, energy exposure, market data, and web context"
  inputs:
    companies: [Google, Apple, Microsoft, Agilent]
    year: 2025
  tasks:
    - id: fetch_financials
      tool: fetch_data
      inputs:
        source: company_info
        companies: "{{pipeline.inputs.companies}}"
        year: "{{pipeline.inputs.year}}"
```

**Parameters** vary per invocation and belong in `inputs`. **Configuration** is fixed
vocabulary belonging inline in tasks (source names, prompt strings, format hints).

---

## Task Structure

```yaml
- id: <string>
  tool: <string>
  inputs:
    <key>: <value | template>
  parallel_over: <template>     # optional
  retry: <int>                  # optional, default 0
  await: [<task_id>, ...]       # optional, escape hatch only
```

### Fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique task identifier within the pipeline, snake_case |
| `tool` | yes | Name of the tool to invoke (see Tool Registry) |
| `inputs` | yes | Key-value pairs passed to the tool. Values may be literals or templates |
| `parallel_over` | no | Fan-out: runs the task once per item in the referenced list. Item bound to `{{item}}` |
| `retry` | no | Number of retry attempts on task failure. Default: `0` |
| `await` | no | Explicit barrier — wait for listed task IDs without consuming output. Use only when no input reference exists |

---

## Dependency Resolution

**Within a pipeline, dependencies are implicit and inferred from input templates.**
There is no `depends_on` field.

The runtime parses all `{{task_id.output}}` references across `inputs` and
`parallel_over` fields to construct the DAG. Tasks with no upstream references are
roots and execute immediately. Tasks whose inputs are all satisfied execute in parallel
automatically.

### Rules

- A task may not reference its own output
- Circular references are invalid and must be rejected at parse time
- `await` is the only mechanism for a dependency that produces no consumed output
- `{{session.*}}` references never create intra-pipeline dependencies — they resolve
  from the blackboard at task execution time

---

## Template Syntax

Templates use double-brace syntax: `{{expression}}`

### Reference Forms

| Template | Resolves to |
|---|---|
| `{{task_id.output}}` | Full output of a completed task in this pipeline |
| `{{task_id.output.field}}` | Named field within a task's output |
| `{{pipeline.inputs.key}}` | A named pipeline input parameter |
| `{{pipeline.goal}}` | The top-level pipeline goal string |
| `{{session.key}}` | A value previously stored to the session blackboard |
| `{{item}}` | Current element when inside a `parallel_over` task |

Templates may appear in any input value and in `parallel_over`. They may not appear in
`id`, `tool`, or other structural fields.

### Session References

`{{session.key}}` references must only be used for keys listed in the sub-pipeline's
`reads` field in the plan. The generator prompt will include the list of currently
available session keys to prevent hallucinated references.

---

## Tool Design Philosophy

All tools are **polymorphic on their inputs**. A tool interprets what it receives
contextually and does its best to fulfil the request:

- A string path or URL is treated as a single resource
- A list of paths or URLs is treated as a set, processed accordingly
- A document handle is used directly
- A list of handles is processed as a collection

The pipeline author does not need to wrap inputs, add type hints, or explicitly loop
over lists in most cases. The tool decides how to handle what it receives.

---

## Tool Registry

### Document Processing Pipeline

The four document tools form a clear, ordered pipeline:

```
ingest_document → select → extract_from_texts
                         → extract_from_tables
```

Each tool has a single, unambiguous responsibility. A tool never needs to consider
what a prior tool should have done.

---

### `ingest_document`

Loads one or more documents and fully resolves them — including OCR for scanned pages.

Accepts a file path, a URL, or a list of either. Handles PDF, XLSX, CSV, DOCX, plain
text, and images automatically. For scanned or image-based PDFs, OCR is applied
**eagerly at ingest time** — every page in the returned handle has its text field
populated. Downstream tools (`select`, `extract_from_texts`, `extract_from_tables`)
never need to consider OCR.

```yaml
- id: ingest_report
  tool: ingest_document
  inputs:
    path: "{{pipeline.inputs.report_path}}"
```

**Emits:** A `DocumentHandle` or list of handles. Every page has `.text` populated
(native PDF text, or OCR result for scanned pages). Image bytes are retained for
visual table extraction.

---

### `select`

Retrieval tool: filters a document down to a relevant subset of pages using a natural
language prompt or explicit page numbers.

Pure retrieval — no extraction, no OCR. Assumes the document was already ingested via
`ingest_document`. Analogous to RAG retrieval but operating on document page structure.
Use `select` before extraction tasks on large documents to prune context and reduce cost.

Selection modes (in priority order):
1. **Explicit pages** — `pages: [2, 3, 4]` selects exact 1-based page numbers.
2. **NL prompt** — LLM identifies relevant page numbers from a page inventory.

```yaml
- id: select_financial_pages
  tool: select
  inputs:
    document: "{{ingest_report.output}}"
    prompt: "Pages containing financial tables, balance sheets, or annual projections"
```

**Emits:** A `PageList` — a reduced view with provenance (original page numbers, sheet
names, source document).

---

### `extract_from_texts`

Structured extraction from document text. Given a selection of pages and an extraction
prompt, returns a structured JSON object with the requested fields.

No OCR is performed — assumes text is already available (i.e. `ingest_document` ran
first). Prompt-driven: the caller describes what to extract; the tool returns a dict.

Use for: narrative content, specific field values, dates, totals, named entities.
Complements `extract_from_tables` — use this for prose and field values, that for
structured row/column data.

```yaml
- id: extract_totals
  tool: extract_from_texts
  inputs:
    document: "{{select_financial_pages.output}}"
    prompt: "Extract the grand total, net profit, and report date"
```

**Emits:** A JSON dict with the extracted fields, e.g.
`{"grand_total": "£2.4M", "net_profit": "£0.8M", "report_date": "2024-03-31"}`.

---

### `extract_from_tables`

Structured table extraction from document pages. Identifies tables and returns them as
structured row/column/cell objects.

No OCR is performed — assumes text (and image bytes for visual tables) are already
available from `ingest_document`. Optional `selector` targets a specific table by name
or description.

Use for: financial statements, data tables, comparison matrices — anywhere the
row/column structure matters. Complements `extract_from_texts`.

```yaml
- id: extract_income_statement
  tool: extract_from_tables
  inputs:
    document: "{{select_financial_pages.output}}"
    selector: "income statement"    # optional
```

**Emits:** A list of table objects, each with `headers`, `rows` (list of dicts mapping
column name to cell value), `source_page`, and optional `sheet_name`.

---

### `llm_job`

Delegates a reasoning, extraction, classification, transformation, or generation task
to an LLM.

All inputs are injected into the LLM's context alongside the prompt. The prompt should
be focused and single-purpose. For complex multi-step reasoning, prefer multiple
chained `llm_job` tasks over a single large prompt.

```yaml
- id: reconcile
  tool: llm_job
  inputs:
    tables: "{{extract_tables.output}}"
    supplement: "{{summarize_supplement.output}}"
    prompt: "Identify any discrepancies between the two sources for revenue line items"
```

`llm_job` is the primary workhorse of the DSL. Conditionals, validation, classification,
and multi-source synthesis all belong here, not in the pipeline structure.

**Emits:** Text or structured data as directed by the prompt.

---

### `fetch_data`

Retrieves structured data from a named external source.

Source-specific parameters are passed through loosely. Built-in sources: `sec_edgar`,
`market_data`, `company_info`, `exchange_rates`. Additional sources can be registered
at runtime.

```yaml
- id: fetch_filings
  tool: fetch_data
  inputs:
    source: sec_edgar
    companies: "{{pipeline.inputs.companies}}"
    year: "{{pipeline.inputs.year}}"
```

**Emits:** Raw structured data in the shape native to the source.

---

### `search_web`

Performs one or more web searches and returns results as text snippets with URLs.

Accepts a query string or a list of queries run in parallel. Prefer this over embedding
search queries inside `llm_job` prompts — explicit retrieval improves auditability.

```yaml
- id: find_context
  tool: search_web
  inputs:
    query: "{{pipeline.inputs.company}} SEC investigation 2025"
```

**Emits:** A list of results, each with title, snippet, and source URL.

---

### `store`

Persists a value to the session blackboard under a named key.

Accepts any value. Subsequent pipelines in the same session read the value via
`{{session.key}}`. Overwrites existing keys unless `append: true`. Blackboard writes
are always **explicit** — no tool writes to the session silently.

The `store` key must match an entry in the sub-pipeline's `stores` list in the plan.

```yaml
- id: persist_financials
  tool: store
  inputs:
    key: company_financials
    value: "{{fetch_financials.output}}"
```

**Emits:** Confirmation of the stored key and a value summary.

---

### `export`

Produces a final output artifact in a specified format.

`export` is a terminal tool and should always be a leaf node with no downstream
dependents. Supported formats: `markdown`, `pdf`, `csv`, `xlsx`, `json`.

```yaml
- id: produce_report
  tool: export
  inputs:
    content: "{{final_summary.output}}"
    format: markdown
    filename: "q3_analysis_report"
```

**Emits:** A file handle or download reference for the produced artifact.

---

## Fan-out with `parallel_over`

Runs a task once per element in a list with explicit per-item parallelism. The runtime
fans out, runs instances in parallel, and collects results into a list.

In most cases, passing a list directly to a tool is sufficient — tools handle lists
internally. Use `parallel_over` when downstream tasks need to reference per-item
results individually.

```yaml
- id: assess_per_company
  tool: llm_job
  parallel_over: "{{pipeline.inputs.companies}}"
  inputs:
    company: "{{item}}"
    financials: "{{fetch_financials.output}}"
    prompt: "Assess credit risk for {{item}} under current scenario"
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

Use sparingly. Frequent use of `await` indicates the pipeline structure needs
revisiting.

---

## Full Example

The `data_acquisition` sub-pipeline from the Gulf disruption assessment plan,
showing session blackboard persistence via `store`.

```yaml
pipeline:
  id: data_acquisition
  goal: "Fetch company financials, energy exposure, market data, and web context"
  inputs:
    companies: [Google, Apple, Microsoft, Agilent]
    year: 2025

  tasks:

    - id: fetch_financials
      tool: fetch_data
      inputs:
        source: company_info
        companies: "{{pipeline.inputs.companies}}"
        fields: [liquidity_ratios, debt_structure, credit_ratings, revenue_breakdown]
        year: "{{pipeline.inputs.year}}"

    - id: fetch_energy_exposure
      tool: fetch_data
      inputs:
        source: company_info
        companies: "{{pipeline.inputs.companies}}"
        fields: [energy_cost_pct_revenue, supply_chain_geography, energy_procurement]

    - id: fetch_energy_markets
      tool: fetch_data
      inputs:
        source: market_data
        markets: [crude_oil, natural_gas, energy_risk_premium]
        fields: [price, volatility, forward_curve]

    - id: search_disruption_context
      tool: search_web
      inputs:
        query: "Gulf energy export disruption logistics bottleneck 2025"

    - id: store_financials
      tool: store
      inputs:
        key: company_financials
        value: "{{fetch_financials.output}}"

    - id: store_energy_exposure
      tool: store
      inputs:
        key: energy_exposure
        value: "{{fetch_energy_exposure.output}}"

    - id: store_energy_markets
      tool: store
      inputs:
        key: energy_markets
        value: "{{fetch_energy_markets.output}}"

    - id: store_disruption_context
      tool: store
      inputs:
        key: disruption_context
        value: "{{search_disruption_context.output}}"
```

### Inferred DAG

```
fetch_financials ──────────► store_financials
fetch_energy_exposure ─────► store_energy_exposure
fetch_energy_markets ──────► store_energy_markets
search_disruption_context ─► store_disruption_context
```

All four fetch/search tasks are roots — they run in parallel immediately. Each `store`
task runs as soon as its upstream task completes.

---

## Design Principles

1. **Topology only.** The DSL encodes execution order and data flow, not logic or
   reasoning.
2. **Two levels of composition.** Complex goals decompose into plans; plans decompose
   into pipelines; pipelines decompose into tasks. Each level has a hard complexity
   ceiling.
3. **Dependencies are implicit.** Inferred from `{{template}}` references within
   pipelines, and from `reads`/`stores` relationships between sub-pipelines.
4. **Logic lives in `llm_job`.** Conditionals, validation, and decisions belong in
   natural language prompts, not DSL constructs.
5. **Tools are polymorphic.** Tools interpret inputs contextually. No type coercion or
   format hints required.
6. **Flat by default.** A linear list of tasks with reference wiring is the normal
   form. Avoid nesting.
7. **Parallelism is automatic.** Tasks with satisfied inputs run concurrently. No
   explicit parallel blocks needed in most cases.
8. **Scope before processing.** Use `select` to reduce document scope before passing to
   `extract_from_tables`, `extract_from_texts`, or `llm_job`.
9. **Persistence is explicit.** Nothing writes to the session blackboard unless a
   `store` task is present. `store` keys must match the sub-pipeline's `stores`
   declaration in the plan.
10. **Session references are declared.** `{{session.key}}` references must correspond
    to keys listed in the sub-pipeline's `reads`. Undeclared reads are invalid.

---

## What This DSL Intentionally Omits

| Concept | Rationale |
|---|---|
| Conditionals / branching | Handled inside `llm_job` prompts |
| Loops / iteration (non-fan-out) | Handled inside `llm_job` or as `parallel_over` |
| Error handling / fallback paths | Runtime concern, not DSL concern |
| Variable assignment | Outputs referenced directly via templates |
| Type constraints on inputs | Tools interpret inputs polymorphically |
| Implicit blackboard writes | All persistence is via explicit `store` tasks |

---

## Tool Summary

| Tool | Purpose | Terminal? |
|---|---|---|
| `ingest_document` | Load files/URLs and fully resolve (incl. OCR) into a DocumentHandle | no |
| `select` | Retrieval: filter document to relevant pages by NL prompt or page numbers | no |
| `extract_from_texts` | Structured extraction of specific fields from page text | no |
| `extract_from_tables` | Structured extraction of row/column/cell table data | no |
| `llm_job` | LLM reasoning, extraction, synthesis, generation | no |
| `fetch_data` | Retrieve structured data from external sources | no |
| `search_web` | Web search, returns snippets and URLs | no |
| `store` | Persist a value to the session blackboard | yes* |
| `export` | Produce a file artifact (md, pdf, csv, xlsx, json) | yes |
| `extract_chart` | Extract chart data from documents (stub) | no |
| `classify_page` | Page classification to guide extraction (reserved) | no |

*`store` is logically terminal but may appear mid-pipeline if persistence is needed
before further processing steps.

> Note: `extract_chart` is provided as a stub under tools/impls/extract.py. `classify_page` is reserved in the DSL but not registered by default — implement and register a `BaseTool` to use it.

---

## Version History

| Version | Changes |
|---|---|
| 1.0 | Initial spec: pipeline structure, tool registry, template syntax |
| 1.1 | Added `pipeline.inputs` block; polymorphic tool philosophy |
| 1.2 | Added full tool registry: `select`, `extract_text`, `search_web`, `store`, `export` |
| 1.3 | Added two-level Planner/Generator architecture; Plan document schema; task tokens `[PLAN]` / `[PIPELINE]`; session `reads`/`stores` contract; complexity budget guidance |
| 1.4 | Renamed and clarified document tools: `load_document` → `ingest_document` (eager OCR); `extract_text` → `extract_from_texts` (structured JSON output); `extract_table` → `extract_from_tables` (row/col/cell JSON); `select` role clarified as pure retrieval |

---

**DSL Version:** 1.3
**Status:** Draft
**Intended consumers:** Fine-tuned small language model (Planner + Generator modes),
DAG runtime executor (consumer)