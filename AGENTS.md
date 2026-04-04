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

Data flow: Natural language goal → Plan (task sequence) → Pipeline (executable DAG) → Execution via `execute_pipeline` (or via `Orchestrator`)

## Critical Workflows

- **Install & Setup**: `pip install -e .[dev]` (includes pytest, black, mypy)
- **Testing**: `pytest tests/ -v` (unit in `tests/unit/`, integration in `tests/integration/`, fixtures in `tests/fixtures/`)
- **Linting**: `black .; isort .; mypy trellis/; ruff .` (line length 100, profile black)
- **Building**: `python -m build` (setuptools backend)
- **API Server**: `python -m trellis_api.main` (runs on localhost:8000)
- **CLI Usage**: `trellis validate path/to/pipeline.yaml; trellis run path/to/pipeline.yaml [--inputs JSON] [--session JSON] [--timeout SECONDS] [--concurrency N] [--jitter FRACTION] [--json]`
  - PowerShell example: `trellis run .\examples\pipelines\single_mock.yaml --inputs '{"param":"value"}' --timeout 30 --concurrency 5 --json`
- **API: Async queued runs (fallback queue)**
  - Submit: `POST /pipelines/run_async` with `{ pipeline, inputs?, session?, options?, tenant_id?, collect_events? }` → `{ run_id, status: queued }`
  - Status: `GET /pipelines/runs/{run_id}` → `{ status, result?, error?, events? }`
  - Cancel: `POST /pipelines/runs/{run_id}/cancel`

## Project Conventions

- **Implicit Dependencies**: No `depends_on`; infer from template references like `{{extract_tables.output}}` (see `execution/template.py`)
- **Blackboard Pattern**: Shared execution state (task outputs, pipeline inputs, goal, session keys, and current `{{item}}`) is held in `execution/template.py:ResolutionContext`. A tenant-scoped Blackboard (`execution/blackboard.py`) isolates persisted session data per `tenant_id`. The `store` tool writes are persisted to the tenant blackboard during execution and reflected into the in-run `session` for downstream reads. Note: the `store` tool implementation is an echo; persistence is handled by the executor, not the tool itself.
- **Multi-tenancy**: Pass `tenant_id` via `Orchestrator(tenant_id=...)` or in API `run_async` request body; all persisted blackboard reads/writes are isolated by `tenant_id`.
- **Tool Extension**: Subclass `BaseTool` and prefer auto-discovery via `AsyncToolRegistry.discover_impls()`/`build_default_registry()` (default constructor required). For manual wiring use `AsyncToolRegistry.register_tool()` or `register_callable()`. Examples in `tools/impls/` (e.g., `llm.py`, `mock.py`, `extract.py`, `document.py`, `store.py`, `search.py`, `export.py`).
- **DSL Design**: Flat task list; parallelism automatic; logic in `llm_job` prompts, not structure (see `docs/PIPELINE-DSL-V1.md`)
- **Naming**: Snake_case for pipeline/task IDs; avoid nesting or conditionals in DSL
- **Entry Points**: Thin wrappers; import from `trellis.*` (e.g., `trellis_cli/main.py` uses `Orchestrator`; `trellis_api/main.py` wires routers)

## Integration Points

- **External Tools**: Provide `BaseTool` implementations under `tools/impls/` (e.g., `fetch.py`, `document.py`, `llm.py`, `extract.py`, `store.py`, `search.py`, `export.py`, `mock.py`). Names should match DSL expectations (see `KNOWN_TOOLS` in `models/pipeline.py`). Auto-registered by the async registry when using `build_default_registry()`.
- **API Endpoints**: Add routers in `trellis_api/routers/` (e.g., `pipelines.py`). Synchronous runs: `POST /pipelines/run`. Queued runs: `POST /pipelines/run_async`, poll `GET /pipelines/runs/{id}`, cancel via `POST /pipelines/runs/{id}/cancel`.
- **Execution Backends**: Local executor is default. Prefect adapter (`execution/prefect_adapter.py`) will provide a pluggable backend; background queue (`execution/run_queue.py`) is the fallback for dev/local.
- **MCP Protocol**: Expose tools to clients via `trellis_mcp/server.py`
- **Dependencies**: Core uses pydantic, pyyaml; API uses fastapi/uvicorn; CLI uses typer/rich; Python >= 3.12

### Tool Registry (as of DSL v1.3)

| Tool           | Purpose                                                      | Terminal? |
|----------------|--------------------------------------------------------------|-----------|
| load_document  | Load files or URLs into working memory                       | no        |
| select         | Filter document to relevant pages/sections/sheets            | no        |
| extract_table  | Deterministic table extraction from documents                | no        |
| extract_text   | Plain text extraction from documents                         | no        |
| llm_job        | LLM reasoning, extraction, synthesis, generation             | no        |
| fetch_data     | Retrieve structured data from external sources               | no        |
| search_web     | Web search, returns snippets and URLs                        | no        |
| store          | Persist a value to the session blackboard (see note above)   | yes*      |
| export         | Produce a file artifact (md, pdf, csv, xlsx, json)           | yes       |
| mock           | Test helper tool (dev/testing only)                          | no        |

*`store` is logically terminal but may appear mid-pipeline if persistence is needed before further processing steps.

- Tracker/fake tools (e.g., `tracker`, `failing_mock`, `flaky_tool`, `reliable_tool`, `permanent_failure`, `tracker_1`, `tracker_2`, `tracker_3`, `tracker_4`) are for testing and should not be used in production pipelines.

### Await Barrier

- The DSL supports an explicit `await` field on tasks as an escape hatch for dependencies that do not consume outputs. Use sparingly; frequent use indicates the pipeline structure may need revisiting.

Reference: `docs/ARCHITECTURE.md`, `docs/PIPELINE-DSL-V1.md`, `pyproject.toml`
