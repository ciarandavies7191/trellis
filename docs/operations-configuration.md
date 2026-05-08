# Configuration & Environment

Trellis is configured entirely through environment variables. There are no config files to manage — every tool reads its defaults from the environment at startup, and the CLI and API expose per-run overrides that set env vars for the duration of that run only.

---

## Setting variables

**Shell export** — available to all processes in the session:

```bash
export OPENAI_API_KEY=sk-...
export EXTRACT_TEXT_MODEL=openai/gpt-4o
trellis run pipelines/extract.yaml --params '...'
```

**`.env` file** — loaded automatically from `./.env` if it exists, or explicitly via `--env-file`:

```bash
# .env
OPENAI_API_KEY=sk-...
EXTRACT_TEXT_MODEL=openai/gpt-4o
SEC_USER_AGENT=MyOrg/1.0 (ops@myorg.com)
```

```bash
trellis run pipelines/extract.yaml --env-file .env.production --params '...'
```

The `.env` loader uses `override=False` — variables already set in the shell take precedence over the file.

---

## LLM provider keys

These are read directly by [LiteLLM](https://github.com/BerriAI/litellm), which Trellis uses for all model calls.

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OLLAMA_HOST` | Ollama server base URL (default: `http://localhost:11434`) |

LiteLLM supports additional providers (Azure, Bedrock, Cohere, etc.) via their own env vars — see the LiteLLM docs for the full list.

---

## Model selection

Trellis tools use a layered model resolution. Each tool checks a specific override first, then falls back to the shared `EXTRACT_TEXT_MODEL`, then to a hard-coded default.

### `llm_job`

| Variable | Default | Description |
|---|---|---|
| `TRELLIS_LLM_MODEL` | `openai/gpt-4o-mini` | Default model for `llm_job` tasks |

### Extract, select, and OCR tools

All LLM-backed tools share `EXTRACT_TEXT_MODEL` as a common fallback. Set this one variable to switch the model for all tools at once:

| Variable | Default | Used by |
|---|---|---|
| `EXTRACT_TEXT_MODEL` | `openai/gpt-4o` | Fallback for all tools below |
| `EXTRACT_MODEL` | → `EXTRACT_TEXT_MODEL` | `extract_from_texts`, `extract_from_tables`, `extract_chart` |
| `EXTRACT_FIELDS_MODEL` | → `EXTRACT_TEXT_MODEL` | `extract_fields` |
| `SELECT_MODEL` | → `EXTRACT_TEXT_MODEL` | `select` |
| `INGEST_OCR_MODEL` | → `EXTRACT_TEXT_MODEL` | OCR pass inside `ingest_document` |

**Example — use GPT-4o for everything:**

```bash
EXTRACT_TEXT_MODEL=openai/gpt-4o trellis run pipelines/extract.yaml --params '...'
```

**Example — use a cheaper model for selection but GPT-4o for extraction:**

```bash
export EXTRACT_TEXT_MODEL=openai/gpt-4o
export SELECT_MODEL=openai/gpt-4o-mini
trellis run pipelines/sec_extraction.yaml --params '...'
```

**Example — use a local Ollama model:**

```bash
export OLLAMA_HOST=http://localhost:11434
export EXTRACT_TEXT_MODEL=ollama/llama3.1
trellis run pipelines/summarize.yaml --params '...'
```

All model strings follow the [LiteLLM routing format](https://docs.litellm.ai/docs/providers): `provider/model-name`.

---

## OCR tuning

`ingest_document` automatically OCRs pages where the native text layer is absent or sparse. These variables control when rasterization is triggered and at what quality:

| Variable | Default | Description |
|---|---|---|
| `PYMUPDF_RASTERIZE_COVERAGE_THRESHOLD` | `0.25` | Image-pixel coverage ratio at or above which a page is rasterized for OCR |
| `PYMUPDF_RASTERIZE_DPI` | `150` | Resolution (DPI) used when converting PDF pages to PNG for OCR |
| `EXTRACT_MIN_NATIVE_CHARS` | `80` | Native character count below which OCR is preferred over the text layer |
| `EXTRACT_IMAGE_COVERAGE_THRESHOLD` | `0.25` | Image coverage fraction above which OCR is preferred |

For high-fidelity scanned documents, increase DPI:

```bash
PYMUPDF_RASTERIZE_DPI=300 trellis run pipelines/ingest_scanned.yaml --params '...'
```

---

## Web search

| Variable | Default | Description |
|---|---|---|
| `TRELLIS_SEARCH_PROVIDER` | `duckduckgo` | Default search backend (`duckduckgo` or `serpapi`) |
| `SERPAPI_API_KEY` | — | SerpAPI key; setting this enables Google-backed search |
| `TRELLIS_SEARCH_TOP_N` | `5` | Default number of results returned per query |
| `TRELLIS_SEARCH_TIMEOUT` | `15` | HTTP timeout in seconds for search requests |
| `TRELLIS_USER_AGENT` | `Trellis/0.1 (...)` | `User-Agent` header sent with search requests |

When `SERPAPI_API_KEY` is set and `provider` is `serpapi`, the `search_web` tool uses Google results via SerpAPI. Otherwise it falls back to DuckDuckGo HTML scraping (no key required).

---

## SEC EDGAR

| Variable | Default | Description |
|---|---|---|
| `SEC_USER_AGENT` | `Trellis/0.1 (contact@example.com)` | `User-Agent` sent to SEC EDGAR — EDGAR requires a contact email |
| `SEC_THROTTLE_SECONDS` | `0.2` | Minimum delay between EDGAR requests to respect rate limits |

SEC EDGAR's fair-access policy requires a descriptive `User-Agent` with a contact address. Set this to your organisation's details:

```bash
SEC_USER_AGENT="MyOrg Research/1.0 (data-eng@myorg.com)"
```

---

## Export

| Variable | Default | Description |
|---|---|---|
| `TRELLIS_OUTPUT_DIR` | `outputs` | Default output directory used by the `export` tool when `output_dir` is not specified in the task |

---

## API server

| Variable | Default | Description |
|---|---|---|
| `TRELLIS_API_HOST` | `127.0.0.1` | Bind address for the FastAPI server |
| `TRELLIS_API_PORT` | `8000` | Port for the FastAPI server |

---

## Per-run overrides (CLI)

The CLI can set any LLM variable for a single run without modifying the environment. These flags translate directly to `os.environ` assignments before the pipeline executes and have no effect on subsequent runs:

| Flag | Variable set | Example |
|---|---|---|
| `--openai-api-key KEY` | `OPENAI_API_KEY` | `--openai-api-key sk-...` |
| `--openai-model NAME` | `OPENAI_MODEL` | `--openai-model gpt-4o` |
| `--anthropic-api-key KEY` | `ANTHROPIC_API_KEY` | `--anthropic-api-key sk-ant-...` |
| `--anthropic-model NAME` | `ANTHROPIC_MODEL` | `--anthropic-model claude-3-haiku-20240307` |
| `--ollama-host URL` | `OLLAMA_HOST` | `--ollama-host http://localhost:11434` |
| `--ollama-model NAME` | `OLLAMA_MODEL` | `--ollama-model llama3` |
| `--llm-provider NAME` | `TRELLIS_LLM_PROVIDER` | `--llm-provider openai` |
| `--llm-model NAME` | `EXTRACT_TEXT_MODEL` | `--llm-model openai/gpt-4o` |
| `--extract-model NAME` | `EXTRACT_TEXT_MODEL` | `--extract-model openai/gpt-4o-mini` |

**Example — run one pipeline with a different model without touching `.env`:**

```bash
trellis run pipelines/summarize.yaml \
  --params '{"ticker": "AAPL", "period_end": "2024-09-30"}' \
  --openai-api-key $OPENAI_API_KEY \
  --llm-model openai/gpt-4o
```

---

## Per-run overrides (Python SDK)

The orchestrator itself has no model configuration — model selection happens inside each tool at invocation time via env vars. To vary the model per run from Python, set env vars before calling `run_pipeline`:

```python
import os, asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

os.environ["EXTRACT_TEXT_MODEL"] = "openai/gpt-4o"

pipeline = Pipeline.from_yaml_file("pipelines/extract.yaml")
orch = Orchestrator()
result = asyncio.run(orch.run_pipeline(pipeline, params={"ticker": "AAPL", "period_end": "2024-09-30"}))
```

---

## Complete variable reference

| Variable | Default | Scope |
|---|---|---|
| `OPENAI_API_KEY` | — | LiteLLM / all OpenAI tools |
| `ANTHROPIC_API_KEY` | — | LiteLLM / all Anthropic tools |
| `OLLAMA_HOST` | `http://localhost:11434` | LiteLLM / Ollama tools |
| `TRELLIS_LLM_MODEL` | `openai/gpt-4o-mini` | `llm_job` |
| `EXTRACT_TEXT_MODEL` | `openai/gpt-4o` | All extract, select, OCR tools |
| `EXTRACT_MODEL` | → `EXTRACT_TEXT_MODEL` | `extract_from_texts`, `extract_from_tables`, `extract_chart` |
| `EXTRACT_FIELDS_MODEL` | → `EXTRACT_TEXT_MODEL` | `extract_fields` |
| `SELECT_MODEL` | → `EXTRACT_TEXT_MODEL` | `select` |
| `INGEST_OCR_MODEL` | → `EXTRACT_TEXT_MODEL` | OCR in `ingest_document` |
| `PYMUPDF_RASTERIZE_DPI` | `150` | `ingest_document` OCR |
| `PYMUPDF_RASTERIZE_COVERAGE_THRESHOLD` | `0.25` | `ingest_document` OCR trigger |
| `EXTRACT_MIN_NATIVE_CHARS` | `80` | `ingest_document` OCR trigger |
| `EXTRACT_IMAGE_COVERAGE_THRESHOLD` | `0.25` | `ingest_document` OCR trigger |
| `SERPAPI_API_KEY` | — | `search_web` SerpAPI backend |
| `TRELLIS_SEARCH_PROVIDER` | `duckduckgo` | `search_web` default backend |
| `TRELLIS_SEARCH_TOP_N` | `5` | `search_web` result count |
| `TRELLIS_SEARCH_TIMEOUT` | `15` | `search_web` HTTP timeout (s) |
| `TRELLIS_USER_AGENT` | `Trellis/0.1 (...)` | `search_web` HTTP header |
| `SEC_USER_AGENT` | `Trellis/0.1 (...)` | `fetch_data` SEC EDGAR header |
| `SEC_THROTTLE_SECONDS` | `0.2` | `fetch_data` EDGAR rate limit delay |
| `TRELLIS_OUTPUT_DIR` | `outputs` | `export` default output dir |
| `TRELLIS_API_HOST` | `127.0.0.1` | FastAPI server bind address |
| `TRELLIS_API_PORT` | `8000` | FastAPI server port |

---

## Next steps

- [Execution Backends & Run Queue](operations-execution.md) — execution options, timeouts, retries, and the background queue
- [CLI reference](interfaces-cli.md) — full flag list including per-run overrides
- [API reference](interfaces-api.md) — server configuration and endpoint reference
