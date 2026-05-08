# CLI

The `trellis` command-line tool validates and runs pipelines and plans. It is installed as part of the `trellis-cli` package and reads configuration from `.env` files and CLI flags.

```bash
pip install trellis-cli
```

---

## Commands

```
trellis [OPTIONS] COMMAND [ARGS]...
```

| Command | Description |
|---|---|
| `validate PATH` | Parse and validate a pipeline or plan YAML; print stats |
| `run PATH` | Execute a pipeline or plan YAML |

Run `trellis --help` or `trellis <command> --help` for full option lists.

---

## `trellis validate`

Parses the YAML at `PATH`, validates it with Pydantic, and prints a stats block. Exits with code `0` on success, `1` on validation failure.

```bash
trellis validate pipelines/sec_extraction.yaml
```

**Pipeline output (success):**

```
Pipeline is valid.
Stats:
{
  "id": "sec_extraction",
  "goal": "Fetch the Apple Inc. 10-K for 2024-09-30 from SEC EDGAR...",
  "tasks": 5,
  "tools": ["export", "extract_fields", "fetch_data", "ingest_document", "load_schema", "select"],
  "inputs_count": 0,
  "store_keys": [],
  "waves": 5,
  "wave_sizes": [2, 1, 1, 1, 1],
  "fan_out_tasks": 0,
  "total_retries": 0
}
```

For a **plan YAML**, `validate` also locates co-located sub-pipeline files (same directory, `<id>.yaml`) and runs contract checks — verifying that `store` keys, `reads` declarations, and `pipeline.inputs` are consistent.

**Plan output:**

```
Plan is valid.
Stats:
{
  "id": "spreading_plan",
  "goal": "Fetch and spread Apple FY2024 financial statements",
  "sub_pipelines": 3,
  "waves": 2,
  "wave_ids": [["fetch_10k", "fetch_schema"], ["spread"]]
}

Contract checks:
  ✓ fetch_10k
  ✓ fetch_schema
  ✓ spread
```

**Options:**

| Flag | Description |
|---|---|
| `--env-file PATH` | Load environment from a `.env` file (default: `./.env` if present) |

---

## `trellis run`

Runs a pipeline or plan YAML and prints task outputs.

```bash
trellis run pipelines/sec_extraction.yaml \
  --params '{"ticker": "AAPL", "period_end": "2024-09-30"}' \
  --timeout 120
```

### Required argument

| Argument | Description |
|---|---|
| `PATH` | Path to the pipeline or plan YAML file |

### Data flags

| Flag | Type | Description |
|---|---|---|
| `--inputs JSON` | string | JSON object for `pipeline.inputs` values |
| `--params JSON` | string | JSON object for typed `params` block values |
| `--session JSON` | string | JSON object of pre-seeded session/blackboard values |
| `--session-file PATH` | path | JSON file of session values; merged with `--session` (inline takes precedence on conflicts) |

### Execution flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--timeout SECONDS` | float | none | Per-task timeout in seconds (each attempt) |
| `--concurrency N` | int | none | Max parallel workers for `parallel_over` tasks |
| `--jitter FRACTION` | float | `0.0` | Retry backoff jitter (0.2 = ±20%) |

### Output flags

| Flag | Description |
|---|---|
| `--json` | Print only the final outputs as compact JSON (no headers, stats, or events) |

### LLM provider flags

These set environment variables for the duration of the run only.

| Flag | Environment variable set | Description |
|---|---|---|
| `--llm-provider NAME` | `TRELLIS_LLM_PROVIDER` | Default provider (`openai`, `ollama`, `anthropic`) |
| `--llm-model NAME` | `EXTRACT_TEXT_MODEL` | Default model for tools with a `model` input |
| `--openai-api-key KEY` | `OPENAI_API_KEY` | OpenAI API key |
| `--openai-model NAME` | `OPENAI_MODEL` | OpenAI model override (e.g., `gpt-4o`) |
| `--anthropic-api-key KEY` | `ANTHROPIC_API_KEY` | Anthropic API key |
| `--anthropic-model NAME` | `ANTHROPIC_MODEL` | Anthropic model override |
| `--ollama-host URL` | `OLLAMA_HOST` | Ollama base URL (e.g., `http://localhost:11434`) |
| `--ollama-model NAME` | `OLLAMA_MODEL` | Ollama model override (e.g., `llama3`) |
| `--extract-model NAME` | `EXTRACT_TEXT_MODEL` | Model for `extract_text` tasks |
| `--env-file PATH` | — | Load a `.env` file (default: `./.env` if present) |

---

## Examples

### Validate a pipeline

```bash
trellis validate pipelines/sec_extraction.yaml
```

### Run with typed params

```bash
trellis run pipelines/sec_extraction.yaml \
  --params '{
    "ticker": "AAPL",
    "company": "Apple Inc.",
    "period_end": "2024-09-30",
    "period_type": "annual"
  }'
```

### Run with legacy inputs

```bash
trellis run pipelines/search_summarize.yaml \
  --inputs '{"query": "renewable energy trends 2024"}'
```

### Run with a session file

```bash
trellis run pipelines/spreads/spread.yaml \
  --session-file session.json \
  --timeout 60
```

Where `session.json` might be:

```json
{
  "document_url": "https://example.com/10k.pdf",
  "schema_path": "schemas/income_statement.json"
}
```

### Machine-readable output

```bash
trellis run pipelines/sec_extraction.yaml \
  --params '{"ticker": "AAPL", "period_end": "2024-09-30"}' \
  --json \
  > outputs/result.json
```

### Override LLM provider inline

```bash
trellis run pipelines/summarize.yaml \
  --inputs '{"query": "market trends"}' \
  --openai-api-key $OPENAI_API_KEY \
  --openai-model gpt-4o-mini
```

### Use an `.env` file

```bash
trellis run pipelines/sec_extraction.yaml \
  --params '{"ticker": "MSFT", "period_end": "2024-06-30"}' \
  --env-file .env.production
```

The `.env` file is loaded with `override=False` — variables already in the environment take precedence.

---

## Standard output

When `--json` is **not** set, `trellis run` prints three sections:

```
Outputs:
{
  "fetch": { "status": "success", ... },
  "extract": { "revenue": 391035, ... },
  "export_json": { "path": "/abs/path/result.json", "size": 312 }
}

Stats:
{
  "waves_executed": 5,
  "tasks_executed": 6
}
```

When `--json` is set, only the outputs JSON is printed to stdout — suitable for piping:

```bash
trellis run pipelines/extract.yaml --params '...' --json | jq '.extract.revenue'
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Validation failed (`validate` command) |
| `2` | Bad argument (invalid JSON, missing session file) |
| `3` | Pipeline execution failed |

---

## Environment variables

The CLI reads these variables from the environment or a `.env` file:

| Variable | Used by |
|---|---|
| `OPENAI_API_KEY` | OpenAI-backed tools |
| `ANTHROPIC_API_KEY` | Anthropic-backed tools |
| `OLLAMA_HOST` | Ollama tool calls |
| `TRELLIS_LLM_PROVIDER` | Default provider selection |
| `EXTRACT_TEXT_MODEL` | `extract_from_texts`, `extract_fields` |
| `SERPAPI_API_KEY` | `search_web` with SerpAPI backend |

---

## Next steps

- [API (REST)](interfaces-api.md) — run pipelines over HTTP
- [Configuration & Environment](operations-configuration.md) — full environment variable reference
- [Pipeline DSL reference](PIPELINE-DSL.md) — task syntax, params, retries, fan-out
