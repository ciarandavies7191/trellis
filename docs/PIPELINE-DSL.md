# Pipeline DSL v1.4 — Reference Specification

## Overview

Pipeline (or Trellis) DSL v1.4 is a declarative, YAML-based language for describing agentic
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

## First-Class Handle Types

The runtime passes typed handle objects between tasks as task outputs. These flow
through the DAG identically to any other value — `{{task_id.output}}` resolves to the
handle at execution time.

### `DocumentHandle`

Produced by `ingest_document`. Carries a list of `Page` objects, each with `.text`
populated (native or OCR'd), optional `.image_bytes`, and `.sheet_name` for XLSX pages.
Consumed by `select`, `extract_from_texts`, `extract_from_tables`, `extract_fields`,
and `load_schema`.

### `SchemaHandle`

Produced by `load_schema` (or by an `llm_job` that infers structure). Carries an
ordered list of `FieldDefinition` objects, source provenance, and an optional `raw`
field retaining the original template bytes for populate-mode `export`.

```
SchemaHandle
  .fields:  list[FieldDefinition]   # name, type_hint, required, description
  .source:  str                     # provenance ("credit_memo_v2", "template.xlsx", …)
  .raw:     Any                     # original source bytes/dict; used for populate-mode export
```

**Key invariant:** the schema is always explicit in the pipeline graph. Field definitions
are never embedded invisibly in a prompt string.

`SchemaHandle` is to structured output what `DocumentHandle` is to document content. It
flows through the pipeline as a standard task output and is consumed by `extract_fields`
and `export`.

### `PeriodDescriptor`

Produced by the `fiscal_period_logic` compute function. Carries a human-readable
period label, ISO period-end date, period type, and `is_annual` flag.

```
PeriodDescriptor
  .label:       str   # "Q1 2025", "FY 2024"
  .period_end:  str   # "2025-03-31"
  .period_type: str   # "annual" | "ytd_current" | "ytd_prior"
  .is_annual:   bool
```

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

**Deterministic computation belongs in `compute`, not `llm_job`.** Any rule fully
expressible in code (date arithmetic, ticker resolution, period labelling, currency
normalization) should use a registered `compute` function. Reserve `llm_job` for tasks
that genuinely require language model judgment.

---

## Tool Registry

### Document Processing Pipeline

The document tools form a clear, ordered pipeline for unstructured content:

```
ingest_document → select → extract_from_texts
                          → extract_from_tables
```

For schema-guided extraction of known fields, add `load_schema` and `extract_fields`:

```
ingest_document ──────────────────────────► select → extract_from_texts / extract_from_tables
load_schema (template) → SchemaHandle ──► extract_fields (schema + rules)
                                                    └──► export (schema, format: xlsx)
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

### `load_schema`

Produces a `SchemaHandle` from any schema source. Parallel to `ingest_document` for
document content — the schema is loaded once and flows through the pipeline as data.

Source resolution order:
1. Registered name → returned directly from `SchemaRegistry`
2. Existing `SchemaHandle` → returned as-is (pass-through)
3. `DocumentHandle` → schema derived from document structure (column headers for
   XLSX/CSV, top-level keys for JSON/YAML, `hint`-driven LLM pass for unstructured)
4. `dict` or `list` → field definitions parsed inline
5. File path or URL → loaded then treated as (3)

```yaml
# Derive schema from a loaded Excel template
- id: load_output_schema
  tool: load_schema
  inputs:
    source: "{{ingest_template.output}}"
    hint: "Column headers in the first sheet define the field names"

# Load a registered schema by name
- id: load_credit_schema
  tool: load_schema
  inputs:
    source: credit_memo_v2
```

| Input | Required | Description |
|---|---|---|
| `source` | yes | File path, URL, registered name, `DocumentHandle`, dict, or existing `SchemaHandle` |
| `hint` | no | Natural language hint guiding derivation when source is a `DocumentHandle` or ambiguous file |

**Emits:** A `SchemaHandle`. **Terminal?** No.

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
structured row/column data. For extraction against a known field contract, prefer
`extract_fields`.

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

### `extract_fields`

Schema-bound field extraction from a document. Extracts values for every field declared
in a `SchemaHandle`. Use this instead of `extract_from_texts` when you have a known
field contract — the schema is explicit, the output is bounded to declared fields, and
missing values are surfaced as a sentinel rather than hallucinated.

Fields that cannot be located in the source document are emitted as the sentinel string
`"__not_found__"` — a warning, not a hard error. A follow-on `llm_job` can review and
resolve not-found fields before `export`.

```yaml
- id: extract_financials
  tool: extract_fields
  inputs:
    document: "{{select_statements.output}}"
    schema: "{{load_output_schema.output}}"
    rules: "{{ingest_spreading_manual.output}}"   # optional
```

| Input | Required | Description |
|---|---|---|
| `document` | yes | `DocumentHandle`, page list, or text string to extract from |
| `schema` | yes | `SchemaHandle` declaring which fields to extract |
| `rules` | no | `DocumentHandle` containing extraction rules (e.g. a spreading manual). Per-field instructions are injected into the extraction context |
| `selector` | no | Natural language hint to scope extraction to a region of the document |

**Emits:** `{field_name: extracted_value | "__not_found__"}` — only fields declared in
the schema are present. No hallucinated fields. **Terminal?** No.

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

Use `compute` instead of `llm_job` for any computation that is fully expressible in
code (date arithmetic, ticker resolution, period labelling, currency normalization).

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

### `compute`

Invokes a named deterministic function from the `FunctionRegistry`. This is the single
DSL surface for all computations that are fully expressible in code — date arithmetic,
ticker resolution, fiscal period logic, currency normalization. It does not accept code
strings; the function definition lives in the operator's registry.

The trust boundary mirrors `fetch_data`'s `source` registry: the model references
registered function names; it cannot define or inject implementations.

```yaml
# Resolve fiscal periods for a given date
- id: resolve_periods
  tool: compute
  inputs:
    function: fiscal_period_logic
    as_of_date: "{{pipeline.inputs.as_of_period}}"
    company: "{{pipeline.inputs.company}}"

# Resolve ticker symbol
- id: resolve_ticker
  tool: compute
  inputs:
    function: ticker_lookup
    company: "{{pipeline.inputs.company}}"

# Normalize currency and scale
- id: normalize_scale
  tool: compute
  inputs:
    function: financial_scale_normalize
    value: "{{extract_financials.output.total_revenue}}"
    source_currency: "{{extract_financials.output.currency}}"
    target_currency: USD
    target_scale: millions
```

| Input | Required | Description |
|---|---|---|
| `function` | yes | Registered function name. Runtime rejects unknown names |
| (others) | varies | Additional key-value inputs forwarded to the function implementation |

**Validation:** A `compute` task must declare a `function` input key. This is checked
at parse time. Registry membership is validated at execution time (consistent with how
`fetch_data` handles its `source`).

**Emits:** Whatever the registered function returns. **Terminal?** No.

#### Built-in Finance Functions

These functions are registered at startup in the default `FunctionRegistry`:

| Function | Inputs | Output | Description |
|---|---|---|---|
| `fiscal_period_logic` | `as_of_date: str`, `company: str` | `list[PeriodDescriptor]` | Returns 1 or 3 period descriptors depending on whether `as_of_date` is a fiscal year-end |
| `ticker_lookup` | `company: str` | `str` | Resolves a company name to its primary exchange ticker |
| `financial_scale_normalize` | `value`, `source_currency`, `target_currency`, `target_scale` | `float` | Converts a financial value between currency and scale units |
| `period_label` | `date: str`, `period_type: str` | `str` | Produces a standardised period label (e.g. `"Q1 2025"`, `"FY 2024"`) |
| `fiscal_year_end` | `company: str` | `str` | Returns the fiscal year-end as `"MM-DD"` for a given company |

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

**Schema-aware mode (v1.4):** When an optional `schema` input is provided, `export`
validates the content against the schema before writing. Missing required fields raise a
`ContractError`. Extra fields are dropped with a warning (set `strict: true` to raise
instead).

**Populate mode:** When `schema.raw` is present and the output format matches the
raw source type (e.g. both XLSX), `export` operates in *populate* mode — values are
written into the original template file rather than generating a new file from scratch.

```yaml
# Basic export (unchanged from v1.3)
- id: produce_report
  tool: export
  inputs:
    content: "{{final_summary.output}}"
    format: markdown
    filename: "q3_analysis_report"

# Schema-validated export with populate mode
- id: produce_output
  tool: export
  inputs:
    data: "{{extract_financials.output}}"
    schema: "{{load_output_schema.output}}"
    format: xlsx
    filename: "q1_2025_spreading"
```

| Input | Required | Description |
|---|---|---|
| `content` / `data` | no | Content to export (`data` takes precedence when both supplied) |
| `format` | no | `markdown`, `json`, `csv`, `xlsx`, `pdf`. Default: `markdown` |
| `filename` | no | Base filename without extension |
| `schema` | no | `SchemaHandle`. When present, validates conformance and enables populate mode |
| `strict` | no | When `true`, extra fields raise `ContractError` instead of being dropped. Default: `false` |

**Emits:** A file handle or download reference for the produced artifact. **Terminal?** Yes.

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

**Runtime-resolved fan-out:** When the list to fan over is produced by a preceding task
(e.g. `compute` returning a list of `PeriodDescriptor`s), `parallel_over` resolves at
execution time. This is the standard pattern for dynamic fan-out:

```yaml
- id: resolve_periods
  tool: compute
  inputs:
    function: fiscal_period_logic
    as_of_date: "{{pipeline.inputs.as_of_date}}"
    company: "{{pipeline.inputs.company}}"

- id: fetch_per_period
  tool: fetch_data
  parallel_over: "{{resolve_periods.output}}"
  inputs:
    source: sec_edgar
    period: "{{item}}"
    company: "{{pipeline.inputs.company}}"
```

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

## Canonical Archetype: Financial Spreading

The financial spreading archetype is the reference pattern for schema-guided document
extraction. It uses `compute` (fiscal period resolution), `load_schema` (template
schema derivation), `extract_fields` (schema-bound extraction), and `export` (populate
mode).

Canonical graph shape:

```
compute (fiscal_period_logic)
    └──► fetch_data (sec_edgar, parallel_over periods)
              └──► ingest_document (template)
              └──► ingest_document (spreading manual)
              └──► load_schema (from template DocumentHandle)
                        └──► select (financial statement pages)
                                  └──► extract_fields (schema + rules)
                                            └──► export (schema, format: xlsx)
                                            └──► export (format: markdown)
```

Example pipeline fragment:

```yaml
pipeline:
  id: financial_spreading
  goal: "Extract financial statement data into a standardised spreading template"
  inputs:
    company: Apple
    as_of_date: "2024-09-30"
    report_path: "{{pipeline.inputs.report_path}}"
    template_path: "{{pipeline.inputs.template_path}}"
    manual_path: "{{pipeline.inputs.manual_path}}"

  tasks:

    - id: resolve_periods
      tool: compute
      inputs:
        function: fiscal_period_logic
        as_of_date: "{{pipeline.inputs.as_of_date}}"
        company: "{{pipeline.inputs.company}}"

    - id: ingest_report
      tool: ingest_document
      inputs:
        path: "{{pipeline.inputs.report_path}}"

    - id: ingest_template
      tool: ingest_document
      inputs:
        path: "{{pipeline.inputs.template_path}}"

    - id: ingest_manual
      tool: ingest_document
      inputs:
        path: "{{pipeline.inputs.manual_path}}"

    - id: load_output_schema
      tool: load_schema
      inputs:
        source: "{{ingest_template.output}}"
        hint: "Column headers in the first sheet define the field names"

    - id: select_statements
      tool: select
      inputs:
        document: "{{ingest_report.output}}"
        prompt: "Pages containing income statement, balance sheet, or cash flow statement"

    - id: extract_financials
      tool: extract_fields
      inputs:
        document: "{{select_statements.output}}"
        schema: "{{load_output_schema.output}}"
        rules: "{{ingest_manual.output}}"

    - id: produce_xlsx
      tool: export
      inputs:
        data: "{{extract_financials.output}}"
        schema: "{{load_output_schema.output}}"
        format: xlsx
        filename: "spreading_output"

    - id: produce_markdown
      tool: export
      inputs:
        data: "{{extract_financials.output}}"
        format: markdown
        filename: "spreading_output"
```

---

## Full Example — Data Acquisition

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
5. **Deterministic computation lives in `compute`.** Any rule fully expressible in code
   belongs in a registered function, not an `llm_job` prompt. This makes computation
   auditable, testable, and cheaper to run.
6. **Schema is explicit.** Field definitions are declared as a `SchemaHandle` in the
   pipeline graph, never embedded invisibly in a prompt string.
7. **Tools are polymorphic.** Tools interpret inputs contextually. No type coercion or
   format hints required.
8. **Flat by default.** A linear list of tasks with reference wiring is the normal
   form. Avoid nesting.
9. **Parallelism is automatic.** Tasks with satisfied inputs run concurrently. No
   explicit parallel blocks needed in most cases.
10. **Scope before processing.** Use `select` to reduce document scope before passing to
    `extract_from_tables`, `extract_from_texts`, `extract_fields`, or `llm_job`.
11. **Persistence is explicit.** Nothing writes to the session blackboard unless a
    `store` task is present. `store` keys must match the sub-pipeline's `stores`
    declaration in the plan.
12. **Session references are declared.** `{{session.key}}` references must correspond
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
| Inline function definitions | All compute logic lives in the `FunctionRegistry`; the DSL only names functions |

---

## Tool Summary

| Tool | Purpose | Terminal? |
|---|---|---|
| `ingest_document` | Load files/URLs and fully resolve (incl. OCR) into a `DocumentHandle` | no |
| `load_schema` | Load or derive a `SchemaHandle` from a file, URL, `DocumentHandle`, or registry name | no |
| `select` | Retrieval: filter document to relevant pages by NL prompt or page numbers | no |
| `extract_from_texts` | Structured extraction of specific fields from page text (prompt-driven) | no |
| `extract_from_tables` | Structured extraction of row/column/cell table data | no |
| `extract_fields` | Schema-bound field extraction from a document (emits `"__not_found__"` for missing fields) | no |
| `llm_job` | LLM reasoning, extraction, synthesis, generation | no |
| `fetch_data` | Retrieve structured data from external sources | no |
| `search_web` | Web search, returns snippets and URLs | no |
| `compute` | Invoke a named deterministic function from the `FunctionRegistry` | no |
| `store` | Persist a value to the session blackboard | yes* |
| `export` | Produce a file artifact (md, pdf, csv, xlsx, json); schema-aware in v1.4 | yes |
| `extract_chart` | Extract chart data from documents (stub) | no |
| `classify_page` | Page classification to guide extraction (reserved) | no |

*`store` is logically terminal but may appear mid-pipeline if persistence is needed
before further processing steps.

> Note: `extract_chart` is a stub. `classify_page` is reserved in the DSL but not
> registered by default — implement and register a `BaseTool` to use it.

---

## Version History

| Version | Changes |
|---|---|
| 1.0 | Initial spec: pipeline structure, tool registry, template syntax |
| 1.1 | Added `pipeline.inputs` block; polymorphic tool philosophy |
| 1.2 | Added full tool registry: `select`, `extract_text`, `search_web`, `store`, `export` |
| 1.3 | Added two-level Planner/Generator architecture; Plan document schema; task tokens `[PLAN]` / `[PIPELINE]`; session `reads`/`stores` contract; complexity budget guidance |
| 1.4 (doc rename) | Renamed and clarified document tools: `load_document` → `ingest_document` (eager OCR); `extract_text` → `extract_from_texts`; `extract_table` → `extract_from_tables`; `select` role clarified as pure retrieval |
| 1.4 (this revision) | **Schema as first-class object:** `SchemaHandle`, `FieldDefinition`, `PeriodDescriptor` handle types; `load_schema` tool; `extract_fields` tool; schema-aware `export` with conformance validation and populate mode. **Deterministic compute:** `compute` tool; `FunctionRegistry`; built-in finance functions (`fiscal_period_logic`, `ticker_lookup`, `financial_scale_normalize`, `period_label`, `fiscal_year_end`). **New design principles** (5, 6). **Financial spreading archetype.** Runtime-resolved fan-out pattern. All changes additive — no v1.3 pipelines require modification. |

---

**DSL Version:** 1.4  
**Status:** Active  
**Intended consumers:** Fine-tuned small language model (Planner + Generator modes),
DAG runtime executor (consumer)
