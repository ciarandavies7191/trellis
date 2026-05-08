# SEC Filing Field Extraction

Fetch a public 10-K or 10-Q from SEC EDGAR, ingest it, select the relevant pages, and extract typed financial fields against a declared schema — all in one pipeline. This is the full document-intelligence workflow that Trellis was built around.

**Tools used:** `load_schema`, `ingest_document`, `fetch_data`, `select`, `extract_fields`, `export`  
**Requires:** LLM API key (`OPENAI_API_KEY` or equivalent)

---

## Prerequisites

You need a field schema file. A schema is a JSON or YAML list of field definitions that tells `extract_fields` what to look for:

```json title="schemas/income_statement.json"
[
  { "name": "revenue",        "type_hint": "number", "section": "face" },
  { "name": "gross_profit",   "type_hint": "number", "section": "face" },
  { "name": "operating_income","type_hint": "number","section": "face" },
  { "name": "net_income",     "type_hint": "number", "section": "face" },
  { "name": "eps_diluted",    "type_hint": "number", "section": "face" }
]
```

Save this as `schemas/income_statement.json` next to your pipeline file.

---

## The pipeline

```yaml title="pipelines/sec_extraction.yaml"
pipeline:
  id: sec_extraction
  goal: >
    Fetch the {{params.company}} {{params.form_type}} for {{params.period_end}}
    from SEC EDGAR and extract income statement face fields.

  params:
    ticker:
      type: string
      description: "Stock ticker symbol (e.g. AAPL)"
    company:
      type: string
      description: "Full company name for the goal description"
    period_end:
      type: string
      description: "Period end date in YYYY-MM-DD format"
    period_type:
      type: string
      description: "annual or quarterly"
      default: annual
    form_type:
      type: string
      description: "SEC form type (10-K or 10-Q)"
      default: "10-K"
    schema_path:
      type: string
      description: "Path to the field schema JSON/YAML file"
      default: "schemas/income_statement.json"
    section_filter:
      type: string
      description: "Which schema section to extract (face, segments, footnotes)"
      default: face
    output_dir:
      type: string
      description: "Directory to write exported results"
      default: outputs

  tasks:
    # ── 1. Load schema and fetch filing in parallel ───────────────────────────
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

    # ── 2. Ingest the filing ──────────────────────────────────────────────────
    - id: ingest
      tool: ingest_document
      inputs:
        path: "{{fetch.output}}"

    # ── 3. Select the relevant pages ─────────────────────────────────────────
    - id: select_pages
      tool: select
      inputs:
        document: "{{ingest.output}}"
        prompt: >
          Select only the pages containing the consolidated income statement (profit
          and loss statement). Include the face of the income statement and any
          immediately following notes to it. Exclude: balance sheet, cash flow
          statement, shareholders' equity, cover pages, and risk factors.

    # ── 4. Extract fields against the schema ─────────────────────────────────
    - id: extract
      tool: extract_fields
      inputs:
        document: "{{select_pages.output}}"
        schema: "{{schema.output}}"
        period_end: "{{params.period_end}}"
        section_filter: "{{params.section_filter}}"

    # ── 5. Export to JSON ─────────────────────────────────────────────────────
    - id: export_json
      tool: export
      inputs:
        data: "{{extract.output}}"
        format: json
        filename: "{{params.ticker}}_{{params.period_end}}_extraction"
        output_dir: "{{params.output_dir}}"
```

### Execution waves

| Wave | Tasks | Why concurrent |
|---|---|---|
| 1 | `schema`, `fetch` | No inter-dependencies — both can start immediately |
| 2 | `ingest` | Needs `fetch` output |
| 3 | `select_pages` | Needs `ingest` output |
| 4 | `extract` | Needs `select_pages` and `schema` |
| 5 | `export_json` | Needs `extract` output |

`schema` and `fetch` run concurrently in wave 1, saving latency when the schema load and network fetch overlap.

---

## Run it — Python SDK

