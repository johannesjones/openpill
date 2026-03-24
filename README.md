# OpenPill

Long-term memory layer for the AI Agent architecture (Wave 3).
Stores distilled memory entries in MongoDB, exposes them via
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/), and
provides autonomous maintenance through LLM-powered extraction, deduplication,
and consolidation -- following Peter Steinberger's "Durable Memory" approach.

## Access Layers

```
                  ┌───────────────────────────────────────────────────┐
Cursor / Claude   │  server.py   (MCP)                               │
Desktop           │                                                   │
                  ├───────────────────────────────────────────────────┤
ChatGPT Actions   │  api.py      (REST API, FastAPI)                 │
Open WebUI        │  GET /pills/search, /pills/semantic, POST /pills │
Scripts           │  Swagger UI at /docs, OpenAPI at /openapi.json   │
                  ├───────────────────────────────────────────────────┤
Any chat tool     │  proxy.py    (OpenAI-compatible proxy)           │
(custom API URL)  │  Auto-injects relevant pills, auto-extracts new │
                  │  ones. Set api_base=http://localhost:4000/v1     │
                  └───────────────────────────────────────────────────┘
                                        │
                              ┌─────────┴─────────┐
                              │  Shared Core       │
                              │  db.py             │
                              │  embeddings.py     │
                              │  extractor.py      │
                              │  pill_relations.py │
                              └────────────────────┘
```

## Three-Tier Memory System

```
Short-Term     Current chat context window
    |
Mid-Term       Local markdown files (MEMORY.md) with distilled facts
    |           <- sync_memory.py exports from Long-Term
Long-Term      MongoDB with vector embeddings + MCP interface
                <- extractor.py ingests, janitor.py cleans
```

**Docs:** `MEMORY.md` is **auto-generated** by `sync_memory.py` (mid-term export)—do not edit by hand. For **research notes and a phased roadmap** (graph memory, RL, future directions), see [`RESEARCH_AND_ROADMAP.md`](RESEARCH_AND_ROADMAP.md). Operations (backup, embeddings, ports): [`docs/OPS.md`](docs/OPS.md). **Integrations** (OpenClaw, MCP, REST, proxy vs MCP): [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md). Public launch checklist: [`docs/PUBLIC_READY.md`](docs/PUBLIC_READY.md). Dry-run status tracking: [`docs/PUBLIC_READY_STATUS.md`](docs/PUBLIC_READY_STATUS.md).

## Tests

```bash
pip install -r requirements.txt   # includes pytest, pytest-asyncio
pytest tests/ -v
```

CI (GitHub Actions) runs `pytest tests/` on push/PR; an optional job exercises MongoDB (`RUN_MONGO_INTEGRATION=1`). Local Mongo smoke: [`scripts/integration_mongo_smoke.sh`](scripts/integration_mongo_smoke.sh). OpenClaw guardrail smoke (semantic_search required): [`scripts/openclaw_guardrail_smoke.sh`](scripts/openclaw_guardrail_smoke.sh). Retrieval benchmark (1-hop vs 2-hop): [`scripts/benchmark_semantic_hops.py`](scripts/benchmark_semantic_hops.py).
Suggested query set for the benchmark: [`scripts/queries_openclaw_memory.txt`](scripts/queries_openclaw_memory.txt).
A/B guardrail matrix helper: [`scripts/openclaw_model_ab_matrix.sh`](scripts/openclaw_model_ab_matrix.sh). Protocol and trigger conditions: [`docs/AB_PROTOCOL.md`](docs/AB_PROTOCOL.md).

## Quick Start

```bash
# 0. Create local env file
cp .env.example .env

# 1. Start MongoDB (auto-initializes replica set for Change Streams)
docker compose up -d

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed example data
python seed.py

# 4. Backfill embeddings for seed data
OPENAI_API_KEY=sk-... python backfill_embeddings.py

# 5. Run the MCP server
python server.py

# 6. (Optional) Run the REST API
python api.py                          # http://localhost:8080/docs

# 7. (Optional) Run the OpenPill proxy
python proxy.py                        # http://localhost:4000/v1
```

### Developer shortcuts

Use the `Makefile` to avoid typing long command chains repeatedly:

```bash
make help
make mongo-up
make test-unit
make ci-local
```

### OpenPill tool shortcuts (search/get flows)

For day-to-day memory checks during development, use:

