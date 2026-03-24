# Integrations — OpenPill

How to connect **OpenPill** to editors, agents, and HTTP clients: **OpenClaw**, **MCP (stdio)**, **REST**, and the **OpenAI-compatible proxy**.

## When to use MCP vs REST vs proxy

| Layer | Best for |
|-------|----------|
| **MCP (`server.py`)** | Cursor, Claude Desktop, and any host that speaks MCP over stdio or SSE. Direct tool calls; no HTTP server required for stdio. |
| **REST (`api.py`)** | ChatGPT Actions, Open WebUI, browser extensions, mobile apps, scripts, or anything that uses HTTP/OpenAPI. |
| **Proxy (`proxy.py`)** | Apps that only support a **custom OpenAI-compatible API URL** (e.g. some chat UIs). Injects relevant pills into the context and can extract new pills from replies. |

Use **MCP** when your environment already supports MCP (e.g. Cursor). Use **REST** when you need a stable HTTP contract, Swagger, or keys/headers. Use the **proxy** when you cannot add MCP or custom REST but can point the client at `http://host:4000/v1`.

## OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) (and similar agent frameworks) can orchestrate tools and memory. For the latest install and wiring steps, follow **OpenClaw’s official documentation** for your version.

To use OpenPill with an MCP-capable stack, register this project’s MCP server (see **MCP stdio** below) so tools like `semantic_search` and `ingest_text` are available to the agent.

### OpenClaw container baseline (post-reboot)

If OpenClaw runs in Docker while MongoDB runs on the host, use:

- `MONGO_URI=mongodb://host.docker.internal:27017/?directConnection=true`

Why this matters: when MongoDB is initialized as a replica set, server discovery can return internal hostnames (for example `localhost:27017`) that are unreachable from inside the OpenClaw container. `directConnection=true` keeps the client pinned to the reachable host endpoint.

Quick validation:

```bash
bash scripts/openclaw_guardrail_smoke.sh
```

This executes 3 fixed OpenClaw prompts and fails if `semantic_search` is not called or if memory backend connection errors are detected.

### Autonomous markdown -> OpenPill ingestion

If OpenClaw (or your workflow) keeps memory in markdown files, OpenPill does not ingest those automatically unless you wire a bridge.

Use:

```bash
python scripts/ingest_markdown_memory.py --root /path/to/memory/dir --interval 60
```

What it does:

- scans `.md` files under the root
- ingests only changed files (`POST /pills/ingest`)
- uses deterministic idempotency keys
- stores local ingest state in `.openpill_md_ingest_state.json` (configurable)

Equivalent Make target:

```bash
make md-watch ROOT=/path/to/memory/dir INTERVAL=60
```

You can auto-start this on login (cross-platform wrapper):

```bash
make md-watch-install ROOT=/path/to/memory/dir INTERVAL=60
```

This installs:
- macOS: launchd user agent
- Linux: systemd user service
- Windows: Startup launcher

## MCP (stdio) — Cursor and others

Run the MCP server from the repo root (after `pip install -r requirements.txt` and MongoDB):

```bash
python server.py
```

**Cursor** — use a project or user MCP config pointing at this repo, for example:

```json
{
  "mcpServers": {
    "openpill": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "/absolute/path/to/knowledge-pill-mcp",
      "env": {
        "MONGO_URI": "mongodb://localhost:27017",
        "MONGO_DB": "knowledge_pills_db",
        "EMBEDDING_MODEL": "text-embedding-3-small"
      }
    }
  }
}
```

Optional: `python server.py --sse` for SSE transport when the host expects a remote MCP connection.

See also the **Cursor Integration** section in the main [`README.md`](../README.md).

## REST API

- **Base URL:** `http://localhost:8080` (default; override via Uvicorn when running `api.py`).
- **OpenAPI:** `/openapi.json` and interactive `/docs`.
- **Health (probes):** `GET /health` — returns `{"status":"ok"}`; does not require MongoDB.

When exposing the API beyond localhost, set optional **`OPENPILL_API_KEY`** (legacy: `MEMORA_API_KEY`, `KNOWLEDGE_PILL_API_KEY`) and send `Authorization: Bearer <key>` or `X-API-Key: <key>`. See [`OPS.md`](OPS.md).

**Idempotent ingest:** For `POST /pills/ingest` and `POST /pills/ingest-conversation`, you may send header **`Idempotency-Key`**. Retries with the **same key and identical JSON body** receive the same successful response; the same key with a **different body** returns **409 Conflict**. Keys expire from storage after the TTL (see OPS).

## Proxy (OpenAI-compatible)

Run `python proxy.py` (default port **4000**). Point clients at:

`http://localhost:4000/v1`

Set `api_base` (or your client’s equivalent) to that URL so requests go through the proxy, which can inject pills and run extraction. Details: [`README.md`](../README.md) — *Access Layers* and proxy sections.
