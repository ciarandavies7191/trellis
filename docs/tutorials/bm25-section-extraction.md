# BM25 Section Extraction

Extract structured fields from a specific section of a large document using keyword-based BM25 retrieval — without reading every page with an LLM.

**Tools used:** `ingest_document` → `select` (BM25 mode) → `extract_from_texts` → `export`  
**Requires:** LLM API key (for `extract_from_texts` only)  
**Time:** ~10 minutes

---

## The problem with page-level selection

The default `select` mode (NL prompt, `granularity: page`) works by asking an LLM to scan a short inventory of every page — page number plus the first 300 characters of text. For most short documents this is fine. For long filings it breaks down:

- A 200-page HTML 10-K has cover pages, risk factors, and table-of-contents entries that dominate the early snippet for many pages.
- The section you want may start mid-page, past the snippet window.
- The LLM may confuse section headings that appear in passing references.

`granularity: chunk` solves this by building a BM25 index over the document's structural chunks — every heading, prose paragraph, and table — and scoring them against your query terms. Pages with highly-scoring chunks are returned. No LLM page scan, no snippet truncation, no context-window pressure.

---

## How BM25 chunk selection works

When `granularity: chunk` is set on a `select` task:

1. **Chunking** — the document is split into structural chunks per page: headings, prose blocks (≤512 tokens each), table rows, and footnotes.
2. **BM25 indexing** — all chunks from the document are indexed in-memory using [rank-bm25](https://pypi.org/project/rank-bm25/).
3. **Retrieval** — the `prompt` field is treated as a keyword query. The top-`top_k` chunks are ranked by BM25 score.
4. **Page mapping** — the page numbers of the matched chunks are collected, deduplicated, and returned as a `PageList`.
5. **Downstream compatibility** — the output is a plain `PageList`, identical to what the default mode returns. `extract_from_texts` and `extract_fields` work unchanged.

The BM25 index is built on-the-fly from the ingested document — no pre-indexing or infrastructure needed.

---

## The pipeline

```yaml title="examples/pipelines/bm25_field_extraction.yaml"
pipeline:
  id: bm25_field_extraction

  tasks:

    - id: ingest
      tool: ingest_document
      inputs:
        path: "https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm"

    - id: select_section
      tool: select
      inputs:
        document: "{{ingest.output}}"
        granularity: chunk
        prompt: "Human Capital Resources employees workforce headcount office cities attrition diversity"
        top_k: 10

    - id: extract
      tool: extract_from_texts
      inputs:
        document: "{{select_section.output}}"
        prompt: |
          Extract every workforce and human capital fact stated in the document.
          Return a JSON object with the following fields (null if not stated):

          - total_employees: integer
          - as_of_date: string (ISO date, e.g. "2025-12-31")
          - office_cities: integer
          - full_time_employees: integer or null
          - part_time_employees: integer or null
          - female_employees_pct: number or null
          - underrepresented_minorities_pct: number or null
          - voluntary_attrition_pct: number or null
          - source_quote: string — exact sentence(s) stating employee count and office cities

    - id: export_result
      tool: export
      inputs:
        data: "{{extract.output.extracted}}"
        format: json
        filename: "meta_2025_workforce"
```

### Execution waves

| Wave | Tasks | Why concurrent |
|---|---|---|
| 1 | `ingest` | Network fetch + parse; no dependencies |
| 2 | `select_section` | Needs `ingest` output; builds BM25 in-memory |
| 3 | `extract` | Needs `select_section` output; LLM call |
| 4 | `export_result` | Needs `extract` output |

---

## Run it — CLI

```bash
trellis run examples/pipelines/bm25_field_extraction.yaml
```

---

## Run it — Python SDK

```python
import asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

async def main():
    pipeline = Pipeline.from_yaml_file("examples/pipelines/bm25_field_extraction.yaml")
    orch = Orchestrator()

    result = await orch.run_pipeline(pipeline)

    # The BM25 selection narrows the 200-page filing to a handful of pages.
    # The LLM only ever sees those pages.
    selection = result.outputs["select_section"]
    print(f"Pages selected: {[p['number'] for p in selection['pages']]}")

    extracted = result.outputs["extract"]["extracted"]
    print("\nExtracted workforce facts:")
    for field, value in extracted.items():
        print(f"  {field}: {value}")

    export = result.outputs["export_result"]
    print(f"\nJSON written to: {export['path']}  ({export['size']} bytes)")

asyncio.run(main())
```

### Build a BM25 index directly (no pipeline)

If you want to use the `select` tool programmatically — for example to inspect scores or reuse the index across multiple queries — construct it directly:

```python
from trellis.tools.impls.document import IngestDocumentTool
from trellis.tools.impls.select import SelectTool

# Ingest
ingest_tool = IngestDocumentTool()
handle = ingest_tool.execute(
    path="examples/data/MET_Annual_Report_FY_2025.pdf"
)

# BM25 selection — builds the index on first call
select_tool = SelectTool()
pages = select_tool.execute(
    document=handle,
    granularity="chunk",
    prompt="investment returns endowment performance asset allocation",
    top_k=8,
)

print(f"Selected {len(pages.pages)} pages: {[p.number for p in pages.pages]}")
# → Selected 3 pages: [14, 15, 16]

# Pass directly to extract_from_texts
from trellis.tools.impls.extract import ExtractFromTextsTool

extract_tool = ExtractFromTextsTool()
result = extract_tool.execute(
    document=pages,
    prompt="Extract total endowment value, annual return percentage, and asset allocation breakdown.",
)
print(result.extracted)
```

---

## Expected output

```python
# result.outputs["select_section"]  — PageList
{
    "parent_source": "https://www.sec.gov/Archives/edgar/data/.../meta-20251231.htm",
    "parent_format": "HTML",
    "pages": [
        {"number": 28, "text": "Human Capital Resources\nWe had a global workforce..."},
        {"number": 29, "text": "...continued..."},
    ],
    "selector_prompt": "[chunk-fallback-passthrough]"
}

# result.outputs["extract"]["extracted"]
{
    "total_employees": 78865,
    "as_of_date": "2025-12-31",
    "office_cities": 100,
    "full_time_employees": null,
    "part_time_employees": null,
    "female_employees_pct": null,
    "underrepresented_minorities_pct": null,
    "voluntary_attrition_pct": null,
    "source_quote": "We had a global workforce of 78,865 employees as of December 31, 2025, ..."
}

# result.outputs["export_result"]
{
    "status": "success",
    "format": "json",
    "filename": "meta_2025_workforce",
    "path": "/absolute/path/to/outputs/meta_2025_workforce.json",
    "size": 512
}
```

---

## Choosing `top_k`

`top_k` controls how many chunks are retrieved. More chunks means more pages in the `PageList`, which gives the LLM more context but increases token cost.

| Document type | Recommended `top_k` |
|---|---|
| Single focused section (2–4 pages) | 5–8 |
| Section with sub-sections or tables | 8–15 |
| Multiple related sections | 15–25 |

If a section spans many pages or the keywords appear in both headings and body text, a higher `top_k` ensures surrounding context pages are included.

---

## Choosing the query prompt

The `prompt` in chunk mode is a **keyword query**, not an instruction to the LLM. Write it as a dense list of terms that appear in the target section:

```yaml
# Good — terms that appear in the section text
prompt: "Human Capital Resources employees workforce headcount office cities attrition diversity"

# Less effective — natural language instruction (works but wastes query budget on stopwords)
prompt: "Select only the pages that contain information about employees and workforce"
```

BM25 tokenizes and scores on term frequency, so including synonyms and related domain terms (e.g. `headcount`, `workforce`, `employees`) boosts recall.

---

## When to use each selection mode

| Mode | Inputs | Best for |
|---|---|---|
| `granularity: page` (default) | NL `prompt` | Short documents; sections with distinctive first-paragraph text |
| `granularity: chunk` | Keyword `prompt`, `top_k` | Long documents; sections that start mid-page; when LLM page scan is unreliable |
| `pages: [3, 5, 12]` | Explicit page list | Known page numbers; deterministic pipelines |

---

## Next steps

- [SEC Filing Field Extraction](sec-field-extraction.md) — schema-bound extraction with `extract_fields` and typed validation
- [PDF Ingest, Page Selection, and Extraction](pdf-ingest-extract.md) — LLM-based page selection for shorter documents
- [Exporting Results](export-results.md) — write extracted data to Markdown, CSV, or XLSX