```bash
make tool-semantic Q="openclaw integration" LIMIT=10 HYBRID=true
make tool-search Q="idempotency key" LIMIT=20
make tool-get ID="<pill_id>"
make tool-neighbors ID="<pill_id>"
make tool-categories
make tool-topics TOP=20 PER=10 MIN_DF=2 MIN_LEN=3
make md-ingest ROOT=.
make md-watch ROOT=. INTERVAL=60
```

These commands call the REST API shortcuts in `scripts/openpill_tools.sh`.
Optional env:

```bash
export OPENPILL_API_BASE="http://localhost:8080"
export OPENPILL_API_KEY="<your_key_if_enabled>"
```

For autonomous markdown-memory sync into OpenPill, use `scripts/ingest_markdown_memory.py` (or `make md-watch`), which ingests only changed `.md` files with idempotency keys.

#### Auto-start on login (cross-platform)

If you want fully hands-off background sync on login (macOS/Linux/Windows):

```bash
make md-watch-install ROOT=. INTERVAL=60
```

Remove it later with:

```bash
make md-watch-uninstall
```

Implementation by OS:
- macOS: launchd user agent
- Linux: systemd user service
- Windows: Startup folder launcher

## Cursor Integration

The project includes a `.cursor/mcp.json` config. To use it globally,
add this to your Cursor MCP settings:

```json
{
  "mcpServers": {
    "openpill": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "/path/to/knowledge-pill-mcp",
      "env": {
        "MONGO_URI": "mongodb://localhost:27017",
        "MONGO_DB": "knowledge_pills_db",
        "EMBEDDING_MODEL": "text-embedding-3-small"
      }
    }
  }
}
```

## MCP Tools

| Tool | Description |
|---|---|
| `search_pills` | Full-text keyword search with category/tag filters |
| `semantic_search` | Vector similarity search; optional graph expansion via `expand_neighbors`, `neighbor_limit`, `max_hops` (1-2), `max_nodes`; optional hybrid lexical fallback via `hybrid` |
| `get_pill` | Retrieve a single pill by ObjectId |
| `get_pill_neighbors` | Outgoing and incoming related pills (graph edges) |
| `create_pill` | Store a new pill (auto-embeds on creation) |
| `ingest_text` | Extract pills from raw text via LLM (auto-deduplicates) |
| `ingest_conversation` | Summarize a conversation and extract pills from the summary |
| `list_categories` | List all active categories |
| `undo_consolidation` | Revert a janitor merge (reactivates archived originals) |

## Autonomous Mode (Self-Healing Memory)

Two autonomous processes keep the knowledge base clean without manual
intervention -- following Peter Steinberger's "Durable Memory" approach:

```
                          ┌──────────────────────┐
  New pill inserted ───>  │  watchdog.py          │  instant, targeted
  (Change Stream)         │  embedding pre-filter │  check per pill
                          │  + LLM analysis       │
                          └──────────────────────┘
                          ┌──────────────────────┐
  Every N minutes ─────>  │  janitor.py --daemon  │  full deep scan
                          │  all categories       │  across entire DB
                          └──────────────────────┘
```

### Watchdog (`watchdog.py`) -- instant reaction

Watches MongoDB via Change Streams. When a new pill is inserted:

1. Compares its embedding against same-category pills (fast, no LLM cost).
2. If any similarity > threshold, sends the new pill + neighbors to the LLM.
3. Auto-consolidates contradictions/redundancies.

```bash
python watchdog.py                        # default threshold 0.85
python watchdog.py --threshold 0.80       # more aggressive matching
python watchdog.py --max-neighbors 10     # limit LLM batch size
```

Requires MongoDB replica set (enabled in `docker-compose.yml`).

### Janitor Daemon (`janitor.py --daemon`) -- periodic deep scan

Runs the full janitor analysis in a loop. Catches issues the watchdog might
miss (e.g., cross-category contradictions, pills without embeddings).

```bash
python janitor.py --daemon                    # scan every 60 min
python janitor.py --daemon --interval 360     # every 6 hours
python janitor.py --daemon --max-ops 10       # cap consolidations per cycle
```

### Running both together

For full autonomy, run the watchdog and daemon side by side:

```bash
# Terminal 1: instant reactions
python watchdog.py

# Terminal 2: periodic deep clean
python janitor.py --daemon --interval 120
```

## REST API (`api.py`)

Exposes the same operations as the MCP server over standard HTTP. Use it for
ChatGPT Custom GPT Actions, Open WebUI, browser extensions, scripts, or any
HTTP client.

```bash
python api.py                          # port 8080
uvicorn api:app --port 8080 --reload   # with hot-reload
```

