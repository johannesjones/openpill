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

## Local-First model policy

- Extractor and Janitor support model routing policy via:
  - `EXTRACTOR_MODEL_POLICY` / `JANITOR_MODEL_POLICY`: `local_only`, `local_first`, `external_first`
  - Optional external models: `EXTRACTOR_EXTERNAL_MODEL`, `JANITOR_EXTERNAL_MODEL`
  - Escalation gate flags: `EXTRACTOR_ESCALATION_ENABLED`, `JANITOR_ESCALATION_ENABLED`
- Recommended production default for cost control: `local_first` with escalation disabled.
- If escalation is enabled, keep strict gates (`EXTRACTOR_ESCALATION_MIN_TEXT_LEN`, `JANITOR_ESCALATION_MIN_GROUP_SIZE`).
- External A/B trigger conditions and cost cap are defined in [`docs/AB_PROTOCOL.md`](AB_PROTOCOL.md).
- Optional runtime A/B guards for external calls:
  - `AB_GUARDS_ENABLED=true`
  - `AB_ALLOW_EXTERNAL=true`
  - `AB_MAX_EXTERNAL_CALLS=<N>`

## Hybrid retrieval pilot

- Optional retrieval fusion combines semantic vector ranking with lexical text-score fallback.
- Safe default is OFF (`HYBRID_RETRIEVAL_ENABLED=false`).
- Runtime knobs:
  - `HYBRID_VECTOR_WEIGHT` (default `0.7`)
  - `HYBRID_LEXICAL_WEIGHT` (default `0.3`)
  - `HYBRID_LEXICAL_LIMIT` (default `30`)
  - `HYBRID_LEXICAL_FALLBACK_MIN_VECTOR` (default `3`)
- API and MCP `semantic_search` also expose an explicit `hybrid` flag per call.

## Semantic similarity floor

- REST `GET /pills/semantic` and MCP `semantic_search` share one pipeline (`retrieval.py`).
- **`OPENPILL_SEMANTIC_MIN_SIMILARITY`** (default unset = no floor): drop vector hits below this cosine similarity, so an unrelated query can return nothing. Per call: `min_similarity` (REST query param, MCP argument).
- The right value depends on the embedding model; measure it with `make retrieval-eval-live` before setting it. For **`ollama/nomic-embed-text`** the eval passes every question type at **0.57** (2026-10-08). The margin is narrow: the most similar unrelated pill scored 0.556, the weakest real match 0.585. Clients that embed something other than short prose (e.g. JSON records) have a different similarity range, so check them before setting the env default; the per-call `min_similarity` is the safer start.
- `retrieval_metrics` reports `min_similarity` and `below_min_similarity` (how many pills the floor dropped).
- Ranking: the top `limit` pills by similarity are ordered by `retrieval_score` = 0.6·relevance + 0.2·confidence + 0.2·freshness, minus penalties for conflicts (up to 0.2) and supersession (0.15). `relevance_score` is the similarity min-max normalized over all pills scanned, so close matches stay close and freshness/supersedes can order them. A superseded pill is never ranked above a pill that supersedes it. Single-pill reads (no query) keep 0.6·confidence + 0.25·freshness.

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
- **`OPENPILL_API_KEYS`** (optional) — JSON object mapping extra keys to a namespace prefix, e.g. `{"<tracker-key>": "jjones/job_tracker"}`. A request with such a key only sees and writes pills under that prefix; it may narrow it (`namespace=jjones/job_tracker/drafts`) but never leave it (403). The shared `OPENPILL_API_KEY` stays unrestricted. An empty prefix (`""`) also means unrestricted. Keys are compared in constant time.

## Namespaces

Pills can carry a **`namespace`**, a path like `jjones/job_tracker` (letters, digits, `_ . @ -`; up to 6 segments). It is stored as a list (`["jjones", "job_tracker"]`). Pills without one are in the global namespace, which includes everything written before namespaces existed.

