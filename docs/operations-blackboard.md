# Persistence & Multi-tenancy (Blackboard)

The **blackboard** is a tenant-scoped key/value store that persists values across tasks within a pipeline and across sub-pipelines within a plan. Tasks write to it via the `store` tool and read from it via `{{session.key}}` template references.

---

## Concepts

| Term | Meaning |
|---|---|
| **Blackboard** | The persistence layer — an interface with `read_many`, `write`, and `get_all` methods |
| **Session** | The slice of the blackboard visible to a single pipeline execution — a dict of `key → value` |
| **Tenant** | A named namespace that isolates one user or workspace from another |
| **`store` task** | The pipeline tool that writes a value to the blackboard under a given key |
| **`{{session.key}}`** | Template expression that reads a value from the session dict |

---

## The `Blackboard` interface

```python
class Blackboard:
    def get_all(self, tenant_id: str) -> dict[str, Any]: ...
    def read_many(self, tenant_id: str, keys: Iterable[str]) -> dict[str, Any]: ...
    def write(self, tenant_id: str, key: str, value: Any, *, append: bool = False) -> None: ...
```

| Method | Description |
|---|---|
| `get_all(tenant_id)` | Return every key/value pair for the tenant — used by `run_plan` to expose the full session to each sub-pipeline |
| `read_many(tenant_id, keys)` | Return a subset of keys — used when a pipeline declares a `reads` list |
| `write(tenant_id, key, value, append=False)` | Write a value; if `append=True`, adds to a list (creating one if the key doesn't exist) |

The interface is intentionally narrow. A future Redis, Postgres, or Prefect Blocks implementation needs only these three methods.

### `InMemoryBlackboard`

The default implementation. Data lives in a plain Python dict and is lost when the process exits:

```python
# Data layout
{
  "default":   {"filing_text": "...", "extracted_fields": {...}},
  "acme-corp": {"filing_text": "...", "extracted_fields": {...}},
}
```

Each `Orchestrator` instance owns one `InMemoryBlackboard` by default. For the in-memory run queue, each background run creates its own `Orchestrator`, so runs are fully isolated from each other.

---

## Tenant isolation

Every `Orchestrator` is constructed with a `tenant_id` (default: `"default"`). All blackboard reads and writes are keyed by that tenant ID — a write to key `"filing_text"` for tenant `"acme-corp"` is completely invisible to tenant `"beta-corp"`.

```python
from trellis.execution.orchestrator import Orchestrator
from trellis.execution.blackboard import InMemoryBlackboard

# Shared blackboard, two tenants
bb = InMemoryBlackboard()

orch_a = Orchestrator(blackboard=bb, tenant_id="acme-corp")
orch_b = Orchestrator(blackboard=bb, tenant_id="beta-corp")

# orch_a and orch_b share the same InMemoryBlackboard object
# but write/read from separate namespaces
```

For the REST API's queued runs, `tenant_id` is passed in the `POST /pipelines/run_async` request body. Each queued run creates its own `Orchestrator` with that tenant ID.

---

## Writing with the `store` tool

The `store` tool is how pipelines write to the blackboard:

```yaml
- id: cache_filing
  tool: store
  inputs:
    key: filing_text
    value: "{{ingest.output}}"

- id: cache_fields
  tool: store
  inputs:
    key: extracted_fields
    value: "{{extract.output}}"
    append: false        # default — overwrites any existing value
```

**Append mode** — accumulates results into a list. Useful when a `parallel_over` fan-out runs `store` once per item:

```yaml
- id: store_each_result
  tool: store
  parallel_over: "{{fetch.output.results}}"
  inputs:
    key: all_filings
    value: "{{item}}"
    append: true         # each item is appended to the list at "all_filings"
```

`store` echoes `value` as its task output so downstream tasks can still reference `{{cache_filing.output}}` normally.

### `store` task fields

| Input | Type | Default | Description |
|---|---|---|---|
| `key` | string | required | Blackboard key to write |
| `value` | any | required | Value to store; any pipeline-serializable type |
| `append` | bool | `false` | Append to list rather than overwrite |

---

## Reading with `{{session.key}}`

Values in the session are available to all tasks in the same pipeline via `{{session.key}}`:

```yaml
- id: summarize
  tool: llm_job
  inputs:
    context: "{{session.extracted_fields}}"
    prompt: "Summarize the extracted fields."
  await:
    - cache_fields    # ensure cache_fields ran before summarize
```

The `await` barrier here is essential — `summarize` has no template reference to `cache_fields.output`, so without `await` the executor might schedule them in the same wave before the session value is written.

The session dict passed to a pipeline at construction time seeds these values before any task runs. Pre-seeded values are available immediately without any `store` task:

```python
result = await orch.run_pipeline(
    pipeline,
    session={"auth_token": "...", "company_cik": "0000320193"},
)
```

---

## Session flow in plans

When running a **plan** (`Orchestrator.run_plan`), the blackboard is the communication channel between sub-pipelines. After each sub-pipeline completes, the full blackboard is exposed as the session for the next sub-pipeline:

```
Plan execution flow
───────────────────
Wave 1: fetch_10k  ──→  store: filing_text
Wave 1: fetch_schema ─→ store: schema_handle

Wave 2: spread        ←  session.filing_text  (written by fetch_10k)
                      ←  session.schema_handle (written by fetch_schema)
```

The plan YAML declares which keys each sub-pipeline produces (`stores`) and consumes (`reads`). The executor uses these declarations to:

1. **Infer sub-pipeline ordering** — a sub-pipeline that `reads` a key must run after the one that `stores` it.
2. **Validate contracts** — `trellis validate plan.yaml` checks that every `reads` key is satisfied and that no two sub-pipelines write the same key.

```yaml
plan:
  id: spreading_plan
  sub_pipelines:
    - id: fetch_10k
      stores: [filing_text]

    - id: fetch_schema
      stores: [schema_handle]

    - id: spread
      reads: [filing_text, schema_handle]   # ← depends on both above
```

---

## Swapping the blackboard backend

Pass a custom `Blackboard` implementation to `Orchestrator` to replace `InMemoryBlackboard`. The contract is three methods — nothing else needs to change:

```python
import redis
from trellis.execution.blackboard import Blackboard

class RedisBlackboard(Blackboard):
    def __init__(self, url: str) -> None:
        self._r = redis.from_url(url)

    def get_all(self, tenant_id: str) -> dict:
        keys = self._r.hkeys(tenant_id)
        if not keys:
            return {}
        return {k.decode(): json.loads(v) for k, v in
                zip(keys, self._r.hmget(tenant_id, keys))}

    def read_many(self, tenant_id: str, keys) -> dict:
        vals = self._r.hmget(tenant_id, list(keys))
        return {k: json.loads(v) for k, v in zip(keys, vals) if v is not None}

    def write(self, tenant_id: str, key: str, value, *, append: bool = False) -> None:
        if append:
            existing = self._r.hget(tenant_id, key)
            lst = json.loads(existing) if existing else []
            lst.append(value)
            self._r.hset(tenant_id, key, json.dumps(lst))
        else:
            self._r.hset(tenant_id, key, json.dumps(value))

bb = RedisBlackboard("redis://localhost:6379")
orch = Orchestrator(blackboard=bb, tenant_id="acme-corp")
```

---

## Multi-tenancy in the REST API

Pass `tenant_id` in the `POST /pipelines/run_async` request body to isolate runs by workspace:

```bash
curl -X POST http://localhost:8000/pipelines/run_async \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": { ... },
    "tenant_id": "acme-corp"
  }'
```

Each queued run creates its own `Orchestrator` with the specified `tenant_id`. All `store` writes from that run are namespaced under `"acme-corp"` and invisible to other tenants.

For the synchronous endpoint (`POST /pipelines/run`), the orchestrator always uses the `"default"` tenant and a fresh `InMemoryBlackboard`, so there is no cross-run bleed.

---

## Inspecting blackboard state

After a plan run, `PlanRunResult.blackboard` contains the full final blackboard for the tenant:

```python
plan_result = await orch.run_plan(plan, plan_dir)
print(plan_result.blackboard)
# {"filing_text": {...}, "schema_handle": {...}, "extracted_fields": {...}}
```

From the CLI with `--json`:

```bash
trellis run plans/spreading_plan.yaml --json
# prints the final blackboard as JSON
```

---

## Next steps

- [Execution Backends & Run Queue](operations-execution.md) — executor options, timeouts, retries, and the background queue
- [Pipeline DSL Reference](PIPELINE-DSL.md) — `store` task syntax, `await` barriers, `{{session.key}}` templates
- [Tools & Registry](tools-index.md) — `store` tool inputs reference
