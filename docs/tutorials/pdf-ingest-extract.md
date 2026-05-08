# PDF Ingest, Page Selection, and Extraction

Load a PDF from disk or a URL, select the pages that matter, extract structured content using an LLM, and produce a concise summary. Works with native-text PDFs and scanned documents (OCR is applied automatically to image-only pages).

**Tools used:** `ingest_document`, `select`, `extract_from_texts`, `llm_job`  
**Requires:** LLM API key (`OPENAI_API_KEY` or equivalent)

---

## The pipeline

```yaml title="pipelines/pdf_ingest_extract.yaml"
pipeline:
  id: pdf_ingest_extract
  goal: "Ingest '{{params.pdf_path}}', extract key content, and summarize"

  params:
    pdf_path:
      type: string
      description: "File path or HTTPS URL to the PDF"
    extraction_prompt:
      type: string
      description: "What to extract from the selected pages"
      default: "Extract the main topics, key facts, and any notable figures or metrics"
    page_selection:
      type: string
      description: "Natural-language page selector (e.g. 'pages 1 to 5', 'executive summary')"
      default: "the first 10 pages"
    model:
      type: string
      description: "LiteLLM model string for extraction and summarization"
      default: "openai/gpt-4o"

  tasks:
    - id: ingest
      tool: ingest_document
      inputs:
        path: "{{params.pdf_path}}"
        model: "{{params.model}}"   # used only if OCR is needed

    - id: select_pages
      tool: select
      inputs:
        document: "{{ingest.output}}"
        prompt: "{{params.page_selection}}"

    - id: extract
      tool: extract_from_texts
      inputs:
        document: "{{select_pages.output}}"
        prompt: "{{params.extraction_prompt}}"
        model: "{{params.model}}"

    - id: summarize
      tool: llm_job
      inputs:
        prompt: |
          Summarize the following extracted content in 5 concise bullet points
          suitable for a busy reader. Focus on concrete facts and figures;
          avoid vague language.

          {{extract.output}}
        temperature: 0.3
        max_tokens: 300
        model: "{{params.model}}"
```

### How it works

| Task | What it does |
|---|---|
| `ingest` | Loads the file or URL, parses pages (text layer or rasterized), OCRs any scanned pages |
| `select_pages` | Passes the selection prompt to an LLM which identifies the relevant page numbers; returns a `PageList` |
| `extract` | Sends the selected pages to an LLM with the extraction prompt; returns a `TextExtractionResult` containing an `extracted` dict |
| `summarize` | Receives the extraction output as context and writes a brief summary string |

**Dependencies inferred from templates:**
`select_pages` → `ingest`, `extract` → `select_pages`, `summarize` → `extract`.  
Each task runs in its own wave; total waves = 4.

---

## Run it — Python SDK

```python
import asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

async def main():
    pipeline = Pipeline.from_yaml_file("pipelines/pdf_ingest_extract.yaml")
    orch = Orchestrator()

    result = await orch.run_pipeline(
        pipeline,
        params={
            "pdf_path": "https://www.princexml.com/samples/newsletter/drylab.pdf",
            "page_selection": "the first 5 pages",
            "extraction_prompt": "Extract the main topics, key announcements, and any metrics",
            "model": "openai/gpt-4o",
        },
    )

    extraction = result.outputs["extract"]
    summary    = result.outputs["summarize"]

    print("Pages processed:", extraction.source_pages)
    print("Extracted fields:", extraction.extracted)
    print("\nSummary:\n", summary)

asyncio.run(main())
```

**Tip:** `ingest_document` also accepts a `DocumentHandle` or the raw output dict of `fetch_data`, so you can chain SEC filing fetch → ingest in a single pipeline (see [SEC Filing Extraction](sec-field-extraction.md)).

---

## Run it — CLI

```bash
trellis run pipelines/pdf_ingest_extract.yaml \
  --params '{
    "pdf_path": "https://www.princexml.com/samples/newsletter/drylab.pdf",
    "page_selection": "the first 5 pages"
  }'
```

---

## Expected output

```python
# result.outputs["ingest"]  — a DocumentHandle (serialized in CLI/API JSON)
{
    "source": "https://www.princexml.com/samples/newsletter/drylab.pdf",
    "format": "PDF",
    "page_count": 8,
    "is_scanned": False,
    "pages": [
        {"number": 1, "text": "DRY LAB...", "is_scanned": False},
        # ...
    ]
}

# result.outputs["select_pages"]  — a PageList
{
    "parent_source": "https://...drylab.pdf",
    "pages": [
        {"number": 1, "text": "..."},
        {"number": 2, "text": "..."},
        # ...
    ],
    "selector_prompt": "the first 5 pages"
}

# result.outputs["extract"]  — a TextExtractionResult
{
    "extracted": {
        "main_topics": ["dry lab techniques", "PCR optimization", "gel electrophoresis"],
        "key_figures": ["95% PCR efficiency", "12 protocol variants tested"],
        "notable_announcements": ["New thermocycler protocol released in March"]
    },
    "source_pages": [1, 2, 3, 4, 5],
    "sources": ["https://...drylab.pdf"],
    "prompt": "Extract the main topics, key announcements, and any metrics",
    "model": "openai/gpt-4o"
}

# result.outputs["summarize"]  — a plain string from the LLM
(
    "- DRY LAB focuses on optimizing PCR and gel electrophoresis protocols\n"
    "- 12 protocol variants were tested, achieving up to 95% PCR efficiency\n"
    "- A new thermocycler protocol was released in March for improved reproducibility\n"
    "- The newsletter targets molecular biology lab practitioners\n"
    "- No major product announcements; content is primarily technical guidance"
)
```

---

## Working with scanned PDFs

For image-only documents (where the text layer is absent or unreadable), `ingest_document` rasterizes each page and sends it to a vision-capable LLM for OCR. No extra configuration is needed — the tool detects scanned pages automatically.

```yaml
- id: ingest
  tool: ingest_document
  inputs:
    path: "./reports/annual_report_2023.pdf"
    model: "openai/gpt-4o"    # vision model for OCR
```

Set a vision-capable model to ensure quality on image-heavy documents. The `model` input is ignored for native-text pages.

---

## Selecting pages explicitly

When you know exactly which pages you need, pass them as a list instead of a prompt:

```yaml
- id: select_pages
  tool: select
  inputs:
    document: "{{ingest.output}}"
    pages: [3, 4, 5, 12]    # 1-based page numbers
```

Explicit selection skips the LLM call and is faster and cheaper.

---

## Processing multiple PDFs

Fan out over a list of paths using `parallel_over`:

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

`result.outputs["ingest_all"]` is a list of `DocumentHandle` objects in the same order as `pdf_paths`.

---

## Next steps

- [SEC Filing Extraction](sec-field-extraction.md) — fetch a filing from EDGAR and extract typed fields against a schema
- [Exporting Results](export-results.md) — write extraction output to JSON, Markdown, or CSV