| Endpoint | Method | Description |
|---|---|---|
| `/pills/search` | GET | Full-text keyword search with category/tag/status filters |
| `/pills/semantic` | GET | Semantic search; query params include `expand_neighbors`, `neighbor_limit`, `max_hops`, `max_nodes`, and optional `hybrid` |
| `/pills/{id}/neighbors` | GET | 1-hop related pills (outgoing + incoming edges) |
| `/pills/{id}` | GET | Retrieve a single pill |
| `/pills` | POST | Create a new pill (auto-embeds) |
| `/pills/ingest` | POST | Extract pills from raw text via LLM |
| `/pills/ingest-conversation` | POST | Summarize a conversation transcript and extract pills |
| `/categories` | GET | List all active categories |
| `/pills/{id}/consolidation` | DELETE | Undo a janitor consolidation |
| `/pills/{id}` | PATCH | Update a pill (title/content/category/tags/status/relations) |
| `/pills/{id}` | DELETE | Archive (soft-delete) a pill |
| `/health` | GET | Health check |
| `/stats` | GET | Active pill counts and pills with graph relations |

Interactive Swagger UI at `http://localhost:8080/docs`. The OpenAPI spec at
`/openapi.json` can be pasted directly into ChatGPT's Custom GPT Action config.

**What hybrid retrieval means for users:** search stays semantic-first, but can optionally add lexical fallback when semantic matches are too sparse. This improves recall for rare wording/keywords without changing your normal workflow.

### Test the API in the browser

1. Start the API: `python api.py`
2. Open **http://localhost:8080/docs** in your browser.
3. Use "Try it out" on any endpoint (e.g. `GET /pills/search`, `GET /pills/semantic`, `POST /pills`, `POST /pills/ingest-conversation`) to send requests and see responses.

### Save my chat web app

For a minimal UI to paste chats and manage pills:

1. Start the API: `python api.py`
2. Open **http://localhost:8080/app** in your browser.
3. Paste a conversation transcript into the **Save my chat** section and click **Distill & save**. The app calls `/pills/ingest-conversation`, which first summarizes the transcript and then extracts pills from the summary (`SourceType.CHAT` with `source.reference` set to `conversation:<ref>`).
4. Use the **Search pills** and **Pill detail** panels to browse, edit, and archive pills (PATCH/DELETE `/pills/{id}`). In semantic mode, enable **Hybrid fallback** if you want better recall for edge-case wording. The detail view loads **Related pills** (1-hop graph neighbors) when edges exist.

## OpenPill Proxy (`proxy.py`)

OpenAI-compatible proxy that sits between any chat tool and the LLM provider.
For every conversation, it automatically:

1. Searches MongoDB for relevant memory entries for the user's latest message.
2. Injects the top-K most relevant pills as a system message.
3. Forwards the enriched request to the upstream LLM (via LiteLLM).
4. (Optionally) Extracts new knowledge from the assistant's response.

```bash
python proxy.py                                  # port 4000
PROXY_AUTO_EXTRACT=true python proxy.py          # auto-extract from responses
PROXY_PORT=5000 PROXY_MAX_PILLS=10 python proxy.py
```

**Client config** (e.g., Open WebUI, TypingMind, or any tool with custom API URL):

```
API Base URL:  http://localhost:4000/v1
API Key:       (your real OpenAI/Anthropic key -- proxy forwards it)
Model:         gpt-4o-mini  (or any model your provider supports)
```

### Open WebUI

Use the **OpenAI** connection, not "OpenAPI Toolserver" (that is for tools/plugins).

1. **Admin Settings** (gear) → **Connections** → **OpenAI** → **Add connection**.
2. **URL:** `http://host.docker.internal:4000/v1` (when Open WebUI runs in Docker; use `http://localhost:4000/v1` if Open WebUI runs on the host).
3. **API Key:** Any non-empty value (e.g. `proxy`); the proxy does not validate it.
4. Save. If verification fails, add model IDs manually in **Model IDs (Filter)**, e.g. `ollama/llama3`, then save again.

The proxy is transparent -- it works with any LLM provider that LiteLLM
supports (OpenAI, Anthropic, Ollama, Gemini, etc.). Streaming is fully
supported.

### Test the proxy in the browser

1. Start the proxy: `python proxy.py`
2. Open **http://localhost:4000** in your browser.
3. Enter a message (e.g. "How does Python handle concurrency?") and click **Send**. The proxy injects relevant memory entries and forwards to the LLM; you see the reply in the page. Use the **Model** dropdown to switch between `ollama/llama3` and `gpt-4o-mini` (if you have an API key).

