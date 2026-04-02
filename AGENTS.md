# AGENTS.md

## Architecture Overview

Trellis is a modular agentic pipeline system with a shared core library (`trellis/`) powering three entry points: `trellis_api/` (FastAPI REST server), `trellis_cli/` (command-line interface), and `trellis_mcp/` (Model Context Protocol server). The core enforces separation of concerns with Pydantic models, validation, execution engine, and tool registry.

Key components:
- `models/pipeline.py`: Pipeline DSL as DAG of tasks with implicit dependencies via `{{task_id.output}}` templates
- `execution/dag.py`: Topological sort executor with blackboard pattern for shared state
- `tools/registry.py`: Tool discovery; implement `BaseTool` for new tools (e.g., `tools/impls/llm.py`)
- `validation/graph.py`: Cycle detection; `validation/contract.py`: Tool input/output validation

Data flow: Natural language goal → Plan (task sequence) → Pipeline (executable DAG) → Execution via `DAGExecutor`

## Critical Workflows

- **Install & Setup**: `pip install -e .[dev]` (includes pytest, black, mypy)
- **Testing**: `pytest tests/ -v` (unit in `tests/unit/`, integration in `tests/integration/`)
- **Linting**: `black .; isort .; mypy trellis/` (line length 100, profile black)
- **Building**: `python -m build` (setuptools backend)
- **API Server**: `python -m trellis_api.main` (runs on localhost:8000)
- **CLI Usage**: `trellis validate path/to/pipeline.yaml; trellis run path/to/pipeline.yaml`

## Project Conventions

- **Implicit Dependencies**: No `depends_on`; infer from template references like `{{extract_tables.output}}` (see `execution/template.py`)
- **Blackboard Pattern**: Tasks share state via `Blackboard` instance; read/write during execution (e.g., `execution/blackboard.py`)
- **Tool Extension**: Subclass `BaseTool`, register with `ToolRegistry.register()` (examples in `tools/impls/`)
- **DSL Design**: Flat task list; parallelism automatic; logic in `llm_job` prompts, not structure (see `docs/pipeline-dsl-v1.md`)
- **Naming**: Snake_case for pipeline/task IDs; avoid nesting or conditionals in DSL
- **Entry Points**: Thin wrappers; import from `trellis.*` (e.g., `trellis_api/main.py` imports `DAGExecutor`)

## Integration Points

- **External Tools**: Register via `ToolRegistry` (e.g., `fetch_data`, `load_document` in `tools/impls/`)
- **API Endpoints**: Add routers in `trellis_api/routers/` (e.g., `pipelines.py` for CRUD)
- **MCP Protocol**: Expose tools to clients via `trellis_mcp/server.py`
- **Dependencies**: Core uses pydantic, pyyaml; optional for API (fastapi), CLI (typer, rich)

Reference: `docs/architecture.md`, `docs/pipeline-dsl-v1.md`, `pyproject.toml`</content>
<parameter name="filePath">D:\projects\trellis\AGENTS.md
