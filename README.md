# Trellis

An agentic solution for pipeline planning and execution within controlled environments.

## Quick Start

### Installation

```bash
pip install -e .
```

### Usage

#### CLI

```bash
# Validate a pipeline
trellis validate pipelines/example.yaml

# Run a pipeline
trellis run pipelines/example.yaml

# Generate a plan from a goal
trellis plan "Extract and analyze market data"

# Generate a pipeline from a goal
trellis generate "Extract and analyze market data" -o output.yaml
```

#### API

```bash
# Start the API server
python -m trellis_api.main

# Create a pipeline
curl -X POST http://localhost:8000/pipelines \
  -H "Content-Type: application/json" \
  -d '{
    "id": "example_pipeline",
    "goal": "Process data",
    "tasks": []
  }'
```

#### Python Library

```python
from trellis.models.pipeline import Pipeline
from trellis.execution.dag import DAGExecutor

pipeline = Pipeline(
    id="my_pipeline",
    goal="Extract and process data",
    tasks=[]
)

executor = DAGExecutor()
results = executor.execute({})
```

## Architecture

See [docs/architecture.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

## Project Structure

```
trellis/
├── trellis/                    # Core library (shared)
│   ├── models/             # Pydantic DSL models
│   ├── validation/         # DAG and contract validation
│   ├── execution/          # Execution engine
│   └── tools/              # Tool protocol and implementations
├── trelis_api/             # FastAPI server
├── trelis_mcp/             # MCP server
├── trelis_cli/             # CLI interface
├── data/                   # Dataset generation
├── tests/                  # Test suite
├── docs/                   # Documentation
└── scripts/                # Utility scripts
```

## Development

### Testing

```bash
pytest tests/ -v
```

### Linting

```bash
black .
isort .
mypy trellis/
```

### Building

```bash
python -m build
```

## License

See LICENSE file for details.