- **Write:** `namespace` in the body of `POST /pills`, `POST /pills/ingest`, `POST /pills/ingest-conversation` (MCP: the `namespace` argument of `create_pill`, `ingest_text`, `ingest_conversation`).
- **Read:** `namespace` query parameter (MCP argument) on search, semantic search, get, neighbors, categories, stats, conflicts and topics. It is a prefix: `jjones` matches `jjones/job_tracker`. Without it, reads cover every namespace (unless the API key is bound).
- **By id:** get, PATCH, DELETE and undo-consolidation answer 404 when the pill lies outside the given namespace.
- **Isolation:** dedup, same-source merges, auto-links, janitor and watchdog only compare pills in the **same exact** namespace, and merged pills keep it. Graph expansion in semantic search stays under the read prefix.
- **MCP:** `OPENPILL_MCP_NAMESPACE` binds one MCP server process to a prefix, like a bound API key (MCP itself has no auth).
- **Adopting namespaces on existing data:** a client that starts sending `namespace` no longer sees its old global pills. Move them first: `python scripts/move_to_namespace.py --category job_application --namespace jjones/job_tracker` (dry run), then add `--apply`.

## Embedding text override (`embed_text`)

By default a pill is embedded as `title + "\n" + content`. Clients whose content is not prose (e.g. JSON records) can send **`embed_text`** on create or update (REST and MCP) with a readable summary to embed instead. On update, the vector is refreshed only when the effective embedding text changes; `embed_text: ""` clears the override. `backfill_embeddings.py` honours the override.

## Structured request logging

- The API logs one JSON line per request on the logger `openpill.api` with fields: `event`, `method`, `path`, `status_code`, `duration_ms`, and optional `request_id` (from client header `X-Request-Id` if present). No secrets or bodies are logged.

## Ingest idempotency

- **`POST /pills/ingest`** and **`POST /pills/ingest-conversation`** accept optional header **`Idempotency-Key`**.
- The server hashes the **raw request body**; the same key + same body replays the **first successful** JSON response while the record exists in MongoDB.
- The same key with a **different body** returns **409 Conflict** (client must use a new key).
- Records live in collection **`idempotency_keys`** with a TTL on `created_at` (default **72 hours**, override with **`IDEMPOTENCY_TTL_SECONDS`**). Clients should only retry with the same key when the request body is identical.

## Pill graph edges (canonical relation kinds)

Stored on each pill as `relations[]` with `{ "target_id", "kind" }`. Allowed **`kind`** values:

- `related`
- `supersedes`
- `same_topic`
- `conflicts_with`

Unknown values in legacy data are **normalized to `related`** when relations are rewritten (e.g. janitor merge). New writes via the REST models should use only these strings.

## Contradictions and supersession (discovery)

- **`conflicts_with`** — The janitor (`janitor.py`) can persist contradiction pairs as bidirectional edges before optional consolidation. To **list** unresolved active↔active conflict pairs: **`GET /pills/conflicts`** (`limit` 1–500, default 100). Response: `total`, `pairs` (`pill_id_a` / `pill_id_b`, `title_a` / `title_b`), `truncated`. MCP equivalent: **`list_unresolved_conflicts`**.
- **`supersedes`** — A newer active pill may point at an older one with `kind: supersedes`. Semantic search and single-pill reads attach **`is_superseded`** and **`consistency_warning`** on the target so clients can deprioritize stale facts.

## Janitor and watchdog scope

- **`OPENPILL_MAINTENANCE_EXCLUDE_CATEGORIES`** (comma-separated, default empty): categories the janitor and watchdog never analyze or merge. Use it for records a client owns and writes deterministically, e.g. `job_application` for the job-application tracker.
- Merged pills are embedded when they are created. If embedding fails they are stored without a vector; run `backfill_embeddings.py`.

## Expiry (`expires_at`)

A pill with `expires_at` in the past is **archived** (`status=archived`), not deleted. The janitor does this at the start of every non-dry-run (`python janitor.py --apply`, or `--daemon`). Startup drops the old Mongo TTL index `ttl_expires` (which hard-deleted documents) and recreates it as a plain index.

## Temporal validity and history

Facts change; OpenPill keeps the old version instead of deleting or silently overwriting it (after Zep/Graphiti):

- **`valid_at`**: when the fact became true (optional on create; freshness uses it when set, else `updated_at`).
- **`invalid_at`**: when it stopped being true. Invalidated pills stay `active` in the store but are hidden from search, semantic search, dedup, links, janitor and watchdog. `include_invalid=true` (REST query / MCP argument) shows them, flagged `is_invalid` with a warning and ranked after every valid pill. Set or clear it with `PATCH /pills/{id}` (`"invalid_at": "<ISO>"` / `null`) or MCP `update_pill(invalid_at="now" | "<ISO>" | "")`.
- **`history`**: every rewrite of title or content (PATCH, MCP `update_pill`, same-source merge, LLM UPDATE) appends the previous version (`title`, `content`, `category`, `previous_updated_at`, `replaced_at`, `reason`), keeping the last 20. Returned by `GET /pills/{id}` and MCP `get_pill` only; list and search responses omit it.

