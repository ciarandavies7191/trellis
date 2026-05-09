# Trellis

**Modular agentic pipeline system for document and data workflows.**

Trellis lets you define multi-step AI pipelines as declarative YAML — ingest PDFs, fetch SEC filings, call LLMs, extract structured fields, fan out over lists — and run them from the CLI, a REST API, or directly from Python.

**[Documentation](https://ciarandavies7191.github.io/trellis)** · [Installation](https://ciarandavies7191.github.io/trellis/installation) · [Pipeline DSL Reference](https://ciarandavies7191.github.io/trellis/PIPELINE-DSL) · [Examples](examples/pipelines/)

---

## Install

```bash
pip install trellis-pipelines
```

With [uv](https://github.com/astral-sh/uv):

```bash
uv add trellis-pipelines
```

Requires Python 3.12+. Set at least one LLM provider key before running pipelines:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, etc.
```

---

## Quickstart

### CLI

```bash
# Validate a pipeline file
trellis validate examples/pipelines/pdf_summarize.yaml

# Run a pipeline
trellis run examples/pipelines/pdf_summarize.yaml

# Pass runtime parameters
trellis run examples/pipelines/fetch_10k_parametrized.yaml \
  --params '{"ticker": "AAPL", "year": "2024"}'

# Override the LLM model for all tasks
trellis run examples/pipelines/pdf_summarize.yaml --model anthropic/claude-haiku-4-5
```

### A pipeline file

```yaml
pipeline:
  id: pdf_summarize
  goal: "Load a PDF and summarize key points"
  tasks:
    - id: ingest_pdf
      tool: ingest_document
      inputs:
        path: "https://example.com/report.pdf"

    - id: extract_content
      tool: extract_from_texts
      inputs:
        document: "{{ingest_pdf.output}}"
        prompt: "Extract the main topics and key metrics"

    - id: summarize
      tool: llm_job
      inputs:
        prompt: |
          Summarize in 5 bullet points for a busy executive:
          {{extract_content.output}}
        max_tokens: 256
```

Dependencies are inferred from `{{task_id.output}}` references — no explicit wiring needed.

### Python

```python
import asyncio
from trellis.models.pipeline import PipelineSpec
from trellis.execution.orchestrator import Orchestrator

spec = PipelineSpec.from_yaml("examples/pipelines/pdf_summarize.yaml")
orchestrator = Orchestrator()
result = asyncio.run(orchestrator.run_pipeline(spec))

print(result.outputs)
```

### REST API

```bash
# Start the server (requires trellis-pipelines[api])
pip install "trellis-pipelines[api]"
uvicorn trellis_api.main:app --reload
```

```bash
curl -X POST http://localhost:8000/pipelines/run \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": {
      "id": "hello",
      "goal": "Say hello",
      "tasks": [
        {
          "id": "greet",
          "tool": "llm_job",
          "inputs": { "prompt": "Say hello in one sentence." }
        }
      ]
    }
  }'
```

---

## Key features

- **Declarative YAML DSL** — flat task list, dependencies inferred from template references
- **Template resolution** — `{{task_id.output}}`, `{{params.key}}`, `{{session.key}}`, `{{item}}` (fan-out)
- **Fan-out / parallel_over** — scatter a task over a list, collect results automatically
- **`await` barriers** — explicit synchronization across parallel branches
- **Retry & backoff** — per-task `retry` with exponential backoff and jitter
- **Structured extraction** — `extract_fields` with JSON Schema, `extract_from_texts` for free-form
- **PDF + web ingestion** — `ingest_document` (PDF/HTML), `fetch_url`, `search_web`
- **Multi-tenancy** — tenant-scoped blackboard (`store` / `{{session.key}}`) for stateful workflows
- **CLI, REST API, and Python SDK** — three interfaces, one engine

---

## Project structure

```
trellis/            # Core: models, execution engine, tool registry
trellis_api/        # FastAPI REST server (optional extra: [api])
trellis_cli/        # Typer CLI
trellis_mcp/        # MCP server adapter (roadmap)
examples/           # Example pipelines and data
docs/               # MkDocs source
tests/              # Test suite
```

---

## Optional extras

| Extra | What it adds |
|---|---|
| `trellis-pipelines[api]` | FastAPI + uvicorn REST server |
| `trellis-pipelines[dev]` | pytest, ruff, mypy, black, isort |
| `trellis-pipelines[all]` | All extras |

---

## Documentation


- [Installation](https://ciarandavies7191.github.io/trellis/installation)
- [Quickstart](https://ciarandavies7191.github.io/trellis/quickstart)
- [Pipeline DSL Reference](https://ciarandavies7191.github.io/trellis/PIPELINE-DSL)
- [Tools & Registry](https://ciarandavies7191.github.io/trellis/tools-index)
- [CLI reference](https://ciarandavies7191.github.io/trellis/interfaces-cli)
- [REST API reference](https://ciarandavies7191.github.io/trellis/interfaces-api)

---

## License

MIT — see [LICENSE](LICENSE) or [https://opensource.org/license/mit](https://opensource.org/license/mit).
