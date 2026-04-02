# Trellis Architecture

## Overview

Trellis is a modular system for agentic pipeline planning and execution. The architecture is organized into distinct layers:

## Core Components

### `core/` — Shared Library

The core library is importable by all entry points and contains:

- **Models** (`core/models/`)
  - `plan.py` — Plan representation
  - `pipeline.py` — Pipeline DSL models (Pydantic-based)

- **Validation** (`core/validation/`)
  - `graph.py` — DAG structure validation, cycle detection
  - `contract.py` — Tool input/output contract validation

- **Execution** (`core/execution/`)
  - `template.py` — Template variable resolution (${task.output})
  - `dag.py` — DAG executor with topological sort
  - `orchestrator.py` — Execution monitoring and state management
  - `blackboard.py` — Shared execution context (pattern)

- **Tools** (`core/tools/`)
  - `base.py` — Tool protocol (abstract base class)
  - `registry.py` — Tool registration and discovery
  - `impls/` — Tool implementations
    - `mock.py` — Mock tool for testing
    - `llm.py` — LLM-based tasks
    - `fetch.py` — Data fetching
    - `document.py` — Document processing

### `trelis_api/` — FastAPI Server

Thin REST layer over core:
- `main.py` — FastAPI application setup
- `routers/pipelines.py` — Pipeline management endpoints
- `routers/plans.py` — Plan generation endpoints
- `schemas.py` — API request/response models

### `trelis_mcp/` — Model Context Protocol Server

Exposes Trellis as a tool provider to Claude and other MCP clients:
- `server.py` — MCP server implementation

### `trelis_cli/` — Command-Line Interface

Thin Typer/Click wrapper:
- `main.py` — CLI commands: validate, run, plan, generate

### `data/` — Dataset Pipeline

For generating evaluation data:
- `generate_dataset.py` — Dataset generation logic
- `prompts/` — Generation prompts
- `archetypes/` — Pipeline archetypes for generation

### `tests/` — Test Suite

```
tests/
├── unit/
│   ├── models/
│   ├── validation/
│   └── execution/
├── integration/
│   └── pipelines/
└── fixtures/
    └── yaml/
```

### `docs/` — Documentation

- `dsl-spec-v1.3.md` — DSL specification
- `architecture.md` — This file

## Data Flow

1. **Planning Phase**
   - User provides natural language goal
   - Model generates a Plan (task sequence)
   - Plan is validated for feasibility

2. **Pipeline Generation**
   - Plan is converted to Pipeline (DAG of tasks)
   - Graph validation checks for cycles
   - Contract validation checks tool compatibility

3. **Execution Phase**
   - DAG executor orders tasks topologically
   - Blackboard tracks shared state
   - Orchestrator monitors progress
   - Results flow through template resolution

## Extension Points

- **New Tools**: Implement `BaseTool`, register with `ToolRegistry`
- **New Validators**: Add to `core/validation/`
- **API Endpoints**: Add routers to `trelis_api/routers/`
- **CLI Commands**: Extend `TrelisCLI` class
