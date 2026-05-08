# Web Search and Summarization

Search the web for any topic, collect the top results, and distil them into a structured summary using an LLM. The result is persisted to the blackboard so downstream pipelines can read it.

**Tools used:** `search_web`, `llm_job`, `store`  
**Requires:** LLM API key (`OPENAI_API_KEY` or equivalent)

---

## The pipeline

```yaml title="pipelines/web_search_summarize.yaml"
pipeline:
  id: web_search_summarize
  goal: "Search for '{{params.topic}}' and produce a structured summary"

  params:
    topic:
      type: string
      description: "Search topic or question"
    num_results:
      type: integer
      description: "Number of web results to retrieve"
      default: 5
    store_key:
      type: string
      description: "Blackboard key to write the summary to"
      default: web_summary

  tasks:
    - id: search
      tool: search_web
      inputs:
        query: "{{params.topic}}"
        top_n: "{{params.num_results}}"

    - id: summarize
      tool: llm_job
      inputs:
        prompt: |
          You are a research assistant. Review the web search results below and
          produce a concise summary with three sections:

          1. **Key findings** — 3–5 bullet points distilling the most important facts
          2. **Notable sources** — list the 2–3 most authoritative URLs
          3. **Gaps** — what the results do not cover or answer

          Keep each section brief and factual. Do not pad with filler.
        results: "{{search.output.results}}"

    - id: persist
      tool: store
      inputs:
        key: "{{params.store_key}}"
        value: "{{summarize.output}}"
```

### How it works

- `search_web` queries the web (DuckDuckGo by default; SerpAPI if configured) and returns a list of `{title, snippet, url, source_query}` objects.
- `llm_job` receives the results list as the `results` context key. Trellis prepends it to the prompt automatically, so the model sees the raw result objects alongside the instruction.
- `store` writes the LLM response string to the blackboard under the configured key.

The three tasks form a linear chain: each wave runs one task. `summarize` cannot start until `search` has completed because `{{search.output.results}}` must be resolved first.

---

## Run it — Python SDK

Save the YAML above as `pipelines/web_search_summarize.yaml`, then:

```python
import asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

async def main():
    pipeline = Pipeline.from_yaml_file("pipelines/web_search_summarize.yaml")
    orch = Orchestrator()

    result = await orch.run_pipeline(
        pipeline,
        params={
            "topic": "Apple Inc earnings Q1 2025",
            "num_results": 5,
        },
    )

    # The LLM summary string
    print(result.outputs["summarize"])

    # What was written to the blackboard
    print(result.outputs["persist"])

asyncio.run(main())
```

Or define the pipeline inline without a file:

```python
import asyncio
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator

YAML = """
pipeline:
  id: web_search_summarize
  goal: Search and summarize
  params:
    topic:
      type: string
  tasks:
    - id: search
      tool: search_web
      inputs:
        query: "{{params.topic}}"
        top_n: 5
    - id: summarize
      tool: llm_job
      inputs:
        prompt: "Summarize these results in 3 bullet points."
        results: "{{search.output.results}}"
"""

async def main():
    pipeline = Pipeline.from_yaml(YAML)
    orch = Orchestrator()
    result = await orch.run_pipeline(pipeline, params={"topic": "Python 3.13 release"})
    print(result.outputs["summarize"])

asyncio.run(main())
```

---

## Run it — CLI

```bash
trellis run pipelines/web_search_summarize.yaml \
  --params '{"topic": "Apple Inc earnings Q1 2025", "num_results": 5}'
```

---

## Expected output

`result.outputs` is keyed by task ID:

```python
{
    "search": {
        "status": "success",
        "results": [
            {
                "title": "Apple Reports First Quarter Results",
                "snippet": "Apple today announced financial results for its fiscal 2025 first quarter...",
                "url": "https://www.apple.com/newsroom/2025/01/apple-reports-first-quarter-results/",
                "source_query": "Apple Inc earnings Q1 2025"
            },
            {
                "title": "AAPL Q1 2025 Earnings Beat Estimates",
                "snippet": "Apple's revenue came in at $124.3 billion, ahead of analyst expectations...",
                "url": "https://finance.yahoo.com/...",
                "source_query": "Apple Inc earnings Q1 2025"
            },
            # ... up to num_results entries
        ]
    },
    "summarize": (
        "**Key findings**\n"
        "- Apple reported Q1 2025 revenue of $124.3B, up 4% year-over-year\n"
        "- iPhone revenue of $69.1B drove the quarter; Services hit a record $26.3B\n"
        "- EPS of $2.40 beat the consensus estimate of $2.35\n\n"
        "**Notable sources**\n"
        "- https://www.apple.com/newsroom/2025/01/apple-reports-first-quarter-results/\n"
        "- https://finance.yahoo.com/...\n\n"
        "**Gaps**\n"
        "- Results don't cover geographic revenue breakdown or Vision Pro sell-through"
    ),
    "persist": {
        "status": "success",
        "key": "web_summary",
        "append": False,
        "value": "**Key findings**\n..."
    }
}
```

`result.waves_executed` will be `3` — one wave per task since each depends on the previous.

---

## Variations

**Multiple queries in parallel**

Fan out a list of queries and run them concurrently:

```yaml
params:
  queries:
    type: list

tasks:
  - id: searches
    tool: search_web
    parallel_over: "{{params.queries}}"
    inputs:
      query: "{{item}}"
      top_n: 3
```

`result.outputs["searches"]` is a list with one result dict per query, in the same order as `params.queries`.

**Use SerpAPI instead of DuckDuckGo**

```yaml
- id: search
  tool: search_web
  inputs:
    query: "{{params.topic}}"
    provider: serpapi
    top_n: 10
```

Set `SERPAPI_KEY` in your environment or `.env` file.

---

## Next steps

- [PDF Ingest & Extraction](pdf-ingest-extract.md) — apply similar LLM processing to document content
- [SEC Filing Extraction](sec-field-extraction.md) — structured field extraction against a schema