```python
import asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

async def main():
    pipeline = Pipeline.from_yaml_file("pipelines/sec_extraction.yaml")
    orch = Orchestrator()

    result = await orch.run_pipeline(
        pipeline,
        params={
            "ticker": "AAPL",
            "company": "Apple Inc.",
            "period_end": "2024-09-30",   # Apple FY2024 annual
            "period_type": "annual",
            "form_type": "10-K",
            "schema_path": "schemas/income_statement.json",
        },
    )

    extracted = result.outputs["extract"]
    export    = result.outputs["export_json"]

    print("Extracted fields:")
    for field, value in extracted.items():
        print(f"  {field}: {value}")

    print(f"\nJSON written to: {export['path']}")
    print(f"File size: {export['size']} bytes")

asyncio.run(main())
```

---

## Run it — CLI

```bash
trellis run pipelines/sec_extraction.yaml \
  --params '{
    "ticker": "AAPL",
    "company": "Apple Inc.",
    "period_end": "2024-09-30",
    "period_type": "annual"
  }'
```

---

## Expected output

```python
# result.outputs["fetch"]
{
    "status": "success",
    "source": "sec_edgar",
    "results": [
        {
            "company_name": "Apple Inc.",
            "ticker": "AAPL",
            "cik": "0000320193",
            "filings": [
                {
                    "form": "10-K",
                    "filing_date": "2024-11-01",
                    "accession_no": "0000320193-24-000123",
                    "url": "https://www.sec.gov/Archives/edgar/data/320193/..."
                }
            ]
        }
    ]
}

# result.outputs["schema"]  — a SchemaHandle
{
    "fields": [
        {"name": "revenue",         "type_hint": "number", "section": "face", "required": True},
        {"name": "gross_profit",    "type_hint": "number", "section": "face", "required": True},
        {"name": "operating_income","type_hint": "number", "section": "face", "required": True},
        {"name": "net_income",      "type_hint": "number", "section": "face", "required": True},
        {"name": "eps_diluted",     "type_hint": "number", "section": "face", "required": True},
    ],
    "source": "schemas/income_statement.json"
}

# result.outputs["extract"]  — field values keyed by schema field name
{
    "revenue":          391035,   # $M
    "gross_profit":     180683,
    "operating_income": 123216,
    "net_income":       93736,
    "eps_diluted":      6.08
}

# result.outputs["export_json"]
{
    "status": "success",
    "format": "json",
    "filename": "AAPL_2024-09-30_extraction.json",
    "path": "/absolute/path/to/outputs/AAPL_2024-09-30_extraction.json",
    "size": 312
}
```

!!! note "Units"
    `extract_fields` does not normalize units — the extracted values reflect what the LLM reads from the document. Add a `compute` task after extraction if you need explicit unit normalization (e.g., converting reported millions to dollars).

---

## Adding a spreading manual

For more accurate extraction on complex filings, pass a `rules` document containing your firm's extraction guidelines. The manual is injected as additional context alongside the filing pages:

```yaml
- id: ingest_manual
  tool: ingest_document
  inputs:
    path: "manuals/income_statement_rules.md"

- id: extract
  tool: extract_fields
  inputs:
    document: "{{select_pages.output}}"
    schema: "{{schema.output}}"
    rules: "{{ingest_manual.output}}"    # ← manual injected here
    period_end: "{{params.period_end}}"
    section_filter: "{{params.section_filter}}"
```

`ingest_manual` can run in parallel with `schema` and `fetch` in wave 1 since it has no dependencies on either.

---

## Extracting multiple periods

To extract current and prior period side-by-side, run two `extract_fields` tasks pointing at the same document with different `period_end` values:

```yaml
- id: extract_current
  tool: extract_fields
  inputs:
    document: "{{select_pages.output}}"
    schema: "{{schema.output}}"
    period_end: "{{params.period_end}}"
    section_filter: face

- id: extract_prior
  tool: extract_fields
  inputs:
    document: "{{select_pages.output}}"
    schema: "{{schema.output}}"
    period_end: "{{params.period_end_prior}}"
    section_filter: face
```

Both tasks depend only on `select_pages` and `schema`, so they run concurrently in the same wave.

---

## Next steps

- [Exporting Results](export-results.md) — write extraction output to Markdown tables, CSV, or XLSX
- [Pipeline DSL reference](../PIPELINE-DSL.md) — full task syntax, `await`, retries, fan-out
