# Quickstart

This guide takes you from zero to a running pipeline in about five minutes. You will validate a pipeline, run it from the CLI, and then run the same pipeline through the REST API.

**Before you start:** make sure Trellis is installed with the `cli` and `api` extras.

```bash
pip install "trellis[cli,api]"
```

---

## Step 1 — Validate your first pipeline

All example pipelines ship in `examples/pipelines/`. Start with the smallest one:

```yaml title="examples/pipelines/single_mock.yaml"
pipeline:
  id: hello_mock
  goal: "Run a single mock task"
  tasks:
    - id: say_hello
      tool: mock
      inputs:
        text: "hello world"
```

Run the validator:

```bash
trellis validate examples/pipelines/single_mock.yaml
```

```
Pipeline is valid.
Stats:
{
  "id": "hello_mock",
  "goal": "Run a single mock task",
  "tasks": 1,
  "tools": ["mock"],
  "inputs_count": 0,
  "store_keys": [],
  "waves": 1,
  "wave_sizes": [1],
  "fan_out_tasks": 0,
  "total_retries": 0
}
```

The validator parses the YAML, enforces the DSL schema, checks that all template references resolve to declared tasks, and reports the execution wave structure without running anything.

---

## Step 2 — Run it

```bash
trellis run examples/pipelines/single_mock.yaml
```

```
Outputs:
{
  "say_hello": {
    "status": "success",
    "message": "Mock tool executed",
    "inputs": {
      "text": "hello world"
    },
    "call_count": 1
  }
}

Stats:
{
  "waves_executed": 1,
  "tasks_executed": 1
}
```

Each key in `outputs` is a task ID. The value is whatever that task returned. For the `mock` tool that is a small status dict — real tools like `extract_fields` or `llm_job` return domain-specific structures.

To get only the output JSON (useful for piping):

```bash
trellis run examples/pipelines/single_mock.yaml --json
```

---

## Step 3 — Chained tasks and template references

Dependencies between tasks are declared implicitly with `{{task_id.output.field}}` syntax. There is no explicit `depends_on` key — the executor infers the DAG from the templates.

```yaml title="examples/pipelines/dependency_chain.yaml"
pipeline:
  id: dependency_chain
  goal: "Show implicit dependencies via templates"
  tasks:
    - id: first
      tool: mock
      inputs:
        input_data: "start"

    - id: second
      tool: mock
      inputs:
        from_first: "{{first.output.status}}"

    - id: third
      tool: mock
      inputs:
        from_second: "{{second.output.status}}"
```

```bash
trellis run examples/pipelines/dependency_chain.yaml
```

```
Outputs:
{
  "first": { "status": "success", "inputs": { "input_data": "start" }, ... },
  "second": { "status": "success", "inputs": { "from_first": "success" }, ... },
  "third": { "status": "success", "inputs": { "from_second": "success" }, ... }
}

Stats:
{
  "waves_executed": 3,
  "tasks_executed": 3
}
```

Three waves because each task depends on the previous one — they must run sequentially. Tasks with no shared dependencies run in the same wave concurrently.

---

## Step 4 — Pass parameters at run time

### With `--inputs` (simple defaults)

The `pipeline.inputs` block sets default values that can be overridden at call time via `--inputs`:

```yaml title="examples/pipelines/pipeline_inputs.yaml"
pipeline:
  id: pipeline_inputs
  goal: "Use pipeline inputs in a task"
  inputs:
    user_name: "Ada"
  tasks:
    - id: greet
      tool: mock
      inputs:
        message: "Hello, {{pipeline.inputs.user_name}}!"
```

```bash
# Use the declared default
trellis run examples/pipelines/pipeline_inputs.yaml

# Override at call time
trellis run examples/pipelines/pipeline_inputs.yaml --inputs '{"user_name": "Grace"}'
```

### With `--params` (typed, validated parameters)

`params` gives each parameter a declared type and makes required vs optional explicit. Trellis validates and coerces values before any task runs.

```yaml title="examples/pipelines/fetch_10k_parametrized.yaml (excerpt)"
pipeline:
  id: fetch_and_load_10k
  goal: "Fetch {{params.company_name}} 10-K filing for FY{{params.fiscal_year}}"

  params:
    company_name:
      type: string
      description: "Full legal company name"
    ticker:
      type: string
      description: "Stock ticker symbol"
    fiscal_year:
      type: integer
      default: 2024          # optional — defaults to 2024
```

