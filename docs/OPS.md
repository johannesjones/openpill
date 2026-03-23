# Operations guide — OpenPill

## Backups

- **MongoDB:** Take regular dumps of the database used by `MONGO_URI` / `OPENPILL_DB` (legacy: `MEMORA_DB`, `MONGO_DB`; default `knowledge_pills_db`).
  ```bash
  mongodump --uri="$MONGO_URI" --db=knowledge_pills_db --out=./backup-$(date +%Y%m%d)
  ```
- **Restore:** `mongorestore` to the same URI/db.
- **Audit log:** Janitor writes to collection `janitor_audit_log` in the same database; include in backup scope if you rely on merge history.

## Embedding model changes

- Pills store **vectors** from `EMBEDDING_MODEL`. If you **change** the model, cosine similarity and duplicate detection shift; ideally **re-embed** all active pills:
  ```bash
  python backfill_embeddings.py
  ```
- After re-embed, review near-duplicate behavior (`EXTRACTOR_DUPLICATE_THRESHOLD`, etc.).

## Environment matrix

| Scenario | `EXTRACTOR_MODEL` | `EMBEDDING_MODEL` | Notes |
|----------|-------------------|-------------------|--------|
| OpenAI cloud | `gpt-4o-mini` | `text-embedding-3-small` | Set `OPENAI_API_KEY`. |
| Local Ollama | `ollama/llama3` | `ollama/nomic-embed-text` | Run Ollama; no cloud key required for local models. |
| Hybrid | Cloud LLM + local embed | Mix per LiteLLM docs | Ensure dimensions match your similarity code paths. |

## Ports (defaults)

| Service | Port | Env override |
|---------|------|--------------|
| REST API (`api.py`) | 8080 | Uvicorn CLI / code |
| MCP proxy (`proxy.py`) | 4000 | `PROXY_PORT` |
| MongoDB (compose) | 27017 | `MONGO_URI` |

## Health checks

- `GET /health` on the API → `{"status":"ok"}` (no DB required). Stays **unauthenticated** even when `OPENPILL_API_KEY` (legacy: `MEMORA_API_KEY`, `KNOWLEDGE_PILL_API_KEY`) is set (suitable for load-balancer probes).
- `GET /stats` → aggregate counts (requires DB). **Requires API key** when auth is enabled.

## REST API authentication (optional)

- **`OPENPILL_API_KEY`** — Preferred auth env var. If unset, the server also checks legacy `MEMORA_API_KEY` and `KNOWLEDGE_PILL_API_KEY`.
- If no API key env var is set, all routes behave as before (local dev). If set, clients must send either:
  - `Authorization: Bearer <key>`, or
  - `X-API-Key: <key>`
- **Public without key** (when the env var is set): `GET /health`, `GET /docs`, `GET /openapi.json`, `GET /redoc`, `GET /`, `GET /app`, and static files under `/static/`.

## Structured request logging

- The API logs one JSON line per request on the logger `openpill.api` with fields: `event`, `method`, `path`, `status_code`, `duration_ms`, and optional `request_id` (from client header `X-Request-Id` if present). No secrets or bodies are logged.

## Ingest idempotency

- **`POST /pills/ingest`** and **`POST /pills/ingest-conversation`** accept optional header **`Idempotency-Key`**.
- The server hashes the **raw request body**; the same key + same body replays the **first successful** JSON response while the record exists in MongoDB.
- The same key with a **different body** returns **409 Conflict** (client must use a new key).
- Records live in collection **`idempotency_keys`** with a TTL on `created_at` (default **72 hours**, override with **`IDEMPOTENCY_TTL_SECONDS`**). Clients should only retry with the same key when the request body is identical.

## Replica set

- Change streams (`watchdog.py`) require a **replica set**. The provided `docker-compose.yml` configures one for local dev.
