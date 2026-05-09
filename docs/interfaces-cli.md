# CLI

The `trellis` command-line tool validates, runs, and compiles pipelines and plans. It is installed as part of the `trellis-pipelines` package and reads configuration from `.env` files and CLI flags.

```bash
pip install trellis-pipelines
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
| `compile [PROMPT]` | Compile a natural-language prompt into a validated pipeline YAML |

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

## `trellis compile`

Compiles a natural-language description into a validated Trellis pipeline YAML. The compiler calls an LLM, validates the response against the Pipeline and Plan models (including cycle detection), and retries with the error context if the first attempt fails.

```bash
trellis compile "Fetch Apple's latest 10-K from SEC EDGAR and summarise key risks"
```

Prints the compiled YAML to stdout with a header showing the pipeline ID and any repair attempts needed.

### Arguments and options

| Argument / Flag | Default | Description |
|---|---|---|
| `PROMPT` | — | Natural-language description of the pipeline (positional). One of `PROMPT` or `--prompt-file` is required. |
| `--prompt-file PATH` | — | Read the prompt from a text file instead of the command line. |
| `--output / -o PATH` | — | Write the compiled YAML to a file. When set, the YAML is not printed to stdout — only a confirmation line is shown. |
| `--model TEXT` | `TRELLIS_COMPILER_MODEL` | litellm model string for the compilation call (e.g. `anthropic/claude-haiku-4-5-20251001`). Falls back to `TRELLIS_LLM_MODEL` then `openai/gpt-4o-mini`. |
| `--max-repairs N` | `2` | Maximum number of re-prompts after a validation failure before giving up. |
| `--json` | off | Print only the raw YAML to stdout with no decorative headers or stats. Useful for piping to files. |
| `--env-file PATH` | `./.env` | Load environment variables from a `.env` file before compiling. |

Exactly one of `PROMPT` or `--prompt-file` must be provided. Passing both is an error.

### How the compiler works

1. The compiler sends the prompt to the configured LLM together with a system prompt that contains the full DSL specification and a live tool catalog derived from the registry.
2. The LLM's response is validated against the Pipeline or Plan Pydantic models and checked for cycles.
3. If validation fails, the compiler appends the error to the conversation and re-sends, up to `--max-repairs` additional times.
4. On success, the validated YAML is returned.

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

### Compile a pipeline from a prompt

```bash
# Print the compiled YAML to stdout
trellis compile "Fetch Apple's latest 10-K from SEC EDGAR and summarise key risks"

# Write to a file
trellis compile "Summarise a PDF report in five executive bullet points" \
  --output pipelines/pdf_summary.yaml

# Use a smarter model for complex pipelines
trellis compile "Multi-step SEC extraction with schema-driven field extraction" \
  --model anthropic/claude-sonnet-4-6 \
  --output pipelines/sec_extract.yaml

# Allow more repair attempts for tricky prompts
trellis compile "Fan-out credit risk assessment across ten companies" \
  --max-repairs 4

# Machine-readable: raw YAML only (good for piping or scripts)
trellis compile "Search the web for renewable energy trends and summarise" \
  --json > pipelines/web_search.yaml
```

### Compile from a prompt file

For long or structured prompts, store the description in a text file:

```text title="prompts/my_pipeline.txt"
Fetch the latest 10-K filings from SEC EDGAR for Apple, Microsoft, and Google.
Ingest each document, select the Management Discussion & Analysis section,
extract the key risk factors as a structured list, and export the results as JSON.
```

```bash
trellis compile --prompt-file prompts/my_pipeline.txt \
  --output pipelines/multi_company_risks.yaml

# Then validate and run
trellis validate pipelines/multi_company_risks.yaml
trellis run pipelines/multi_company_risks.yaml
```

### Compile then immediately validate

```bash
trellis compile "Fetch AAPL 10-K and summarise risk factors" \
  --output pipelines/aapl_risks.yaml \
&& trellis validate pipelines/aapl_risks.yaml
```

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

| Code | Meaning | Commands |
|---|---|---|
| `0` | Success | all |
| `1` | Validation or compilation failure | `validate`, `compile` |
| `2` | Bad argument (missing prompt, invalid JSON, missing file) | all |
| `3` | Pipeline execution failed | `run` |

---

## Environment variables

The CLI reads these variables from the environment or a `.env` file:

| Variable | Used by |
|---|---|
| `OPENAI_API_KEY` | OpenAI-backed tools and compiler |
| `ANTHROPIC_API_KEY` | Anthropic-backed tools and compiler |
| `OLLAMA_HOST` | Ollama tool calls |
| `TRELLIS_LLM_PROVIDER` | Default provider selection |
| `TRELLIS_LLM_MODEL` | Default model for `llm_job` and compiler fallback |
| `TRELLIS_COMPILER_MODEL` | Model used by `trellis compile` (overrides `TRELLIS_LLM_MODEL`) |
| `EXTRACT_TEXT_MODEL` | `extract_from_texts`, `extract_fields` |
| `SERPAPI_API_KEY` | `search_web` with SerpAPI backend |

---

## Next steps

- [Compile from prompt tutorial](tutorials/compile-pipeline.md) — end-to-end walkthrough of compiling, validating, and running a generated pipeline
- [API (REST)](interfaces-api.md) — run pipelines over HTTP
- [Configuration & Environment](operations-configuration.md) — full environment variable reference
- [Pipeline DSL reference](PIPELINE-DSL.md) — task syntax, params, retries, fan-out