```bash
# Supply required params; fiscal_year uses its default
trellis run examples/pipelines/fetch_10k_parametrized.yaml \
  --params '{"company_name": "Apple Inc.", "ticker": "AAPL"}'

# Override everything
trellis run examples/pipelines/fetch_10k_parametrized.yaml \
  --params '{"company_name": "Microsoft Corp.", "ticker": "MSFT", "fiscal_year": 2023}'
```

If a required param is missing, Trellis exits with a clear error before any task starts.

---

## Step 5 — Run via the REST API

Start the API server:

```bash
uvicorn trellis_api.main:app --reload
```

The server listens on `http://127.0.0.1:8000` by default. Interactive docs are available at `http://127.0.0.1:8000/docs`.

Post the same hello-world pipeline inline:

=== "curl"

    ```bash
    curl -s -X POST http://127.0.0.1:8000/pipelines/run \
      -H "Content-Type: application/json" \
      -d '{
        "pipeline": {
          "id": "hello_mock",
          "goal": "Run a single mock task",
          "tasks": [
            {
              "id": "say_hello",
              "tool": "mock",
              "inputs": { "text": "hello world" }
            }
          ]
        }
      }' | python -m json.tool
    ```

=== "Python"

    ```python
    import httpx

    pipeline = {
        "id": "hello_mock",
        "goal": "Run a single mock task",
        "tasks": [
            {"id": "say_hello", "tool": "mock", "inputs": {"text": "hello world"}}
        ],
    }

    resp = httpx.post("http://127.0.0.1:8000/pipelines/run", json={"pipeline": pipeline})
    resp.raise_for_status()
    print(resp.json())
    ```

Response:

```json
{
  "outputs": {
    "say_hello": {
      "status": "success",
      "message": "Mock tool executed",
      "inputs": { "text": "hello world" },
      "call_count": 1
    }
  },
  "waves_executed": 1,
  "tasks_executed": 1,
  "events": null
}
```

For long-running pipelines use `POST /pipelines/run_async` to get a `run_id`, then poll `GET /pipelines/runs/{run_id}` for status and results.

---

---

## Step 6 — Compile a pipeline from a prompt

Instead of writing YAML by hand, describe what you want in plain English and let the Trellis compiler generate a validated pipeline for you.

```bash
trellis compile "Fetch Apple's latest 10-K from SEC EDGAR and summarise the key risk factors in bullet points" \
  --output pipelines/aapl_risks.yaml
```

```
Compiling...
Compiled pipeline 'fetch_apple_risks' -> pipelines/aapl_risks.yaml
```

The compiler calls an LLM with a system prompt containing the full DSL spec and your registered tool catalog. The response is validated with Pydantic and checked for cycles before it is accepted. If the first attempt fails validation, the compiler re-prompts with the error and tries again (up to `--max-repairs` times, default 2).

Validate and run the result immediately:

```bash
trellis validate pipelines/aapl_risks.yaml
trellis run pipelines/aapl_risks.yaml
```

### Output to stdout (pipe-friendly)

```bash
# --json prints only the raw YAML — no headers, no stats
trellis compile "Summarise a PDF report in five executive bullet points" --json \
  > pipelines/pdf_summary.yaml
```

### Prompt from a file

For longer or more structured descriptions, put them in a text file:

```bash
trellis compile --prompt-file prompts/sec_extraction_brief.txt \
  --output pipelines/sec_extract.yaml
```

### From Python

The compiler is also available as a Python class:

```python
import asyncio
from trellis.compiler import PipelineCompiler

compiler = PipelineCompiler()
result = asyncio.run(compiler.compile(
    "Fetch Apple's latest 10-K from SEC EDGAR and summarise key risks."
))
print(result.yaml_text)
print(f"Pipeline id: {result.pipeline.id}")
print(f"Compiled in {result.attempts} attempt(s)")
```

`CompilerResult` exposes `pipeline` (or `plan`), `yaml_text`, `attempts`, and a `repair_history` list of `(broken_yaml, error)` pairs from any failed intermediate attempts.

---

## What's next

| Topic | Where to go |
|---|---|
| Full task syntax, tools, templates | [Pipeline DSL reference](PIPELINE-DSL.md) |
| All CLI flags and environment variables | [CLI reference](interfaces-cli.md) |
| Compile from prompt — full walkthrough | [Compile tutorial](tutorials/compile-pipeline.md) |
| API endpoints, request/response schemas | [API reference](interfaces-api.md) |
| Fan-out, retries, timeouts, plans | [Execution reference](operations-execution.md) |
| Adding your own tools | [Extensibility](extensibility-index.md) |
