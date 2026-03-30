# OpenPill

Long-term memory layer for agents.
OpenPill stores distilled memory entries in MongoDB and exposes them via MCP, REST, and an OpenAI-compatible proxy.
Here, a "pill" means a compact knowledge pill: a small, retrievable unit of useful memory.

## Why Not Markdown-Only Memory?

OpenClaw's markdown memory is great for fast, personal note-taking. OpenPill is for cases where memory must be reliable under load, across agents, and over time.

- **More reliable retrieval:** semantic search and neighbor expansion instead of plain file lookup.
- **Safer operations:** API/auth boundaries, idempotent ingest, and explicit health checks.
- **Cost control by design:** local-first model policy with optional external-model guards.
- **Production readiness:** backup/restore workflows and deployment patterns for VM/cloud usage.

In short: markdown is a good scratchpad; OpenPill is the memory layer when reliability and control matter.

## Start Here

- **Quick setup:** copy env, start Mongo, install deps, run API/MCP.
- **Deep details:** use the docs index below (recommended).

```bash
cp .env.example .env
docker compose up -d
pip install -r requirements.txt
python api.py      # http://localhost:8080/docs
# or
python server.py   # MCP (stdio)
```

## Common Commands

```bash
make help
make test-unit
make ci-local
make tool-semantic Q="openclaw integration" LIMIT=10 HYBRID=true
make md-watch ROOT=. INTERVAL=60
```

## Documentation Index

- **Integrations (OpenClaw, MCP, REST, proxy, container pitfalls):** [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md)
- **Operations (env vars, model policy, hybrid retrieval, auth, backups):** [`docs/OPS.md`](docs/OPS.md)
- **Production-ish VM blueprint (GCP/Hetzner):** [`docs/DEPLOY_PROD.md`](docs/DEPLOY_PROD.md)
- **External A/B protocol and cost caps:** [`docs/AB_PROTOCOL.md`](docs/AB_PROTOCOL.md)
- **Research and roadmap:** [`RESEARCH_AND_ROADMAP.md`](RESEARCH_AND_ROADMAP.md)
- **Agent behavior policy for tool-capable agents:** [`AGENTS.md`](AGENTS.md)
- **Project intent/values:** [`SOUL.md`](SOUL.md)

## Access Layers

- **MCP (`server.py`)** for Cursor/Claude Desktop and MCP-capable hosts.
- **REST (`api.py`)** for HTTP clients, scripts, Actions, and web tooling.
- **Proxy (`proxy.py`)** for OpenAI-compatible clients that need automatic context injection.

## Testing

```bash
pytest tests/ -v
bash scripts/integration_mongo_smoke.sh
bash scripts/openclaw_guardrail_smoke.sh
```

Latest CI should be green before release/tag decisions.
