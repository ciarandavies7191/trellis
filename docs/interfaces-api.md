# API (REST)

Trellis ships a FastAPI server (`trellis_api`) that exposes pipeline and plan operations over HTTP. It supports synchronous runs (response returns when the pipeline finishes) and asynchronous runs (response returns a `run_id` immediately for polling).

**Start the server:**

```bash
uvicorn trellis_api.main:app --host 127.0.0.1 --port 8000
```

Interactive docs are available at `http://127.0.0.1:8000/docs` once the server is running.

---

## Base URL

All endpoints described below are relative to `http://127.0.0.1:8000` (or whichever host/port you configure via `TRELLIS_API_HOST` / `TRELLIS_API_PORT`).

---

## Endpoints

### `GET /health`

Liveness check. Returns `{"status": "ok"}`.

```bash
curl http://127.0.0.1:8000/health
```

---

### `POST /pipelines/validate`

Parses and validates a pipeline definition without executing it.

**Request body:**

```json
{
  "pipeline": { ...pipeline object... }
}
```

**Response:**

```json
{
  "ok": true,
  "message": "Pipeline is valid"
}
```

On failure, `ok` is `false` and `errors` contains a list of validation messages.

---

### `GET /pipelines/tools`

Returns the list of registered tool names and their metadata.

```bash
curl http://127.0.0.1:8000/pipelines/tools
```

**Response:**

```json
{
  "tools": ["ingest_document", "select", "extract_fields", "llm_job", "..."],
  "metadata": [
    {
      "name": "llm_job",
      "description": "Run a single LLM prompt and return the response text.",
      "inputs": {
        "prompt": {"name": "prompt", "description": "...", "required": true, "default": null}
      },
      "output": {"name": "output", "description": "LLM response text", "type": "string"}
    }
  ]
}
```

---

### `POST /pipelines/run`

Run a pipeline synchronously. The HTTP connection stays open until the pipeline completes and returns all task outputs in the response body.

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `pipeline` | object | Yes | Full pipeline definition (same structure as YAML, parsed as JSON) |
| `inputs` | object | No | `pipeline.inputs` values |
| `session` | object | No | Pre-seeded session/blackboard values |
| `options` | object | No | Execution options (see below) |
| `collect_events` | bool | No | Include per-task event log in response (default `false`) |

**Execution options object:**

| Field | Type | Description |
|---|---|---|
| `per_task_timeout` | float | Per-attempt timeout in seconds |
| `fan_out_concurrency` | int | Max parallel workers for `parallel_over` tasks |
| `backoff_jitter` | float | Retry backoff jitter fraction, 0.0–1.0 (default `0.0`) |

**Example:**

```bash
curl -X POST http://127.0.0.1:8000/pipelines/run \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": {
      "id": "hello",
      "goal": "Greet the world",
      "tasks": [
        {
          "id": "greet",
          "tool": "llm_job",
          "inputs": {"prompt": "Say hello in one sentence."}
        }
      ]
    },
    "options": {"per_task_timeout": 30}
  }'
```

**Response:**

```json
{
  "outputs": {
    "greet": "Hello, world! It's wonderful to meet you."
  },
  "waves_executed": 1,
  "tasks_executed": 1,
  "events": null
}
```

**Error responses:**

| Status | Cause |
|---|---|
| `400` | Invalid pipeline schema or misconfigured LLM provider |
| `500` | Task execution failed (includes `task_id` and error in `detail`) |

---

### `POST /pipelines/run_async`

Submit a pipeline for background execution. Returns a `run_id` immediately; poll `/pipelines/runs/{run_id}` for status.

**Request body** — same fields as `/pipelines/run`, plus:

| Field | Type | Description |
|---|---|---|
| `tenant_id` | string | Tenant namespace for blackboard isolation (default `"default"`) |

**Example:**

```bash
curl -X POST http://127.0.0.1:8000/pipelines/run_async \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": { ...same as above... },
    "tenant_id": "acme-corp"
  }'
```

**Response:**

```json
{
  "run_id": "a3f7b2c1d4e5f6a7b8c9d0e1f2a3b4c5",
  "status": "queued"
}
```

---

### `GET /pipelines/runs/{run_id}`

Poll the status of an async run.

```bash
curl http://127.0.0.1:8000/pipelines/runs/a3f7b2c1d4e5f6a7b8c9d0e1f2a3b4c5
```

**Response:**

```json
{
  "run_id": "a3f7b2c1d4e5f6a7b8c9d0e1f2a3b4c5",
  "status": "succeeded",
  "result": {
    "outputs": { "greet": "Hello, world!" },
    "waves_executed": 1,
    "tasks_executed": 1
  },
  "error": null,
  "events": null
}
```

**Status values:**

| Value | Meaning |
|---|---|
| `queued` | Waiting to start |
| `running` | Pipeline is executing |
| `succeeded` | Finished — `result` is populated |
| `failed` | Execution error — `error` contains the message |
| `cancelled` | Cancelled via `POST /runs/{run_id}/cancel` |

Returns `404` if `run_id` is not found.

---

### `POST /pipelines/runs/{run_id}/cancel`

Cancel a queued or running pipeline.

```bash
curl -X POST http://127.0.0.1:8000/pipelines/runs/a3f7b2c1d4e5f6a7b8c9d0e1f2a3b4c5/cancel
```

Returns `400` if the run is already in a terminal state (`succeeded`, `failed`, `cancelled`).

---

### `POST /plans/validate`

Validate a plan definition without executing it.

**Request body:**

```json
{
  "plan": { ...plan object... }
}
```

**Response:**

```json
{
  "ok": true,
  "message": "Plan is valid"
}
```

---

## Running a pipeline from Python

```python
import httpx

pipeline_def = {
    "id": "sec_extraction",
    "goal": "Extract AAPL FY2024 income statement",
    "tasks": [
        {
            "id": "fetch",
            "tool": "fetch_data",
            "inputs": {
                "source": "sec_edgar",
                "ticker": "AAPL",
                "period_end": "2024-09-30",
                "period_type": "annual",
            }
        },
        # ... more tasks
    ]
}

response = httpx.post(
    "http://127.0.0.1:8000/pipelines/run",
    json={"pipeline": pipeline_def, "options": {"per_task_timeout": 120}},
    timeout=300,
)
response.raise_for_status()
data = response.json()
print(data["outputs"])
```

---

## Async polling pattern

```python
import time
import httpx

client = httpx.Client(base_url="http://127.0.0.1:8000")

# Submit
resp = client.post("/pipelines/run_async", json={"pipeline": pipeline_def})
run_id = resp.json()["run_id"]

# Poll until done
while True:
    status = client.get(f"/pipelines/runs/{run_id}").json()
    if status["status"] in ("succeeded", "failed", "cancelled"):
        break
    time.sleep(2)

if status["status"] == "succeeded":
    print(status["result"]["outputs"])
else:
    print("Failed:", status["error"])
```

---

## Server configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `TRELLIS_API_HOST` | `127.0.0.1` | Bind address |
| `TRELLIS_API_PORT` | `8000` | Port |
| `OPENAI_API_KEY` | — | Required for OpenAI-backed tools |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic-backed tools |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |

---

## Next steps

- [CLI](interfaces-cli.md) — validate and run pipelines from the command line
- [Tools & Registry](tools-index.md) — tool reference, inputs, and outputs
- [Execution Backends](operations-execution.md) — run queue and concurrency settings