## Update decisions on ingest (opt-in)

- **`OPENPILL_UPDATE_POLICY`** = `threshold` (default: near-duplicates above the threshold are skipped or merged by source, as before) or `llm`. With `llm`, each extracted fact is compared with up to 5 valid pills in the same namespace (cosine ≥ **`OPENPILL_UPDATE_CANDIDATE_MIN_SIMILARITY`**, default `0.75`); one LLM call (the extractor model) picks:
  - **ADD**: insert as usual (no similar pills → ADD without an LLM call),
  - **UPDATE**: rewrite the named pill with the merged title/content (old text goes to `history`),
  - **INVALIDATE**: insert the new fact, set `invalid_at` on the named pill and add a directed `supersedes` edge new → old,
  - **NOOP**: skip.
  An unusable answer (bad JSON, unknown target, provider error) falls back to the threshold path. Ingest responses add `decisions`, `updated` and `invalidated`.
- **`OPENPILL_APPLY_SUPERSEDES_HINTS`** (default `false`): with the strict extraction schema, a `supersedes` relation hint whose `target_concept` exactly matches (case-insensitive) the title of a valid pill in the same namespace and category invalidates that pill after the new one is inserted.

## Same-source merge on ingest (dedup → update)

When **`OPENPILL_MERGE_SAME_SOURCE`** is `true` (default), ingest runs compare near-duplicates to the pill’s **`source.reference`**:

- Document ingest uses `extractor:<source_reference>`.
- Conversation ingest uses `conversation:<source_reference>`.

If the **best** near-duplicate (above the usual duplicate threshold) belongs to the **same** reference, OpenPill **updates** that document (title, content, category, tags, confidence, embedding, `updated_at`) instead of skipping as a duplicate. The JSON response includes **`merged_same_source`** (list of `{ title, pill_id, similarity }`) alongside **`inserted`**.

Set **`OPENPILL_MERGE_SAME_SOURCE=false`** to restore “skip all duplicates” behavior.

## Strict extraction schema (optional)

When **`OPENPILL_STRICT_EXTRACTION_SCHEMA=true`**, document and conversation extractors use prompts that require additional JSON fields per pill:

- **`entities`** — short entity names (0–8)
- **`relation_hints`** — suggested links to other concepts by name, each with `target_concept` and `kind` (`related`, `supersedes`, `same_topic`, `conflicts_with`; unknown kinds normalize to `related` on save)
- **`evidence_quote`** — short verbatim snippet from the source (or `null`)
- **`rationale`** — one line explaining why the fact is worth storing

Persisted on the MongoDB document as **`extraction_meta`** (omitted or `null` when absent). Default **`false`** preserves the original compact schema and full backward compatibility for LLM output and clients.

## Retrieval golden queries (regression)

Offline checks for **semantic ranking, category filters, neighbor expansion, and superseded metadata** use a fixed corpus and **mocked embeddings** (no LLM or DB required):

- Fixture: `tests/fixtures/retrieval_golden.json`
- Test: `tests/test_retrieval_golden.py`
- Run: `make retrieval-golden` (also included in `pytest tests/` and the CI unit job)

## Memory-quality eval

A small labeled set in `evals/retrieval_cases.json` (30 pills, 41 questions) by question type, modelled on LoCoMo / LongMemEval: **single_hop**, **multi_hop** (needs `expand_neighbors`), **temporal** (newer fact must rank above the older one), **knowledge_update** (a `supersedes` target must rank below its replacement), **abstention** (nothing relevant → empty).

- `make retrieval-eval`: offline, deterministic hashed bag-of-words embeddings; fails if a type drops below the baseline in the fixture. Also runs in `pytest tests/` (`tests/test_retrieval_eval.py`). It tests the pipeline (floor, freshness, supersedes, expansion), not semantic understanding.
- `make retrieval-eval-live`: same questions with the configured `EMBEDDING_MODEL` (e.g. Ollama). Use `python evals/retrieval_eval.py --embedder live --min-similarity 0.5` to try floors.
- Output per type: pass rate and MRR; `--verbose` lists failing questions, `--json` prints the report.

## Replica set

- Change streams (`watchdog.py`) require a **replica set**. The provided `docker-compose.yml` configures one for local dev.
