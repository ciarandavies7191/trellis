# Tools & Registry Reference

Built-in tool catalog and the async discovery/registration system that connects tools to the pipeline executor.

---

## Quick reference

| Tool | DSL name | LLM required | What it does |
|---|---|---|---|
| [ingest_document](#ingest_document) | `ingest_document` | For OCR only | Load and parse a document; OCR any scanned pages |
| [select](#select) | `select` | For NL selection | Filter a document to relevant pages |
| [extract_from_texts](#extract_from_texts) | `extract_from_texts` | Yes | Freeform field extraction from document text |
| [extract_from_tables](#extract_from_tables) | `extract_from_tables` | Yes | Row/column/cell extraction from tabular data |
| [extract_fields](#extract_fields) | `extract_fields` | Yes | Schema-bound typed field extraction |
| [extract_chart](#extract_chart) | `extract_chart` | — | Chart data extraction (stub) |
| [load_schema](#load_schema) | `load_schema` | No | Resolve a field schema from file, dict, or document |
| [llm_job](#llm_job) | `llm_job` | Yes | General LLM reasoning and generation |
| [fetch_data](#fetch_data) | `fetch_data` | No | Fetch SEC EDGAR filings or HTTP URLs |
| [search_web](#search_web) | `search_web` | No | Web search via DuckDuckGo or SerpAPI |
| [compute](#compute) | `compute` | No | Invoke a registered deterministic function |
| [store](#store) | `store` | No | Persist a value to the session blackboard |
| [export](#export) | `export` | No | Write output to JSON, Markdown, CSV, or XLSX |

---

## ingest_document

Loads a document and fully populates the text of every page. For digital PDFs the native text layer is extracted. For scanned pages (image coverage above the threshold), a vision LLM is called to OCR each page. After this tool runs, all pages in the returned `DocumentHandle` are ready for `select`, `extract_from_texts`, and `extract_fields` — those tools never need to consider OCR.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | str, URL, list, or `DocumentHandle` | Yes | — | File path, HTTPS URL, list of paths, or the output dict of `fetch_data` |
| `model` | str | No | `INGEST_OCR_MODEL` env var or `openai/gpt-4o` | LiteLLM model string used for OCR (ignored for native-text pages) |

**Output** — `DocumentHandle`

```python
{
    "source": "https://example.com/report.pdf",
    "format": "PDF",                    # PDF | TEXT | IMAGE | XLSX | DOCX | UNKNOWN
    "page_count": 42,
    "is_scanned": False,                # True if any page was OCR'd
    "pages": [
        {
            "number": 1,                # 1-based
            "text": "Annual Report...", # populated for every page
            "is_scanned": False,
            "sheet_name": None          # set for XLSX sheets
        },
        # ...
    ]
}
```

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `INGEST_OCR_MODEL` | `openai/gpt-4o` | Vision model for OCR |
| `PYMUPDF_RASTERIZE_COVERAGE_THRESHOLD` | `0.25` | Image-coverage ratio above which a page is rasterized |
| `PYMUPDF_RASTERIZE_DPI` | `150` | DPI used when rasterizing pages |
| `EXTRACT_MIN_NATIVE_CHARS` | `80` | Char count below which OCR is preferred over native text |

**Minimal example**

```yaml
- id: ingest
  tool: ingest_document
  inputs:
    path: "{{params.pdf_path}}"
```

Pass the output of `fetch_data` directly — `ingest_document` understands the SEC EDGAR result structure:

```yaml
- id: fetch
  tool: fetch_data
  inputs:
    source: sec_edgar
    ticker: AAPL
    period_end: "2024-09-30"
    count: 1

- id: ingest
  tool: ingest_document
  inputs:
    path: "{{fetch.output}}"    # passes the full fetch_data result dict
```

---

## select

Filters a `DocumentHandle` or `PageList` to the subset of pages that match a selection criterion. Three modes in priority order:

1. **Explicit page numbers** — pass `pages: [3, 5, 12]`; no LLM call, fastest
2. **BM25 chunk retrieval** — `granularity: chunk`; builds an in-memory BM25 index over structural chunks and scores them against a keyword query; no LLM call; best for large documents
3. **NL prompt** — `granularity: page` (default); the LLM reads a page inventory (page number + first 300 chars) and returns the relevant page numbers
4. **Passthrough** — if neither `pages` nor `prompt` is provided, all pages are returned unchanged

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `document` | `DocumentHandle`, `PageList`, list, or str | Yes | — | Ingested document; pages must already have text populated |
| `prompt` | str | No | `None` | Keyword query (chunk mode) or NL instruction (page mode) |
| `pages` | list[int] | No | `None` | Explicit 1-based page numbers to select |
| `granularity` | str | No | `"page"` | `"page"` for LLM page scan; `"chunk"` for BM25 keyword retrieval |
| `top_k` | int | No | `15` | Number of top-scoring chunks to retrieve (chunk mode only) |
| `model` | str | No | `SELECT_MODEL` or `openai/gpt-4o` | LiteLLM model string for NL page-mode selection |

**Output** — `PageList` (or `list[PageList]` when input is a list)

```python
{
    "parent_source": "https://example.com/report.pdf",
    "parent_format": "PDF",
    "pages": [
        {"number": 4, "text": "Income Statement..."},
        {"number": 5, "text": "Notes to Financial Statements..."}
    ],
    "selector_prompt": "consolidated income statement"  # or "[explicit pages]" / "[passthrough]"
}
```

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `SELECT_MODEL` | `openai/gpt-4o` | Model for NL page-mode selection |

**Examples**

```yaml
# BM25 keyword retrieval — no LLM, best for large documents
- id: select_section
  tool: select
  inputs:
    document: "{{ingest.output}}"
    granularity: chunk
    prompt: "Human Capital Resources employees workforce headcount"
    top_k: 10

# NL prompt selection (default page mode)
- id: select_income_stmt
  tool: select
  inputs:
    document: "{{ingest.output}}"
    prompt: "Select only the consolidated income statement pages"

# Explicit page numbers — no LLM call
- id: select_pages
  tool: select
  inputs:
    document: "{{ingest.output}}"
    pages: [3, 4, 5]
```

See the [BM25 Section Extraction tutorial](tutorials/bm25-section-extraction.md) for a full worked example and guidance on choosing `top_k` and writing effective keyword queries.

---

## extract_from_texts

Sends the text content of selected pages to an LLM with a freeform extraction prompt. Returns the extracted fields as a structured JSON dict. Use this when you want flexible, prompt-driven extraction without a rigid schema.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `document` | `DocumentHandle`, `PageList`, list, or str | Yes | — | Document with populated page text |
| `prompt` | str | Yes | — | What to extract, e.g. `"Extract the revenue and net income figures"` |
| `model` | str | No | `EXTRACT_MODEL` or `openai/gpt-4o` | LiteLLM model override |

**Output** — `TextExtractionResult`

```python
{
    "extracted": {                      # LLM-parsed JSON dict
        "revenue": "391035",
        "net_income": "93736",
        "reporting_currency": "USD"
    },
    "source_pages": [4, 5],             # 1-based pages processed
    "sources": ["https://example.com/report.pdf"],
    "prompt": "Extract the revenue and net income figures",
    "model": "openai/gpt-4o"
}
```

Access extracted fields in downstream tasks as `{{extract.output.extracted.revenue}}`.

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `EXTRACT_MODEL` | `openai/gpt-4o` | Extraction model |

**Minimal example**

```yaml
- id: extract
  tool: extract_from_texts
  inputs:
    document: "{{select_pages.output}}"
    prompt: "Extract the total revenue, gross profit, and net income for the most recent period"
```

---

## extract_from_tables

Extracts structured table data (headers and rows) from document pages. Can target a specific table with an optional `selector` hint. Useful for XLSX, HTML tables embedded in filings, and tabular PDFs.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `document` | `DocumentHandle`, `PageList`, list, or str | Yes | — | Document with populated page text |
| `selector` | str | No | `None` | NL hint to target a specific table, e.g. `"income statement"` |
| `model` | str | No | `EXTRACT_MODEL` or `openai/gpt-4o` | LiteLLM model override |

**Output** — `TableExtractionResult`

```python
{
    "tables": [
        {
            "headers": ["", "FY2024", "FY2023"],
            "rows": [
                {"": "Total Revenues", "FY2024": "391,035", "FY2023": "383,285"},
                {"": "Cost of Sales",  "FY2024": "210,352", "FY2023": "214,137"},
                # ...
            ],
            "source_page": 4,
            "sheet_name": None,
            "selector": "income statement"
        }
    ],
    "source_pages": [4],
    "sources": ["report.pdf"],
    "model": "openai/gpt-4o"
}
```

**Minimal example**

```yaml
- id: extract_table
  tool: extract_from_tables
  inputs:
    document: "{{select_pages.output}}"
    selector: "consolidated balance sheet"
```

---

## extract_fields

Schema-bound extraction: extracts values for every field declared in a `SchemaHandle` in a single LLM call. Fields that cannot be located are set to the sentinel string `"__not_found__"`. Use this when you have a predefined list of fields and want typed, validated output.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `document` | `DocumentHandle`, `PageList`, list, or str | Yes | — | Pre-selected document pages |
| `schema` | `SchemaHandle` | Yes | — | Field definitions to extract |
| `rules` | `DocumentHandle` | No | `None` | Spreading manual or extraction rules document injected as context |
| `selector` | str | No | `None` | NL hint to scope extraction to a sub-region of the document |
| `period_end` | str | No | `None` | ISO date (YYYY-MM-DD); instructs the model to extract only this period's values |
| `section_filter` | str | No | `None` | Extract only fields with this `section` value (`face`, `segments`, `footnotes`, etc.) |

**Output** — `dict[str, Any]`

```python
{
    "revenue":          391035,
    "gross_profit":     180683,
    "operating_income": 123216,
    "net_income":       93736,
    "eps_diluted":      "__not_found__"   # field not located in document
}
```

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `EXTRACT_FIELDS_MODEL` | `openai/gpt-4o` | Extraction model |

**Minimal example**

```yaml
- id: extract
  tool: extract_fields
  inputs:
    document: "{{select_pages.output}}"
    schema: "{{load_schema.output}}"
    period_end: "{{params.period_end}}"
    section_filter: face
```

**With a spreading manual for higher accuracy**

```yaml
- id: extract
  tool: extract_fields
  inputs:
    document: "{{select_pages.output}}"
    schema: "{{load_schema.output}}"
    rules: "{{ingest_manual.output}}"
    period_end: "{{params.period_end}}"
```

---

## extract_chart

Extract numerical data from charts in a document. Currently a stub — returns an empty `charts` list. Intended for future multimodal chart parsing.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `document` | `DocumentHandle`, `PageList`, or str | Yes | — | Document containing charts |
| `classification` | `PageClassification` or list | No | `None` | Page classification to guide the backend |

**Output**

```python
{"status": "success", "charts": [], "document": "...", "classification": null}
```

---

## load_schema

Resolves a field schema from multiple source types and returns a `SchemaHandle`. Use this as the first step in any extraction pipeline that uses `extract_fields`.

**Source resolution order**

1. If `source` is already a `SchemaHandle` → returns it unchanged (pass-through)
2. If a `SchemaRegistry` is configured and `source` is a registered name → looked up
3. If `source` is a `.json` / `.yaml` / `.yml` file path → loaded and parsed
4. If `source` is a `dict` → interpreted as `{field_name: type_hint}` or `{"fields": [...]}`
5. If `source` is a `list` → treated as a list of field name strings or field definition dicts
6. If `source` is a `DocumentHandle` → field names derived from Markdown table rows or XLSX column headers

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `source` | str, dict, list, `DocumentHandle`, or `SchemaHandle` | Yes | — | Schema source (see resolution order above) |
| `hint` | str | No | `None` | NL hint guiding field derivation when source is a document |

**Output** — `SchemaHandle`

```python
{
    "fields": [
        {
            "name": "revenue",
            "type_hint": "number",
            "section": "face",
            "required": True,
            "description": "Total consolidated revenue",
            "computed": False,
            "formula": None,
            "sign_convention": None,
            "manual_ref": "§2.1"
        },
        # ...
    ],
    "source": "income_statement.json",
    "task_id": "load_schema"
}
```

**JSON schema file format**

```json
{
  "fields": [
    {
      "name": "revenue",
      "type_hint": "number",
      "section": "face",
      "description": "Total revenues",
      "required": true,
      "sign_convention": null,
      "formula": null,
      "manual_ref": "§2.1"
    }
  ]
}
```

**Minimal example**

```yaml
- id: schema
  tool: load_schema
  inputs:
    source: "schemas/income_statement.json"
```

---

## llm_job

General-purpose LLM invocation backed by LiteLLM. Accepts any number of extra keyword arguments alongside `prompt` — each extra kwarg is serialised as a labelled context block that is prepended to the prompt before the model sees it.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `prompt` | str | Yes | — | Instruction text for the LLM |
| `model` | str | No | `TRELLIS_LLM_MODEL` or `openai/gpt-4o-mini` | LiteLLM model string |
| `temperature` | float | No | `0.7` | Sampling temperature |
| `max_tokens` | int | No | `2000` | Maximum response tokens |
| `**kwargs` | any | No | — | Any additional key-value pairs become labelled context blocks in the prompt |

**Output** — `str` (the LLM response text)

**Context injection**

Any input key that is not `prompt`, `model`, `temperature`, or `max_tokens` is injected into the prompt as a labelled section:

```yaml
- id: review
  tool: llm_job
  inputs:
    extracted: "{{extract.output}}"       # injected as "--- extracted ---\n{...}"
    schema: "{{schema.output}}"           # injected as "--- schema ---\n{...}"
    prompt: "Review the extracted values and fix any __not_found__ entries."
```

The model receives:

```
--- extracted ---
{"revenue": "391035", "eps_diluted": "__not_found__"}

--- schema ---
{"source": "income_statement.json", "fields": ["revenue", "eps_diluted"]}

--- prompt ---
Review the extracted values and fix any __not_found__ entries.
```

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `TRELLIS_LLM_MODEL` | `openai/gpt-4o-mini` | Default model for all `llm_job` tasks |

---

## fetch_data

Fetches data from SEC EDGAR or a generic HTTP URL. In SEC mode, resolves the company or ticker to a CIK, queries the EDGAR submissions API, and returns a list of matching filings with URLs. In URL mode, performs a plain HTTP GET and returns the parsed response.

### SEC EDGAR mode (`source: sec_edgar`)

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `source` | str | Yes | — | `sec_edgar`, `sec`, or `edgar` |
| `companies` | str or list[str] | Yes* | — | Company names or tickers; `ticker` is an alias |
| `ticker` | str or list[str] | Yes* | — | Alias for `companies` |
| `period_end` | str | No | `None` | ISO date `YYYY-MM-DD`; year extracted for filing filter |
| `period_type` | str | No | `None` | `annual` → 10-K only; `quarterly`/`ytd_current`/`ytd_prior` → 10-Q only |
| `forms` | list[str] | No | `None` | Explicit form filter, e.g. `["10-K", "10-Q"]` |
| `year` | int | No | `None` | Filter by filing year (overrides year derived from `period_end`) |
| `count` | int | No | `20` | Max filings per company |

*One of `companies` or `ticker` is required.

**Output**

```python
{
    "status": "success",
    "source": "sec_edgar",
    "results": [
        {
            "company_input": "AAPL",
            "company_name": "Apple Inc.",
            "ticker": "AAPL",
            "cik": "0000320193",
            "filings": [
                {
                    "form": "10-K",
                    "filing_date": "2024-11-01",
                    "accession_no": "0000320193-24-000123",
                    "url": "https://www.sec.gov/Archives/edgar/data/320193/.../aapl20240928.htm",
                    "primary_document": "aapl20240928.htm"
                }
            ]
        }
    ]
}
```

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `SEC_USER_AGENT` | `Trellis/0.1 (contact@example.com)` | User-Agent header sent to SEC EDGAR (required by SEC policy) |
| `SEC_THROTTLE_SECONDS` | `0.2` | Delay between EDGAR requests |

### HTTP URL mode (`source: url`)

**Additional inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `url` | str | Yes | — | HTTP URL to fetch |
| `method` | str | No | `GET` | HTTP method |
| `headers` | dict | No | `None` | Custom HTTP headers |

**Output**

```python
{
    "status": "success",
    "source": "url",
    "url": "https://api.example.com/data.json",
    "content_type": "application/json",
    "data": {"key": "value"}           # parsed JSON, text, or raw bytes
}
```

**Examples**

```yaml
# Annual 10-K for Apple, fiscal year ending 2024-09-30
- id: fetch
  tool: fetch_data
  inputs:
    source: sec_edgar
    ticker: AAPL
    period_end: "2024-09-30"
    period_type: annual
    count: 1

# Multiple companies
- id: fetch_multi
  tool: fetch_data
  inputs:
    source: sec_edgar
    companies: ["Apple Inc.", "Microsoft Corporation"]
    forms: ["10-K"]
    year: 2024

# HTTP URL
- id: fetch_json
  tool: fetch_data
  inputs:
    source: url
    url: "https://api.example.com/metrics.json"
```

---

## search_web

Performs web search and returns titles, snippets, and URLs. Uses DuckDuckGo by default (no API key required). Falls back automatically to DuckDuckGo if SerpAPI is configured but fails.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | str or list[str] | Yes | — | Search query or list of queries |
| `top_n` | int | No | `5` | Max results per query |
| `provider` | str | No | `duckduckgo` | `duckduckgo` or `serpapi` |
| `timeout` | int | No | `15` | HTTP timeout in seconds |

**Output**

```python
{
    "status": "success",
    "results": [
        {
            "title": "Apple Reports First Quarter Results",
            "snippet": "Apple today announced financial results for its fiscal 2025 first quarter...",
            "url": "https://www.apple.com/newsroom/...",
            "source_query": "Apple earnings Q1 2025"
        },
        # ...
    ]
}
```

When `query` is a list, all results are merged into a single flat list with the originating query preserved in `source_query`.

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `TRELLIS_SEARCH_PROVIDER` | `duckduckgo` | Default provider |
| `TRELLIS_SEARCH_TOP_N` | `5` | Default result count |
| `TRELLIS_SEARCH_TIMEOUT` | `15` | Default HTTP timeout |
| `SERPAPI_API_KEY` | — | Required for SerpAPI provider |

**Minimal example**

```yaml
- id: search
  tool: search_web
  inputs:
    query: "{{params.topic}}"
    top_n: 10
```

---

## compute

Invokes a named deterministic function from the `FunctionRegistry`. This is the DSL surface for all codeable, side-effect-free computations: date arithmetic, currency normalization, fiscal period resolution, financial ratios, etc. The function body lives in the operator's registry — the pipeline only names it.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `function` | str | Yes | — | Registered function name |
| `**kwargs` | any | No | — | Forwarded to the function as keyword arguments |

**Output** — whatever the registered function returns (depends on the function)

The built-in `FunctionRegistry` is populated by `build_finance_registry()`. See the [Extensibility](extensibility-index.md) guide for how to add functions.

**Minimal example**

```yaml
- id: resolve_periods
  tool: compute
  inputs:
    function: fiscal_period_logic
    as_of_date: "{{params.period_end}}"
    company: "{{params.company}}"
```

---

## store

Persists a value to the session blackboard under a named key. The actual persistence happens in the DAG executor, which writes to the `Blackboard` after `store` returns — the value is then visible to all subsequent tasks in the same pipeline via `{{session.key}}`.

In a multi-pipeline `Plan`, values written by `store` in one sub-pipeline are available as `{{session.key}}` in any later sub-pipeline that declares the key in its `reads` list.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `key` | str | Yes | — | Blackboard key to write under |
| `value` | any | Yes | — | Value to persist; can be any Python object |
| `append` | bool | No | `False` | Append to an existing list under this key instead of replacing |

**Output**

```python
{
    "status": "success",
    "key": "extraction_result",
    "append": False,
    "value": { ... }                    # echoes back the stored value
}
```

**Minimal example**

```yaml
- id: persist
  tool: store
  inputs:
    key: extraction_result
    value: "{{extract.output}}"
```

Keys can be parameterized:

```yaml
- id: persist
  tool: store
  inputs:
    key: "{{params.ticker}}_{{params.period_end}}_result"
    value: "{{extract.output}}"
```

---

## export

Writes any pipeline value to disk in a choice of formats. Output goes to `TRELLIS_OUTPUT_DIR` (default: `outputs/`) unless overridden. JSON strings from `llm_job` are parsed automatically before writing.

**Inputs**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `data` | any | No* | — | Content to export (takes precedence over `content` if both supplied) |
| `content` | any | No* | — | Alias for `data` |
| `format` | str | No | `markdown` | `json`, `markdown`, `csv`, `xlsx` |
| `filename` | str | No | `artifact` | Base filename without extension |
| `output_dir` | str | No | `outputs` | Directory to write into (created if absent) |
| `schema` | `SchemaHandle` | No | `None` | When provided, validates conformance before writing |
| `strict` | bool | No | `False` | Raise on extra fields when `schema` is supplied |
| `metadata` | dict | No | `None` | Document header fields for Markdown export (`company`, `ticker`, `currency`, `units`, `audited`, `source_filing`, `filed`) |
| `periods` | list | No | `None` | Period label dicts (`{"label": "FY2024"}`) used as Markdown column headers |
| `analyst_notes` | list or str | No | `None` | Notes appended as an Analyst Notes table in Markdown output |

*At least one of `data` or `content` should be supplied.

**Output**

```python
{
    "status": "success",
    "format": "json",
    "filename": "AAPL_extraction",
    "path": "/absolute/path/to/outputs/AAPL_extraction.json",
    "size": 512,
    "schema_source": "income_statement.json"   # only when schema= was supplied
}
```

**Environment variables**

| Variable | Default | Effect |
|---|---|---|
| `TRELLIS_OUTPUT_DIR` | `outputs` | Default output directory |

**Minimal example**

```yaml
- id: write
  tool: export
  inputs:
    data: "{{extract.output}}"
    format: json
    filename: "{{params.ticker}}_result"
```

**Markdown export with metadata**

```yaml
- id: write_md
  tool: export
  inputs:
    data: "{{extract.output}}"
    format: markdown
    filename: income_statement
    metadata:
      company: "Apple Inc."
      ticker: AAPL
      currency: USD
      units: millions
      audited: true
    periods:
      - label: "FY2024"
      - label: "FY2023"
```

---

## Registry mechanics

### How tools are discovered

`AsyncToolRegistry.discover_impls()` scans every module under `trellis.tools.impls`, finds all `BaseTool` subclasses, instantiates them with default arguments, and registers them. The `build_default_registry()` factory calls this and then re-registers `ComputeTool` with the built-in `FunctionRegistry`:

```python
from trellis.tools.registry import build_default_registry

registry = build_default_registry()
print(registry.registered_tools())
# ['compute', 'export', 'extract_chart', 'extract_fields', 'extract_from_tables',
#  'extract_from_texts', 'fetch_data', 'ingest_document', 'llm_job', 'load_schema',
#  'mock', 'search_web', 'select', 'store', ...]
```

The `Orchestrator` calls `build_default_registry()` on construction, so all built-in tools are available automatically.

### How invocation works

When the executor runs a task, it resolves the task's template inputs and calls `registry.invoke(task.tool, resolved_inputs)`. Internally:

- If the tool's `execute()` method is a coroutine function, it is `await`-ed directly.
- If it is a synchronous function, it is run in a worker thread via `asyncio.to_thread()` so it does not block the event loop.

This means all tools — sync or async — are safe to run concurrently inside a wave.

### Registering a custom tool

Subclass `BaseTool` and pass an instance to `registry.register_tool()`:

```python
from trellis.tools.base import BaseTool, ToolInput, ToolOutput
from trellis.tools.registry import build_default_registry
from trellis.execution.orchestrator import Orchestrator
from typing import Any, Dict

class MyTool(BaseTool):
    def __init__(self):
        super().__init__("my_tool", "Does something custom")

    def execute(self, text: str, *, multiplier: int = 1, **kwargs) -> Dict[str, Any]:
        return {"result": text * multiplier}

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "text":       ToolInput("text",       "Input text",        required=True),
            "multiplier": ToolInput("multiplier", "Repeat count",      required=False, default=1),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput("result", "Repeated text", type_="string")


# Wire it into the orchestrator
registry = build_default_registry()
registry.register_tool(MyTool())

orch = Orchestrator(registry=registry)
```

The tool name (`"my_tool"`) is what you use in the DSL `tool:` field. Note that `KNOWN_TOOLS` in `trellis/models/pipeline.py` is the set of names that pass validation — add your tool name there, or disable validation for your custom pipelines.

### Registering a plain callable

For lightweight tools that don't need `BaseTool` metadata, register any callable directly:

```python
async def my_async_tool(query: str, **kwargs) -> dict:
    return {"answer": f"result for {query}"}

registry.register_callable("my_async_tool", my_async_tool)
```

Sync and async callables are both supported — the registry detects `iscoroutinefunction` at invoke time.
