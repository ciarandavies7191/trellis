# AGENTS.md

## Architecture Overview

Trellis is a modular agentic pipeline system with a shared core library (`trellis/`) powering three entry points: `trellis_api/` (FastAPI REST server), `trellis_cli/` (command-line interface), and `trellis_mcp/` (Model Context Protocol server). The core enforces separation of concerns with Pydantic models, validation, execution engine, and tool registry.

Key components:
- `models/pipeline.py`: Pipeline DSL as DAG of tasks with implicit dependencies via `{{task_id.output}}` templates
- `execution/template.py`: ResolutionContext (now includes `tenant_id` and a `blackboard` handle) and `resolve*` helpers
- `execution/blackboard.py`: Tenant-scoped Blackboard abstraction plus `InMemoryBlackboard` (swappable for Redis/Postgres/Prefect Blocks)
- `execution/dag.py`: Async DAG executor that resolves templates, supports fan-out (`parallel_over`), retries, per-task timeouts and execution stats; persists `store` tool writes to the tenant blackboard; shared state flows through `ResolutionContext`
- `execution/orchestrator.py`: High-level runner that builds a `ResolutionContext` (injects `tenant_id` and `blackboard`), discovers tools via the default async registry, runs `execute_pipeline`, and returns a structured `RunResult` (with `cancel()` support and optional event collection)
- `execution/run_queue.py`: In-memory background run manager (submit/get/cancel) used by the API for queued execution (fallback executor)
- `execution/prefect_adapter.py`: Prefect executor skeleton (pluggable execution backend; to be implemented)
- `tools/registry.py`: Async tool discovery/invocation; subclass `BaseTool` for new tools (e.g., `tools/impls/llm.py`). Use `AsyncToolRegistry.discover_impls()` or `build_default_registry()` for auto-registration; manual registration via `AsyncToolRegistry.register_tool()` or `register_callable()`
- `tools/impls/`: Built-in tool implementations (see below for registry details)
- `validation/graph.py`: Cycle detection; `validation/contract.py`: Tool input/output validation
- `models/handles.py`: First-class handles (`FieldDefinition`, `SchemaHandle`, `PeriodDescriptor`, `FIELD_NOT_FOUND`) used by tools and validators
- `registry/finance_functions.py`: Built-in deterministic finance functions (e.g., `fiscal_period_logic`, `ticker_lookup`, `compute_derived_fields`); `build_default_registry()` wires these into the `compute` tool

Data flow: Natural language goal → Plan (task sequence) → Pipeline (executable DAG) → Execution via `execute_pipeline` (or via `Orchestrator`)

## Critical Workflows

- **Install & Setup**: `pip install -e .[dev]` (includes pytest, black, mypy)
- **Testing**: `pytest tests/ -v` (unit in `tests/unit/`, integration in `tests/integration/`, fixtures in `tests/fixtures/`)
- **Linting**: `black .; isort .; mypy trellis/; ruff .` (line length 100, profile black)
- **Building**: `python -m build` (setuptools backend)
- **API Server**: `python -m trellis_api.main` (runs on localhost:8000)
- **CLI Usage**: `trellis validate path/to/{pipeline_or_plan}.yaml; trellis run path/to/{pipeline_or_plan}.yaml [--inputs JSON] [--session JSON] [--session-file PATH] [--timeout SECONDS] [--concurrency N] [--jitter FRACTION] [--json] [--llm-provider NAME] [--llm-model NAME] [--openai-api-key KEY] [--openai-model NAME] [--anthropic-api-key KEY] [--anthropic-model NAME] [--ollama-host URL] [--ollama-model NAME] [--extract-model NAME] [--env-file PATH]`
  - PowerShell example: `trellis run .\examples\pipelines\pdf_summarize.yaml --inputs '{"path":".\\examples\\data\\apple_10q_4q25.pdf"}' --timeout 30 --concurrency 5 --json`
  - PowerShell (OpenAI): `trellis run .\examples\pipelines\pdf_summarize.yaml --llm-provider openai --openai-api-key $env:OPENAI_API_KEY --openai-model gpt-4o`
  - PowerShell (Ollama): `trellis run .\examples\pipelines\pdf_summarize.yaml --llm-provider ollama --ollama-host http://localhost:11434 --ollama-model llama3`
  - PowerShell (Anthropic): `trellis run .\examples\pipelines\pdf_summarize.yaml --llm-provider anthropic --anthropic-api-key $env:ANTHROPIC_API_KEY --anthropic-model claude-3-haiku-20240307`
  - PowerShell (.env): `trellis run .\examples\pipelines\pdf_summarize.yaml --env-file .env`
  - Note: These flags set per-run environment overrides for built-in tools. `ingest_document` uses `INGEST_OCR_MODEL` for OCR (falls back to `EXTRACT_TEXT_MODEL`). `extract_from_texts`/`extract_from_tables` use `EXTRACT_MODEL` or `EXTRACT_TEXT_MODEL`. `select` uses `SELECT_MODEL` (falls back to `EXTRACT_TEXT_MODEL`). The `--llm-model` and `--extract-model` flags set `EXTRACT_TEXT_MODEL`.
  - For `llm_job`, the default model is read from `TRELLIS_LLM_MODEL` (see `tools/impls/llm.py`). To affect `llm_job` globally without per-task overrides, set `TRELLIS_LLM_MODEL` (the `--llm-model` CLI flag does not change this unless you also export that env var).
  - Validate/Run accept either a single-pipeline YAML (`pipeline:` root) or a plan YAML (`plan:` root). For plans, `validate` also reports contract checks; `run` executes sub-pipelines in wave order and prints the final blackboard when `--json` is not set.

