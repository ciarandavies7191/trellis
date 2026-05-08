# Exporting Results

The `export` tool writes any pipeline output to disk in a choice of formats. It works as the final task in any pipeline and is the standard way to produce files from an extraction or LLM workflow.

**Tools used:** `export`  
**Requires:** Nothing — the examples below use `mock` data so no LLM key is needed

---

## Supported formats

| `format` value | Output | Best for |
|---|---|---|
| `json` | Pretty-printed JSON file | Machine consumption, debugging |
| `markdown` | Markdown table | Human review, audit trail |
| `csv` | Comma-separated values | Spreadsheet import |
| `xlsx` | Excel workbook | Finance team handoff |

---

## Basic export — JSON

```yaml title="pipelines/export_json.yaml"
pipeline:
  id: export_json_example
  goal: "Extract data and write it to JSON"

  tasks:
    - id: produce_data
      tool: mock
      inputs:
        revenue: 391035
        gross_profit: 180683
        net_income: 93736
        eps_diluted: 6.08

    - id: write_json
      tool: export
      inputs:
        data: "{{produce_data.output.inputs}}"
        format: json
        filename: extraction_result
        output_dir: outputs
```

```python
import asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

async def main():
    pipeline = Pipeline.from_yaml_file("pipelines/export_json.yaml")
    orch = Orchestrator()
    result = await orch.run_pipeline(pipeline)

    export = result.outputs["write_json"]
    print(f"Written to: {export['path']}")   # outputs/extraction_result.json
    print(f"Size: {export['size']} bytes")

asyncio.run(main())
```

**Output file (`outputs/extraction_result.json`):**

```json
{
  "revenue": 391035,
  "gross_profit": 180683,
  "net_income": 93736,
  "eps_diluted": 6.08
}
```

**Task output:**

```python
{
    "status": "success",
    "format": "json",
    "filename": "extraction_result.json",
    "path": "/absolute/path/outputs/extraction_result.json",
    "size": 89
}
```

---

## Markdown export with metadata

Markdown export is useful for financial spreads — it renders the fields as a labelled table and prepends document metadata as a header block:

```yaml title="pipelines/export_markdown.yaml"
pipeline:
  id: export_markdown_example
  goal: "Export extraction result as a Markdown spread"

  params:
    ticker:
      type: string
      default: AAPL
    period_end:
      type: string
      default: "2024-09-30"

  tasks:
    - id: produce_data
      tool: mock
      inputs:
        revenue: 391035
        gross_profit: 180683
        operating_income: 123216
        net_income: 93736
        eps_diluted: 6.08

    - id: write_markdown
      tool: export
      inputs:
        data: "{{produce_data.output.inputs}}"
        format: markdown
        filename: "{{params.ticker}}_income_statement"
        output_dir: outputs
        metadata:
          company: Apple Inc.
          ticker: "{{params.ticker}}"
          period_end: "{{params.period_end}}"
          currency: USD
          units: millions
          audited: true
        periods:
          - label: "FY2024"
            period_end: "{{params.period_end}}"
```

**Output file (`outputs/AAPL_income_statement.md`):**

```markdown
| Field | Value |
| --- | --- |
| Company | Apple Inc. |
| Ticker | AAPL |
| Period End | 2024-09-30 |
| Currency | USD |
| Units | millions |
| Audited | true |

| Field | FY2024 |
| --- | --- |
| revenue | 391,035 |
| gross_profit | 180,683 |
| operating_income | 123,216 |
| net_income | 93,736 |
| eps_diluted | 6.08 |
```

---

## CSV and XLSX export

CSV and XLSX exports write a header row followed by one data row:

```yaml
- id: write_csv
  tool: export
  inputs:
    data: "{{extract.output}}"
    format: csv
    filename: extraction
    output_dir: outputs
```

```yaml
- id: write_xlsx
  tool: export
  inputs:
    data: "{{extract.output}}"
    format: xlsx
    filename: extraction
    output_dir: outputs
```

---

## Exporting multiple formats from one pipeline

Run export tasks in parallel since they depend on the same upstream data and have no dependency on each other:

```yaml title="pipelines/multi_format_export.yaml"
pipeline:
  id: multi_format_export
  goal: "Export extraction result to JSON, Markdown, and CSV simultaneously"

  tasks:
    - id: extract
      tool: mock
      inputs:
        revenue: 391035
        net_income: 93736
        eps_diluted: 6.08

    - id: export_json
      tool: export
      inputs:
        data: "{{extract.output.inputs}}"
        format: json
        filename: result
        output_dir: outputs

    - id: export_md
      tool: export
      inputs:
        data: "{{extract.output.inputs}}"
        format: markdown
        filename: result
        output_dir: outputs

    - id: export_csv
      tool: export
      inputs:
        data: "{{extract.output.inputs}}"
        format: csv
        filename: result
        output_dir: outputs
```

`export_json`, `export_md`, and `export_csv` all depend on `extract` and have no dependency on each other, so they run concurrently in wave 2.

```python
import asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

async def main():
    pipeline = Pipeline.from_yaml_file("pipelines/multi_format_export.yaml")
    orch = Orchestrator()
    result = await orch.run_pipeline(pipeline)

    for task_id in ("export_json", "export_md", "export_csv"):
        out = result.outputs[task_id]
        print(f"{out['format']:8}  →  {out['path']}")

asyncio.run(main())
```

```
json      →  /path/to/outputs/result.json
markdown  →  /path/to/outputs/result.md
csv       →  /path/to/outputs/result.csv
```

`result.waves_executed` is `2`: wave 1 runs `extract`, wave 2 runs the three exports concurrently.

---

## Chaining from extraction

In a real pipeline, replace the `mock` task with an `extract_fields` task. The output shape is the same — a flat dict of field name → value:

```yaml
- id: extract
  tool: extract_fields
  inputs:
    document: "{{select_pages.output}}"
    schema: "{{schema.output}}"
    period_end: "{{params.period_end}}"
    section_filter: face

- id: export_json
  tool: export
  inputs:
    data: "{{extract.output}}"    # same: flat dict of field values
    format: json
    filename: "{{params.ticker}}_extraction"
    output_dir: outputs
```

See [SEC Filing Extraction](sec-field-extraction.md) for the complete pipeline that includes the fetch, ingest, and select steps before this.

---

## Next steps

- [SEC Filing Extraction](sec-field-extraction.md) — the full extraction workflow that feeds into `export`
- [Pipeline DSL reference](../PIPELINE-DSL.md) — `parallel_over`, retries, `store`, and all other task options
