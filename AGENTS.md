# AGENTS.md

## Architecture Overview

Trellis is a modular agentic pipeline system with a shared core library (`trellis/`) powering three entry points: `trellis_api/` (FastAPI REST server), `trellis_cli/` (command-line interface), and `trellis_mcp/` (Model Context Protocol server). The core enforces separation of concerns with Pydantic models, validation, execution engine, and tool registry.

Key components:
- `models/pipeline.py`: Pipeline DSL as DAG of tasks with implicit dependencies via `{{task_id.output}}` templates
- `execution/dag.py`: Async DAG executor that resolves templates, supports fan-out (`parallel_over`), retries, and execution stats; shared state flows through `ResolutionContext`
- `execution/orchestrator.py`: High-level runner that builds a `ResolutionContext`, discovers tools via the default async registry, runs `execute_pipeline`, and returns a structured `RunResult` (with `cancel()` support and optional event collection)
- `tools/registry.py`: Async tool discovery/invocation; subclass `BaseTool` for new tools (e.g., `tools/impls/llm.py`). Use `AsyncToolRegistry.discover_impls()` or `build_default_registry()` for auto-registration; manual registration via `AsyncToolRegistry.register_tool()` or `register_callable()`
- `validation/graph.py`: Cycle detection; `validation/contract.py`: Tool input/output validation

Data flow: Natural language goal → Plan (task sequence) → Pipeline (executable DAG) → Execution via `execute_pipeline` (or via `Orchestrator`)

## Critical Workflows

- **Install & Setup**: `pip install -e .[dev]` (includes pytest, black, mypy)
- **Testing**: `pytest tests/ -v` (unit in `tests/unit/`, integration in `tests/integration/`)
- **Linting**: `black .; isort .; mypy trellis/; ruff .` (line length 100, profile black)
- **Building**: `python -m build` (setuptools backend)
- **API Server**: `python -m trellis_api.main` (runs on localhost:8000)
- **CLI Usage**: `trellis validate path/to/pipeline.yaml; trellis run path/to/pipeline.yaml [--inputs JSON] [--session JSON] [--timeout SECONDS] [--concurrency N] [--jitter FRACTION] [--json]`
  - PowerShell example: `trellis run .\examples\pipelines\single_mock.yaml --inputs '{"param":"value"}' --timeout 30 --concurrency 5 --json`

## Project Conventions

- **Implicit Dependencies**: No `depends_on`; infer from template references like `{{extract_tables.output}}` (see `execution/template.py`)
- **Blackboard Pattern**: Shared execution state (task outputs, pipeline inputs, goal, session keys, and current `{{item}}`) is held in `execution/template.py:ResolutionContext`. Session "blackboard" access uses `{{session.key}}` and is validated against plan reads in `validation/contract.py`. (`execution/blackboard.py` exists as a placeholder.)
- **Tool Extension**: Subclass `BaseTool` and prefer auto-discovery via `AsyncToolRegistry.discover_impls()`/`build_default_registry()` (default constructor required). For manual wiring use `AsyncToolRegistry.register_tool()` or `register_callable()`. Examples in `tools/impls/` (e.g., `llm.py`).
- **DSL Design**: Flat task list; parallelism automatic; logic in `llm_job` prompts, not structure (see `docs/PIPELINE-DSL-V1.md`)
- **Naming**: Snake_case for pipeline/task IDs; avoid nesting or conditionals in DSL
- **Entry Points**: Thin wrappers; import from `trellis.*` (e.g., `trellis_cli/main.py` uses `Orchestrator`; `trellis_api/main.py` wires routers)

## Integration Points

- **External Tools**: Provide `BaseTool` implementations under `tools/impls/` (e.g., `fetch.py`, `document.py`, `llm.py`). Names should match DSL expectations (see `KNOWN_TOOLS` in `models/pipeline.py`). Auto-registered by the async registry when using `build_default_registry()`.
- **API Endpoints**: Add routers in `trellis_api/routers/` (e.g., `pipelines.py` for CRUD)
- **MCP Protocol**: Expose tools to clients via `trellis_mcp/server.py`
- **Dependencies**: Core uses pydantic, pyyaml; API uses fastapi/uvicorn; CLI uses typer/rich; Python >= 3.12

Reference: `docs/ARCHITECTURE.md`, `docs/PIPELINE-DSL-V1.md`, `pyproject.toml`
