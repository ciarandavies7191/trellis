# Installation

Trellis requires **Python 3.12 or later** and works on Linux, macOS, and Windows.

---

## Install with pip

```bash
pip install trellis
```

This installs the core runtime: the pipeline executor, tool registry, blackboard, and models. The CLI, API server, and MCP adapter are available as optional extras (see below).

---

## Install with uv (recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package manager that resolves and installs dependencies significantly faster than pip.

```bash
uv pip install trellis
```

Or add it to a project:

```bash
uv add trellis
```

---

## Optional extras

Install additional components depending on how you plan to use Trellis.

| Extra | Installs | When to use |
|---|---|---|
| `cli` | `typer`, `rich` | `trellis validate` / `trellis run` commands |
| `api` | `fastapi`, `uvicorn` | REST API server (`trellis_api`) |
| `mcp` | *(none currently)* | MCP server adapter (`trellis_mcp`) |
| `dev` | testing, linting, type-checking tools | Contributing or running the test suite |

Install one or more extras with brackets:

```bash
# CLI only
pip install "trellis[cli]"

# API server
pip install "trellis[api]"

# CLI + API together
pip install "trellis[cli,api]"

# Everything including dev tooling
pip install "trellis[cli,api,dev]"
```

With uv:

```bash
uv pip install "trellis[cli,api]"
```

---

## Development install (editable)

Clone the repository and install in editable mode so changes to source are reflected immediately:

```bash
git clone https://github.com/ciarandavies7191/trellis.git
cd trellis
pip install -e ".[cli,api,dev]"
```

With uv:

```bash
git clone https://github.com/ciarandavies7191/trellis.git
cd trellis
uv pip install -e ".[cli,api,dev]"
```

---

## System dependencies

Some tools have system-level requirements:

**PDF ingestion (`ingest_document`, `select`)**
Trellis uses [PyMuPDF](https://pymupdf.readthedocs.io/) for PDF parsing. PyMuPDF ships pre-built wheels for Linux, macOS, and Windows — no system libraries are needed in most cases.

If you are on a minimal Linux container and see errors loading `libmupdf`, install the build dependencies:

```bash
# Debian/Ubuntu
apt-get install -y libmupdf-dev
```

**Image OCR**
OCR features (`extract_chart`, image-only PDF pages) depend on the LLM provider you configure rather than a local OCR library. No additional system packages are required.

---

## LLM provider credentials

Trellis delegates LLM calls to [LiteLLM](https://docs.litellm.ai/). You need credentials for at least one provider to use `llm_job`, `extract_fields`, or `extract_from_texts`.

Set the relevant environment variable before running a pipeline, or put it in a `.env` file in your working directory:

```bash
# OpenAI
export OPENAI_API_KEY=sk-...

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Ollama (local)
export OLLAMA_HOST=http://localhost:11434
```

Trellis loads `.env` automatically when you use the CLI (`trellis run`). See [Configuration & Environment](operations-configuration.md) for the full list of variables.

---

## Verify the installation

After installing, check that the CLI is available:

```bash
trellis --help
```

Expected output:

```
Usage: trellis [OPTIONS] COMMAND [ARGS]...

  Trellis CLI — validate and run pipelines

Options:
  --help  Show this message and exit.

Commands:
  run       Run a pipeline or plan YAML with options.
  validate  Validate a pipeline or plan YAML file and print basic stats.
```

Validate one of the bundled example pipelines:

```bash
trellis validate examples/pipelines/single_mock.yaml
```

Expected output:

```
Pipeline is valid.
Stats:
{
  "id": "single_mock",
  ...
}
```

---

## Next steps

- Follow the [Quickstart](quickstart.md) to run your first pipeline end-to-end.
- Browse [example pipelines](https://github.com/ciarandavies7191/trellis/tree/main/examples/pipelines) for common patterns.
- Read the [Pipeline DSL reference](PIPELINE-DSL.md) to understand the full task syntax.