- **API: Async queued runs (fallback queue)**
  - Submit: `POST /pipelines/run_async` with `{ pipeline, inputs?, session?, options?, tenant_id?, collect_events? }` → `{ run_id, status: queued }`
  - Status: `GET /pipelines/runs/{run_id}` → `{ status, result?, error?, events? }`
  - Cancel: `POST /pipelines/runs/{run_id}/cancel`

## Project Conventions

- **Implicit Dependencies**: No `depends_on`; infer from template references like `{{extract_tables.output}}` (see `execution/template.py`)
- **Blackboard Pattern**: Shared execution state (task outputs, pipeline inputs, goal, session keys, and current `{{item}}`) is held in `execution/template.py:ResolutionContext`. A tenant-scoped Blackboard (`execution/blackboard.py`) isolates persisted session data per `tenant_id`. The `store` tool writes are persisted to the tenant blackboard during execution and reflected into the in-run `session` for downstream reads. Note: the `store` tool implementation is an echo; persistence is handled by the executor, not the tool itself.
- **Multi-tenancy**: Pass `tenant_id` via `Orchestrator(tenant_id=...)` or in API `run_async` request body; all persisted blackboard reads/writes are isolated by `tenant_id`.
- **Tool Extension**: Subclass `BaseTool` and prefer auto-discovery via `AsyncToolRegistry.discover_impls()`/`build_default_registry()` (default constructor required). For manual wiring use `AsyncToolRegistry.register_tool()` or `register_callable()`. Examples in `tools/impls/` (e.g., `llm.py`, `mock.py`, `extract.py`, `document.py`, `store.py`, `search.py`, `export.py`, `load_schema.py`, `extract_fields.py`, `compute.py`).
- **DSL Design**: Flat task list; parallelism automatic; logic in `llm_job` prompts, not structure (see `docs/PIPELINE-DSL-V1.md`)
- **Naming**: Snake_case for pipeline/task IDs; avoid nesting or conditionals in DSL
- **Entry Points**: Thin wrappers; import from `trellis.*` (e.g., `trellis_cli/main.py` uses `Orchestrator`; `trellis_api/main.py` wires routers)

## Integration Points