## CLI Scripts

### Memory Janitor (`janitor.py`)

Scans pills for contradictions and redundancies using an LLM.

```bash
python janitor.py                           # dry-run (report only)
python janitor.py --apply                   # apply consolidations
python janitor.py --apply --confirm         # ask Y/n before each merge
python janitor.py --apply --max-ops 5       # cap at 5 operations

JANITOR_MODEL=ollama/llama3 python janitor.py   # use local model
```

All operations are logged to the `janitor_audit_log` MongoDB collection.
Originals are archived (never deleted) and can be restored via `undo_consolidation`.
Local-first routing is controlled with `JANITOR_MODEL_POLICY` (`local_only|local_first|external_first`, default `local_first`). Optional escalation to an external model can be enabled with `JANITOR_ESCALATION_ENABLED=true` and `JANITOR_EXTERNAL_MODEL=...`.

### Memory Extractor (`extractor.py`)

Auto-distills memory entries from raw text.

```bash
python extractor.py --file notes.md                  # dry-run
python extractor.py --file notes.md --insert          # insert into DB
python extractor.py --stdin --insert < transcript.txt # pipe from stdin
python extractor.py --file notes.md --min-confidence 0.7 --max-pills 20
```

Deduplicates via embedding similarity before inserting. Threshold is configurable: `EXTRACTOR_DUPLICATE_THRESHOLD` (default 0.92); conversation pills use a stricter default (0.95) unless `EXTRACTOR_CONVERSATION_DUPLICATE_THRESHOLD` is set. After insert, optional **related** edges link the new pill to same-category neighbors in similarity band `[EXTRACTOR_RELATED_THRESHOLD, duplicate threshold)` (see `EXTRACTOR_LINK_ON_INSERT`, `EXTRACTOR_RELATED_MAX_LINKS`). Very short pills are skipped (min title/content length configurable via env). Categories are normalized to a fixed list. Confidence is lightly re-scored. Extraction retries once on JSON parse failure. Ingest responses include `stats` and duplicate skips include `similar_pill_id` when applicable.
Local-first routing is controlled with `EXTRACTOR_MODEL_POLICY` (`local_only|local_first|external_first`, default `local_first`). Optional escalation to an external model is gated behind `EXTRACTOR_ESCALATION_ENABLED=true` and an input-length threshold.

### Embedding Backfill (`backfill_embeddings.py`)

Populates embeddings for pills that don't have one yet.

```bash
python backfill_embeddings.py                    # all pills
python backfill_embeddings.py --category python  # specific category
```

### Mid-Term Memory Sync (`sync_memory.py`)

Exports pills to a markdown file for Cursor context injection.

```bash
python sync_memory.py                                       # -> MEMORY.md
python sync_memory.py -o ~/.cursor/rules/knowledge.md       # custom path
python sync_memory.py -c python,architecture -n 100         # filter + limit
```

### Topic Snapshot (`scripts/topic_snapshot.py`)

Classical NLP helper to generate a cheap/explainable topic overview from active pills.

```bash
python scripts/topic_snapshot.py
python scripts/topic_snapshot.py --top-terms 15 --per-category 8
```

## Schema

Each document in the `knowledge_pills` collection:

