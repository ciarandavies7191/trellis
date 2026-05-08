# Tutorials & Examples

Guided walkthroughs that show complete, runnable pipelines from install through to output. Each tutorial uses the Python SDK and includes the YAML definition, the code to run it, and realistic expected outputs.

## Before you start

Install Trellis with the extras you need for these tutorials:

```bash
pip install "trellis[cli]"
```

Most tutorials call an LLM. Set at least one provider key before running them:

```bash
export OPENAI_API_KEY=sk-...        # OpenAI
export ANTHROPIC_API_KEY=sk-ant-... # Anthropic Claude
export OLLAMA_HOST=http://localhost:11434  # Ollama (local)
```

Trellis uses [LiteLLM](https://docs.litellm.ai/) internally, so any provider it supports works. Pass the model string as `"openai/gpt-4o"`, `"anthropic/claude-3-5-sonnet-20241022"`, or `"ollama/llama3"`.

---

## Tutorials

### [Web Search and Summarization](web-search-summarize.md)

Search the web for a topic and distil the results into a structured summary using an LLM.

**Tools:** `search_web` → `llm_job` → `store`  
**Requires:** LLM API key  
**Time:** ~5 minutes

---

### [PDF Ingest, Page Selection, and Extraction](pdf-ingest-extract.md)

Load a PDF from disk or URL, select the pages that matter, extract structured content, and summarize.

**Tools:** `ingest_document` → `select` → `extract_from_texts` → `llm_job`  
**Requires:** LLM API key  
**Time:** ~10 minutes

---

### [SEC Filing Field Extraction](sec-field-extraction.md)

Fetch a public 10-K or 10-Q from SEC EDGAR, ingest it, and extract typed financial fields against a declared schema. The full document-intelligence workflow end-to-end.

**Tools:** `fetch_data` → `ingest_document` → `select` → `load_schema` → `extract_fields` → `export`  
**Requires:** LLM API key  
**Time:** ~15 minutes

---

### [Exporting Results](export-results.md)

Take any pipeline output and write it to JSON, Markdown, CSV, or XLSX with optional schema conformance checks.

**Tools:** `export`  
**Requires:** Nothing (runs with `mock` data)  
**Time:** ~5 minutes

---

## Examples gallery

These pipelines ship in `examples/pipelines/` and can be run directly with `trellis run`:

| File | What it shows |
|---|---|
| `single_mock.yaml` | Minimal single-task pipeline |
| `dependency_chain.yaml` | Implicit task dependencies via `{{task_id.output.field}}` |
| `fan_out.yaml` | Parallel fan-out with `parallel_over` and `{{item}}` |
| `pipeline_inputs.yaml` | Runtime inputs with `{{pipeline.inputs.key}}` |
| `fetch_10k_parametrized.yaml` | Typed `params` block with required and optional params |
| `web_search_investor_day.yaml` | Fan-out web search across multiple years |
| `pdf_summarize.yaml` | PDF → extract → LLM summarize |
| `image_ocr_summarize.yaml` | Scanned PDF with OCR → select → extract → summarize |
| `extract_sec_field.yaml` | Full SEC extraction: schema + manual + fetch + extract + export |