- **External Tools**: Provide `BaseTool` implementations under `tools/impls/` (e.g., `fetch.py`, `document.py`, `llm.py`, `extract.py`, `load_schema.py`, `extract_fields.py`, `compute.py`, `store.py`, `search.py`, `export.py`, `mock.py`). Names should match DSL expectations (see `KNOWN_TOOLS` in `models/pipeline.py`). Auto-registered by the async registry when using `build_default_registry()`; the `compute` tool is re-registered with the built-in finance `FunctionRegistry`.
- **API Endpoints**: Add routers in `trellis_api/routers/` (e.g., `pipelines.py`). Synchronous runs: `POST /pipelines/run`. Queued runs: `POST /pipelines/run_async`, poll `GET /pipelines/runs/{id}`, cancel via `POST /pipelines/runs/{id}/cancel`. Pipeline validation: `POST /pipelines/validate`. Tool discovery: `GET /pipelines/tools` lists registered tools with metadata.
- **Execution Backends**: Local executor is default. Prefect adapter (`execution/prefect_adapter.py`) will provide a pluggable backend; background queue (`execution/run_queue.py`) is the fallback for dev/local.
- **MCP Protocol**: Expose tools to clients via `trellis_mcp/server.py`
- **Dependencies**: Core uses pydantic, pyyaml; API uses fastapi/uvicorn; CLI uses typer/rich and python-dotenv; built-in document/LLM tools use litellm, PyPDF2, and PyMuPDF; Python >= 3.12

### Tool Registry (as of DSL v1.4)

Document processing pipeline: `ingest_document → select → extract_from_texts / extract_from_tables`

| Tool                | Purpose                                                                  | Terminal? |
|---------------------|--------------------------------------------------------------------------|-----------|
| ingest_document     | Load files/URLs and fully resolve (incl. eager OCR) into DocumentHandle  | no        |
| select              | Retrieval: filter document to relevant pages by NL prompt or page numbers | no        |
| extract_from_texts  | Structured field extraction from page text → JSON dict                   | no        |
| extract_from_tables | Structured table extraction → list of {headers, rows, source_page}       | no        |
| load_schema         | Load/derive SchemaHandle from file/URL/DocumentHandle/registry name      | no        |
| extract_fields      | Schema-bound extraction for declared fields (emits `"__not_found__"`)    | no        |
| compute             | Invoke a named deterministic function from the FunctionRegistry          | no        |
| llm_job             | LLM reasoning, extraction, synthesis, generation                         | no        |
| fetch_data          | Retrieve structured data from external sources                            | no        |
| search_web          | Web search, returns snippets and URLs                                     | no        |
| store               | Persist a value to the session blackboard (see note above)                | yes*      |
| export              | Produce a file artifact (md, pdf, csv, xlsx, json)                        | yes       |
| mock                | Test helper tool (dev/testing only)                                       | no        |
| extract_chart       | Extract chart data from documents (stub)                                  | no        |
| classify_page       | Page classification to guide extraction (reserved)                        | no        |

*`store` is logically terminal but may appear mid-pipeline if persistence is needed before further processing steps.

- Tracker/fake tools (e.g., `tracker`, `failing_mock`, `flaky_tool`, `reliable_tool`, `permanent_failure`, `tracker_1`, `tracker_2`, `tracker_3`, `tracker_4`) are for testing and should not be used in production pipelines.

> Note: `extract_chart` is provided as a stub implementation in `tools/impls/extract.py`. `classify_page` is part of the DSL tool names but is not registered by default — implement and register a `BaseTool` to use it in pipelines.

- Input document types supported:
  - Raw web content: pass as a string to `select` / `extract_from_texts` / `extract_from_tables` (auto-wrapped into a single-page TEXT handle)
  - Structured API content (e.g., LSEG, EDGAR): use `fetch_data`; persist with `store` for cross-pipeline reuse via `{{session.*}}`
  - PDFs: digital-text, image-only, or mixed; `ingest_document` eagerly OCRs image-heavy pages; images/logos/photos are retained in `Page.image_bytes` for downstream table extraction
  - Excel: multi-sheet workbooks; `Page.sheet_name` is preserved; `extract_from_tables` can return multiple tables per sheet and includes `sheet_name` in results

> Note: `search_web` defaults to DuckDuckGo HTML; set `SERPAPI_API_KEY` to enable Google via SerpAPI (`provider: serpapi`). See `examples/pipelines/web_search_investor_day.yaml` for a concrete raw web content pipeline using `search_web → llm_job → store`.

### Await Barrier

- The DSL supports an explicit `await` field on tasks as an escape hatch for dependencies that do not consume outputs. Use sparingly; frequent use indicates the pipeline structure may need revisiting.

Reference: `docs/ARCHITECTURE.md`, `docs/PIPELINE-DSL-V1.md`, `pyproject.toml`