```
title           string      Short descriptive title
content         string      The distilled fact
category        string      e.g. "python", "architecture", "devops"
tags            [string]    Filterable tag list
source.type     enum        "chat" | "document" | "manual" | "code"
source.reference string     Origin reference (chat ID, URL, file path)
embedding       [float]?    Vector embedding for semantic search
confidence      float       0.0-1.0
status          enum        "active" | "archived" | "deprecated"
created_at      datetime    Auto-set on creation
updated_at      datetime    Auto-set on creation / update
expires_at      datetime?   Optional TTL
relations       [{target_id, kind}]  Optional graph edges: kind is related | supersedes | same_topic
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `OPENPILL_DB` | `knowledge_pills_db` | Preferred database env var |
| `MEMORA_DB` | `knowledge_pills_db` | Legacy database env var (still supported) |
| `MONGO_DB` | `knowledge_pills_db` | Legacy database env var (still supported) |
| `OPENPILL_COLLECTION` | `knowledge_pills` | Preferred collection env var |
| `MEMORA_COLLECTION` | `knowledge_pills` | Legacy collection env var (still supported) |
| `KNOWLEDGE_COLLECTION` | `knowledge_pills` | Legacy collection env var (still supported) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | LiteLLM embedding model |
| `JANITOR_MODEL` | `gpt-4o-mini` | LiteLLM model for the janitor |
| `EXTRACTOR_MODEL` | `gpt-4o-mini` | LiteLLM model for the extractor |
| `JANITOR_MODEL_POLICY` | `local_first` | Janitor routing policy: `local_only`, `local_first`, `external_first` |
| `JANITOR_EXTERNAL_MODEL` | -- | Optional external Janitor model used by policy/escalation |
| `JANITOR_ESCALATION_ENABLED` | `false` | Allow Janitor escalation from local to external model |
| `JANITOR_ESCALATION_MIN_GROUP_SIZE` | `6` | Min consolidation group size to escalate (local_first mode) |
| `EXTRACTOR_MODEL_POLICY` | `local_first` | Extractor routing policy: `local_only`, `local_first`, `external_first` |
| `EXTRACTOR_EXTERNAL_MODEL` | -- | Optional external Extractor model used by policy/escalation |
| `EXTRACTOR_ESCALATION_ENABLED` | `false` | Allow Extractor escalation from local to external model |
| `EXTRACTOR_ESCALATION_MIN_TEXT_LEN` | `8000` | Min transcript/text length to escalate (conversation path) |
| `EXTRACTOR_DUPLICATE_THRESHOLD` | `0.92` | Dedup similarity threshold (document extraction) |
| `EXTRACTOR_CONVERSATION_DUPLICATE_THRESHOLD` | `0.95` (or same as above) | Stricter dedup for conversation pills |
| `EXTRACTOR_MAX_PILLS` | `50` | Max pills per extraction run |
| `EXTRACTOR_MIN_PILL_TITLE_LEN` | `3` | Min title length to accept |
| `EXTRACTOR_MIN_PILL_CONTENT_LEN` | `15` | Min content length to accept |
| `EXTRACTOR_CONVERSATION_SUMMARY_CHARS` | `600,1200` | Summary length range (min,max) for conversation summarizer |
| `EXTRACTOR_CONVERSATION_EXCERPT_TRANSCRIPT_MIN_LEN` | `2000` | Min transcript length to append excerpt to extraction input |
| `EXTRACTOR_CONVERSATION_EXCERPT_LEN` | `500` | First/last N characters used for transcript excerpt |
| `EXTRACTOR_RELATED_THRESHOLD` | `0.88` | Min similarity to auto-link `related` edges on insert |
| `EXTRACTOR_RELATED_MAX_LINKS` | `3` | Max neighbor links per new pill |
| `EXTRACTOR_LINK_ON_INSERT` | `true` | Enable post-insert graph linking |
| `HYBRID_RETRIEVAL_ENABLED` | `false` | Global switch for hybrid retrieval (vector + lexical fallback) |
| `HYBRID_VECTOR_WEIGHT` | `0.7` | Weight of vector similarity in hybrid fusion |
| `HYBRID_LEXICAL_WEIGHT` | `0.3` | Weight of lexical text score in hybrid fusion |
| `HYBRID_LEXICAL_LIMIT` | `30` | Max lexical candidates fetched in fallback mode |
| `HYBRID_LEXICAL_FALLBACK_MIN_VECTOR` | `3` | Trigger lexical fallback when fewer vector candidates are found |
| `WATCHDOG_SIMILARITY_THRESHOLD` | `0.85` | Watchdog embedding similarity threshold |
| `PROXY_PORT` | `4000` | Proxy listen port |
| `PROXY_MAX_PILLS` | `5` | Max pills injected per proxy request |
| `PROXY_MIN_SIMILARITY` | `0.70` | Min similarity for proxy pill injection |
| `PROXY_AUTO_EXTRACT` | `false` | Auto-extract pills from proxy responses |
| `PROXY_EXPAND_NEIGHBORS` | `false` | Append 1-hop related pills after semantic hits |
| `PROXY_NEIGHBOR_LIMIT` | `5` | Max extra neighbor pills in proxy injection |
| `OPENAI_API_KEY` | -- | Required for OpenAI models |
| `ANTHROPIC_API_KEY` | -- | Required for Anthropic models |
| `OPENPILL_API_KEY` | -- | Preferred API key for REST auth |
| `MEMORA_API_KEY` | -- | Legacy API key env var (still supported) |
| `KNOWLEDGE_PILL_API_KEY` | -- | Legacy API key env var (still supported) |

All models are switchable to local Ollama (e.g. `ollama/llama3`, `ollama/nomic-embed-text`).
