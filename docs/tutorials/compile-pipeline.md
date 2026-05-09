# Compile a Pipeline from a Prompt

The Trellis compiler turns a natural-language description into a validated, ready-to-run pipeline YAML. This tutorial walks through every mode — CLI, Python SDK, and a realistic end-to-end workflow.

**Requires:** an LLM API key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or similar)  
**Time:** ~10 minutes

---

## How it works

1. You supply a prompt — one sentence or several paragraphs describing what the pipeline should do.
2. The compiler sends the prompt to an LLM together with a system prompt containing the full DSL specification and a live tool catalog derived from your registry.
3. The LLM response is validated with Pydantic (schema, tool names, template references, param declarations) and checked for cycles.
4. If validation fails, the compiler appends the error to the conversation and re-prompts — up to `--max-repairs` additional times (default 2).
5. On success, the validated YAML is returned.

---

## CLI quickstart

```bash
# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Compile and print to stdout
trellis compile "Search the web for the latest developments in solid-state batteries \
  and write a two-paragraph summary."
```

Expected output:

```
Compiling...
Compiled pipeline 'battery_research_summary':
pipeline:
  id: battery_research_summary
  goal: "Search for solid-state battery developments and summarise"
  tasks:
    - id: search
      tool: search_web
      inputs:
        query: "solid-state battery latest developments 2024"

    - id: summarise
      tool: llm_job
      inputs:
        results: "{{search.output}}"
        prompt: |
          Write a two-paragraph summary of the latest developments
          in solid-state batteries based on the search results below.
        max_tokens: 400
```

---

## Write the output to a file

```bash
trellis compile \
  "Fetch Apple's latest 10-K from SEC EDGAR, ingest the document, select the
   Risk Factors section, and extract them as a structured list." \
  --output pipelines/aapl_risks.yaml
```

```
Compiling...
Compiled pipeline 'apple_risk_factors' -> pipelines/aapl_risks.yaml
```

Then validate and run:

```bash
trellis validate pipelines/aapl_risks.yaml
trellis run pipelines/aapl_risks.yaml
```

---

## Pipe-friendly mode

`--json` suppresses all decorative output and prints only the raw YAML — useful for scripting:

```bash
trellis compile "Summarise a PDF report in five executive bullet points" \
  --json > pipelines/pdf_summary.yaml

# Immediately validate what was written
trellis validate pipelines/pdf_summary.yaml
```

---

## Prompt from a file

For complex multi-step pipelines, write your description in a text file so you can revise it easily:

```text title="prompts/sec_full_extraction.txt"
Fetch the most recent 10-K filing for Apple Inc. from SEC EDGAR.
Ingest the full document as a PDF.
Select the pages that contain the income statement and balance sheet.
Load the standard income-statement schema from the registry.
Extract all schema fields from the selected pages.
Export the extracted data as JSON to results/apple_financials.json.
```

```bash
trellis compile --prompt-file prompts/sec_full_extraction.txt \
  --output pipelines/apple_full_extraction.yaml
```

---

## Choose the compilation model

The compiler defaults to `TRELLIS_COMPILER_MODEL` → `TRELLIS_LLM_MODEL` → `openai/gpt-4o-mini`. For complex pipelines, a more capable model produces better first-attempt results:

```bash
trellis compile "Fan-out credit risk assessment across Apple, Microsoft, and Google" \
  --model anthropic/claude-sonnet-4-6 \
  --output pipelines/credit_risk.yaml
```

You can also set `TRELLIS_COMPILER_MODEL` in your `.env` to make this permanent:

```bash
TRELLIS_COMPILER_MODEL=anthropic/claude-sonnet-4-6
```

---

## Repair attempts

If the first LLM response fails validation (unknown tool name, missing param declaration, cyclic dependency), the compiler automatically re-prompts with the error. The default is 2 repair attempts after the initial try (3 total). Increase it for ambiguous prompts:

```bash
trellis compile "Fan-out analysis over a dynamic list with session state" \
  --max-repairs 4
```

A repair attempt count appears in the output header when repairs were needed:

```
Compiled pipeline 'fan_out_analysis' (2 repair(s) needed):
```

---

## Python SDK

The compiler is available directly as a Python class, which is useful when you want to embed pipeline generation inside your own application:

```python
import asyncio
from trellis.compiler import PipelineCompiler

compiler = PipelineCompiler()

result = asyncio.run(compiler.compile(
    "Fetch Apple's latest 10-K from SEC EDGAR and summarise key risk factors."
))

print(result.yaml_text)
print(f"Pipeline id:  {result.pipeline.id}")
print(f"Attempts:     {result.attempts}")

if result.repair_history:
    print(f"\n{len(result.repair_history)} repair(s) were needed:")
    for i, (bad_yaml, error) in enumerate(result.repair_history, 1):
        print(f"  Attempt {i} error: {error[:80]}")
```

### Constructor options

```python
from trellis.compiler import PipelineCompiler
from trellis.tools.registry import build_default_registry

compiler = PipelineCompiler(
    registry=build_default_registry(),   # default; swap for a custom registry
    model="anthropic/claude-sonnet-4-6", # override compilation model
)
```

### Per-call options

```python
result = await compiler.compile(
    prompt,
    model="openai/gpt-4o",   # override for this call only
    max_repair_attempts=4,
)
```

### CompilerResult fields

| Field | Type | Description |
|---|---|---|
| `yaml_text` | `str` | The validated YAML string (code fences stripped) |
| `pipeline` | `Pipeline \| None` | Parsed Pipeline object, or `None` if a Plan was compiled |
| `plan` | `Plan \| None` | Parsed Plan object, or `None` if a Pipeline was compiled |
| `artifact` | `Pipeline \| Plan` | Convenience property — returns whichever is non-`None` |
| `is_pipeline` | `bool` | `True` when a Pipeline was compiled |
| `is_plan` | `bool` | `True` when a Plan was compiled |
| `attempts` | `int` | Total LLM calls made (`1` = first-try success) |
| `repair_history` | `list[tuple[str, str]]` | `(broken_yaml, error_message)` pairs from failed attempts |

### Handling CompilerError

```python
from trellis.compiler import PipelineCompiler, CompilerError

compiler = PipelineCompiler()

try:
    result = await compiler.compile(prompt, max_repair_attempts=1)
except CompilerError as exc:
    print(f"Compilation failed after {exc.attempts} attempt(s)")
    print(f"Last error: {exc.last_error}")
    print(f"Last LLM output:\n{exc.last_yaml}")
```

---

## End-to-end example: SEC extraction pipeline

This shows the full loop: compile, validate, run.

**Step 1 — Write a detailed prompt**

```bash
cat > prompts/sec_risks.txt << 'EOF'
Fetch the most recent 10-K filing for the company identified by the ticker
parameter from SEC EDGAR. Ingest the document. Select the pages that contain
the Risk Factors section. Extract all named risk factors as a structured list.
Export the results as JSON.
EOF
```

**Step 2 — Compile**

```bash
trellis compile --prompt-file prompts/sec_risks.txt \
  --model anthropic/claude-sonnet-4-6 \
  --output pipelines/sec_risks.yaml
```

**Step 3 — Validate**

```bash
trellis validate pipelines/sec_risks.yaml
```

**Step 4 — Run with a ticker parameter**

```bash
trellis run pipelines/sec_risks.yaml \
  --params '{"ticker": "AAPL"}' \
  --timeout 120 \
  --env-file .env
```

---

## Tips for better prompts

| Tip | Example |
|---|---|
| Name the tools you want used | "...using `fetch_data` with `source: sec_edgar`..." |
| Describe the output shape | "...export the result as JSON to `results/out.json`" |
| Mention when fan-out is needed | "...process each company in parallel..." |
| Specify typed parameters | "...parametrised by `ticker` (string) and `fiscal_year` (integer, default 2024)..." |
| Keep the prompt task-oriented | Describe *what* the pipeline does, not *how* the YAML should look |

Longer, more specific prompts produce more accurate pipelines. When the first attempt fails validation, the error message is shown in the `repair_history` — reading it often reveals what the prompt was missing.

---

## When to write YAML by hand

The compiler is a productivity tool, not a replacement for understanding the DSL. Write YAML directly when:

- You need precise control over retry counts, per-task timeouts, or `await` barriers
- You are authoring a plan with multi-pipeline session contracts
- The compiled result is close but needs a few manual tweaks

See the [Pipeline DSL reference](../PIPELINE-DSL.md) for the full syntax.

---

## Next steps

- [Pipeline DSL reference](../PIPELINE-DSL.md) — full task and template syntax
- [CLI reference](../interfaces-cli.md) — all `trellis compile` flags
- [Tools reference](../tools-index.md) — what each tool does and its inputs
