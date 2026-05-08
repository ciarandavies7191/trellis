# MCP Server

Trellis can expose its registered tools as [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) tools, allowing Claude Desktop, Continue, and other MCP-compatible hosts to call Trellis tools directly during a conversation — without writing a pipeline YAML at all.

!!! note "Status"
    The MCP server module (`trellis_mcp`) is included in the repository but is not yet wired up. This page describes the intended interface. Track progress in the project issue tracker.

---

## What it enables

An MCP host (e.g., Claude Desktop) connects to the Trellis MCP server over stdio or SSE. The host discovers available tools from the server's tool list, and can invoke them by name with structured inputs. The server executes the tool and returns a result.

This means you can, from a Claude chat session:

- Call `ingest_document` on a local PDF and get a `DocumentHandle`
- Call `select` to pick pages, then `extract_fields` against a schema
- Call `search_web` and get live results back into context

All without writing any code or YAML.

---

## Planned server interface

### Startup (stdio transport)

```bash
python -m trellis_mcp.server
```

The server will listen on stdin/stdout using the MCP JSON-RPC protocol.

### Startup (SSE transport)

```bash
python -m trellis_mcp.server --transport sse --port 3000
```

### Tools exposed

The MCP server will expose each registered Trellis tool as an MCP tool, with:

- `name` — the tool name (e.g., `ingest_document`)
- `description` — from `BaseTool.description`
- `inputSchema` — JSON Schema derived from `BaseTool.get_inputs()`

```json
{
  "tools": [
    {
      "name": "search_web",
      "description": "Search the web using DuckDuckGo or SerpAPI.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query":    {"type": "string"},
          "backend":  {"type": "string", "default": "duckduckgo"},
          "max_results": {"type": "integer", "default": 5}
        },
        "required": ["query"]
      }
    }
  ]
}
```

---

## Claude Desktop configuration (planned)

Add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trellis": {
      "command": "python",
      "args": ["-m", "trellis_mcp.server"],
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "SERPAPI_API_KEY": "..."
      }
    }
  }
}
```

After restarting Claude Desktop, Trellis tools will appear in the tool picker.

---

## Calling tools from a host

Once connected, a host can invoke tools by name. For example, calling `search_web`:

**Host → server (MCP call):**

```json
{
  "method": "tools/call",
  "params": {
    "name": "search_web",
    "arguments": {
      "query": "AAPL FY2024 annual revenue",
      "max_results": 3
    }
  }
}
```

**Server → host (MCP result):**

```json
{
  "content": [
    {
      "type": "text",
      "text": "[{\"title\": \"Apple Reports Fourth Quarter Results\", \"url\": \"...\", \"snippet\": \"...\"}]"
    }
  ]
}
```

---

## Implementation notes

The server is built using the `mcp` Python SDK. Each registered tool in `AsyncToolRegistry` is wrapped as an MCP tool definition. When a call arrives, the server constructs a minimal `ResolutionContext` and invokes the tool via `AsyncToolRegistry.execute()`, then serializes the result back as MCP content.

Tools that return non-JSON-serializable objects (e.g., `DocumentHandle` dataclasses) are serialized with the same `_json_sanitize` helper used by the REST API.

---

## Next steps

- [API (REST)](interfaces-api.md) — run full pipelines over HTTP
- [CLI](interfaces-cli.md) — validate and run pipelines from the terminal
- [Tools & Registry](tools-index.md) — full list of available tools and their inputs
