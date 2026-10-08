# Research synthesis & roadmap: dynamic memory for LLM agents

This document distills recent directions in **long-term / dynamic memory** for LLMs and agents (especially **graph-based** representations combined with **reinforcement learning**), and maps them to the **Knowledge Pill** stack. It is **hand-written**—unlike `MEMORY.md`, which is **auto-generated** by `sync_memory.py` from stored pills.

---

## Why graphs + RL

| Piece | Role |
|--------|------|
| **Graphs** | Explicit **entities, relations, and paths** (temporal, causal, semantic). Supports structured retrieval and more **explainable** “why this memory mattered” than flat vector-only stores. |
| **RL / policy learning** | Natural fit for **memory management**: *when to write, merge, retrieve, or forget* under **sparse or delayed reward**—where fixed heuristics don’t scale. |

Together: **graph = representation + navigation**, **RL = learning how to use and update the store**.

---

## Representative research directions (arXiv / preprints)

Treat these as **fast-moving**; validate claims against experiments, code, and benchmarks.

### Trainable / strategic graph memory

- **From Experience to Strategy: Empowering LLM Agents with Trainable Graph Memory** — [arXiv:2511.07800](https://arxiv.org/abs/2511.07800)  
  Abstracts trajectories into graph-structured strategies; reward-based weighting; addresses limitations of implicit memory (forgetting) vs. static prompt-only memory (rigidity).

### Multi-graph agentic memory

- **MAGMA: A Multi-Graph based Agentic Memory Architecture for AI Agents** — [arXiv:2601.03236](https://arxiv.org/abs/2601.03236)  
  Orthogonal views (e.g. semantic, temporal, causal, entity); retrieval as **policy-guided traversal** over relational structure.

### Memory-integrated RL

- **MIRA: Memory-Integrated Reinforcement Learning Agent with Limited LLM Guidance** — [arXiv:2602.17930](https://arxiv.org/abs/2602.17930)  
  Structured evolving memory; amortizes LLM queries; emphasis on **sparse-reward** settings.

### Learned indexing for long horizons

- **Memex(RL): Scaling Long-Horizon LLM Agents via Indexed Experience Memory** — [arXiv:2603.04257](https://arxiv.org/abs/2603.04257)  
  RL learns **what to summarize, archive, index, and when to retrieve**—a learned memory controller vs. a single static RAG index.

### Surveys & taxonomies

- **Graph-based Agent Memory: Taxonomy, Techniques, and Applications** — [arXiv:2602.05665](https://arxiv.org/abs/2602.05665)  
  Lifecycle: **extraction → storage → retrieval → evolution**; short vs. long-term; knowledge vs. experience; structural vs. non-structural memory.

- **A Survey on the Memory Mechanism of Large Language Model based Agents** — [arXiv:2404.13501](https://arxiv.org/abs/2404.13501)  
  Broader framing of agent memory mechanisms.

- **LLM-empowered knowledge graph construction: A survey** — [arXiv:2510.20345](https://arxiv.org/abs/2510.20345)  
  Classical pipeline **ontology → extraction → fusion**; **schema-based** vs **schema-free** paradigms; outlook on **dynamic knowledge for agentic systems** and multimodal KGs. Useful as a **product lens** for what to adopt in small steps (see Phase B2 below).

### Curated lists

- **Awesome-GraphMemory** (GitHub: `DEEP-PolyU/Awesome-GraphMemory`) — papers, tools, and benchmarks for graph-based agent memory.

---

## Open problems (field-wide)

- Continual **consolidation** and **contradiction** handling  
- **Causal** grounding of retrieval (not just similarity)  
- **Learned forgetting** and privacy-aware retention  
- Strong **evaluation**: benchmarks that stress **long-horizon** behavior, not single-turn QA  

---

## How this maps to Knowledge Pill (today)

| Capability | Current stack |
|------------|----------------|
| Long-term store | MongoDB + embeddings |
| Ingestion | `extractor.py` (LLM distill), conversation + document paths |
| Deduplication | Embedding similarity + thresholds |
| Access | MCP (`server.py`), REST (`api.py`), optional proxy injection |
| Mid-term bridge | `sync_memory.py` → `MEMORY.md` for editor context |

This is a solid **extract → embed → dedupe → retrieve** layer—aligned with “experience / knowledge memory” in surveys, but **not yet** a full relational graph or learned write/retrieve policy.

---

## LLM-KG survey ↔ OpenPill (what to borrow, without building a research KG)

The [2510.20345](https://arxiv.org/abs/2510.20345) survey frames **KG construction** as ontology engineering, **knowledge extraction**, and **knowledge fusion**. OpenPill stays a **pragmatic agent memory layer**; the takeaway is **which stages** to strengthen first:

| Survey stage | OpenPill analogue | Adoption stance |
|--------------|-------------------|-----------------|
| Ontology / schema | Categories, tags, `relations[].kind` | **Light**: canonical relation vocabulary + optional entity keys later—not a full OWL stack |
| Extraction | `extractor.py`, ingest paths | **Strengthen**: stricter structured output, chunking for long inputs, optional validation pass |
| Fusion | Dedup, `conflict_count`, relations | **Strengthen**: merge rules keyed on `source_reference`, explicit conflict/supersedes workflows |

**Strategy:** **Phase B2** = minimal schema + fusion first; deeper graph/RL remains Phase C/D.

---

## LangGraph memory docs ↔ OpenPill (what matches, what is missing)

Compared against the LangChain/LangGraph memory pages (Memory overview, Short-term memory, Memory how-to, Context overview). LangGraph splits memory into **short-term** (thread-scoped graph state, persisted by a **checkpointer**) and **long-term** (a **store** of JSON documents under **namespaces**, searchable by vector similarity). OpenPill is **long-term memory only**, run as its own service in place of the in-process `BaseStore`.

| LangGraph concept | OpenPill today | Gap / note |
|-------------------|----------------|------------|
| Checkpointer, `thread_id` (short-term) | Out of scope | The tracker agent compiles its graph **without a checkpointer**, so it has no thread memory either |
| Store: namespace + key + JSON value | One flat collection; ObjectId as key; `category` is the only grouping | **No namespaces / no `user_id`** → single-tenant. REST has one optional shared key (`OPENPILL_API_KEY`); MCP has no auth |
| `store.search(ns, query=, filter=)` | `/pills/semantic` (category filter), `/pills/search` (text, category, tags) | Similarity is **brute-force cosine in Python**; `models.py` mentions Atlas `vectorSearch`, but `db.py` creates no vector index |
| Embedding config on the store (`index`) | `embed_text_for_pill`; LiteLLM model via `EMBEDDING_MODEL` | Callers that store whole JSON records (the tracker) get a narrow similarity band; embed selected text fields instead |
| Ranking = vector similarity | `0.6·confidence + 0.25·freshness + 0.15·similarity`, lexical fusion when < 3 vector hits, 1–2-hop neighbor expansion | **Ahead of the docs.** (Freshness was stuck at 0.5 on semantic search and single reads — fixed) |
| Semantic memory: profile vs collection | **Collection** of atomic pills | The tracker uses OpenPill **profile-style** (one record per company); code merges it, which avoids the docs' warning about LLM-regenerated profiles |
| Episodic memory (few-shot from past runs) | Conversation summaries via `ingest-conversation` | No few-shot example selection |
| Procedural memory (reflection rewrites prompts) | `sync_memory.py` exports pills to `MEMORY.md` / editor rules | No self-updating instructions |
| Writes on the hot path (save-memory tool) | MCP `create_pill` / `update_pill`, REST | Matches |
| Writes in the background (scheduled, cron, manual) | `janitor.py --daemon` (scheduled), `watchdog.py` (change-stream, event-driven), `extractor.py` (manual), `proxy.py` auto-extract | **Ahead of the docs** (the event-driven path has no counterpart there) |
| Over-insert / over-update, contradictions; "evaluate with LangSmith / Trustcall" | Dedup thresholds (0.92 / 0.95), same-source merge, janitor + watchdog consolidation, `conflicts_with` / `supersedes`, audit log, `undo_consolidation` | **No eval of merge quality.** Janitor/watchdog do not exclude caller-owned categories (e.g. `job_application`), so an LLM merge can rewrite records a caller treats as code-owned |
| Forgetting (trim / delete / summarize messages) | Freshness in score, soft delete (`archived`), `expires_at` | ~~`expires_at` hard-deleted via a TTL index~~ — fixed: the janitor archives expired pills |
| Context engineering: runtime context, state, store | Proxy injects relevant pills into the system prompt for any OpenAI-compatible client | Matches the docs' "search store, then prompt" node pattern, outside LangGraph |

**Where OpenPill goes further:** combined ranking, typed relations and neighbor expansion, provenance (`source`, `extraction_meta`), event-driven plus scheduled maintenance, and one shared service over REST, MCP and a proxy instead of a store bound to one graph.

**Improvement plan** (also drawing on Mem0, Zep/Graphiti, LangMem, Letta, A-MEM, LoCoMo/LongMemEval):

*Phase 1 — correctness (done)*
- [x] Freshness score: `_serialize_doc` turned `updated_at` into a string before scoring, so semantic search and `GET /pills/{id}` always got 0.5
- [x] `expand_neighbors=true` no longer rescores direct hits (it wiped the superseded penalty and the hybrid blend)
- [x] Timezone-aware `updated_at` on PATCH / MCP update
- [x] `expires_at` archives instead of hard-deleting (janitor run; TTL index replaced)
- [x] Janitor/watchdog skip `OPENPILL_MAINTENANCE_EXCLUDE_CATEGORIES`; merged pills get an embedding

*Phase 2 — one search path + memory-quality evals (done)*
- [x] Shared `retrieval.py` for `api.py` and `server.py` (MCP results now carry the same scores and superseded flags)
- [x] 41-question eval set by type (`evals/`, `make retrieval-eval`, live mode against the real embedder)
- [x] Optional similarity floor (`OPENPILL_SEMANTIC_MIN_SIMILARITY` / `min_similarity`)
- [ ] **Finding:** without a floor, single-hop pass rate drops from 0.94 to 0.56 offline, because the final order weighs similarity at only 0.15. Next: rank by similarity first (or raise its weight) and use confidence/freshness as tie-breakers; measure with `make retrieval-eval-live` and pick a default floor per model

*Phase 3 — namespaces (LangGraph store model)*
- [ ] Optional `namespace` on pills, filtered on every read/write; janitor/watchdog never merge across namespaces; per-key namespace prefixes
- [ ] `embed_text` override so callers embed a readable summary instead of a JSON blob

*Phase 4 — temporal validity + update decisions (Graphiti, Mem0)*
- [ ] `valid_at` / `invalid_at`; contradictions invalidate the old fact instead of merging it away
- [ ] Turn extractor `supersedes` hints into real edges + invalidation
- [ ] ADD / UPDATE / INVALIDATE / NOOP decision per extracted fact (opt-in), with a `history` array on updates

*Phase 5 — optional*
- [ ] `$vectorSearch` backend (Atlas or Community 8.2+ with `mongot`) behind a flag, once brute force gets slow
- [ ] Per-namespace "core profile" pill maintained in the background (Letta memory blocks / LangGraph profile)
- [ ] Opt-in memory evolution of linked neighbors (A-MEM), audited

---

## Roadmap (suggested phases)

### Phase A — Solidify the current model (near-term)

- [ ] Tune extraction / dedup / categories using existing env + `stats` from ingest responses  
- [ ] Document operational playbooks (Ollama vs. cloud, backup, indexing)  
- [ ] Expand evaluation: regression tests on ingest quality and retrieval hit-rate  

### Phase B — Structure without full RL (medium-term)

- [x] **Explicit links** between pills (`relations[]` with `target_id` + `kind`: related, supersedes, same_topic) stored in MongoDB; auto-link on ingest in a similarity band  
- [x] Lightweight **graph-aware retrieval**: `GET /pills/{id}/neighbors`, semantic search `expand_neighbors`, MCP `get_pill_neighbors`, optional `PROXY_EXPAND_NEIGHBORS`  
- [ ] Optional **entity extraction** on ingest to attach stable entity keys for linking — **deferred** (schema `entities[]` + suggest-links flow; track as separate change when Phase B graph usage is stable)  

### Phase B2 — Minimal schema & fusion (KG-survey-inspired, near-term)

Prioritised concrete work—**small scope**, high leverage. Suggested order:

- [x] **Canonical relation vocabulary** — Document and enforce a **closed or curated set** of `PillRelationKind` values (plus a safe fallback like `related`); reject or normalise unknown kinds at ingest/API boundaries where practical. *(Implemented: `normalize_relation_kind`, `sanitize_relations`, janitor rewire normalization; see `models.py`, `pill_relations.py`, `docs/OPS.md`.)*
- [x] **Merge / dedup by provenance** — When a new ingest matches an existing pill **strongly** and shares the same **`source.reference`** as the extractor would use, **update** that pill instead of skipping. *(Implemented: `OPENPILL_MERGE_SAME_SOURCE`, `merged_same_source` in ingest responses; see `extractor.py`, `docs/OPS.md`.)*
- [x] **Conflict & supersession UX** — Make **contradictions** first-class: ensure `conflicts` edges + janitor/API paths are **documented and discoverable**; optional tool or endpoint to **list unresolved conflicts** for agents or humans. *(Implemented: `GET /pills/conflicts`, MCP `list_unresolved_conflicts`, `list_active_conflict_pairs` in `pill_relations.py`; see `docs/OPS.md`.)*
- [x] **Retrieval smoke / golden queries** — A **small fixed set** of queries (e.g. 20–50) with expected pill IDs or keywords; run in CI or `make` target to catch retrieval regressions (hybrid on/off). *(Implemented: offline golden suite in `tests/fixtures/retrieval_golden.json` + `tests/test_retrieval_golden.py`, deterministic one-hot mocks; `make retrieval-golden`; runs in default `pytest tests/` / CI unit job.)*
- [x] **Stricter extraction schema (optional)** — Structured LLM output (entity / relation / confidence / span hints) before mapping to pills; keep backward compatibility for existing clients. *(Implemented: optional `OPENPILL_STRICT_EXTRACTION_SCHEMA`; extended `ExtractedFact` + strict prompts; `extraction_meta` on `KnowledgePill` with `entities`, `relation_hints`, `evidence_quote`, `rationale`; see `extractor.py`, `models.py`, `tests/test_extraction_schema.py`, `docs/OPS.md`.)*

**Later (still minimal):** chunking for long ingest text; light validation (“is this supported by the source excerpt?”) only on high-risk paths.

### Phase C — Graph-first memory (aspirational)

- [ ] Separate **views** (MAGMA-style): e.g. semantic similarity vs. temporal / causal edges  
- [ ] **Policy-guided traversal** for retrieval (learned ranker or small RL policy on “which edge to follow”)  
- [ ] **Contradiction** and merge workflows (janitor + human-in-the-loop)  

### Phase D — Learned memory management (research / long-term)

- [ ] Reward signals from **task success** or **user feedback** to tune **when to write** and **what to merge**  
- [ ] Explore **Memex(RL)**-style learned **summarization / indexing** for very long transcripts  

---

## How to proceed with the app you have (actionable order)

Research papers assume **graphs + learned policies**. Your app today is **vector + rules + LLM extract**—that is already the right **foundation**. Proceed in **layers**: earn value from Phase A before building graph edges; add graph **structure** in Phase B before dreaming about RL.

### 1. This month — Phase A (no schema change)

| Action | Why |
|--------|-----|
| Pick **default env** for your stack (e.g. Ollama models + thresholds) and document one **“known good”** command line for API + MCP | Reproducible ingest quality |
| After each **ingest**, skim `stats` (lengths, `excerpt_used`, `candidates`) and tune `EXTRACTOR_*` env vars | Turns the pipeline into a **measured** system |
| Add a tiny **golden transcript** (or doc) in `tests/` and assert JSON shape + non-empty or empty pills as expected | Prevents regressions when you change prompts |
| Write a **short ops note**: backup `MONGO_URI`, how to re-embed if model changes | Long-term memory is only as good as **durability** |

### 2. Next — Phase B (first “graph” without ML training) — implemented

- **`relations[]`** on each pill with `target_id` + `kind`; index on `relations.target_id` for reverse lookups.
- **Post-insert linking**: same-category neighbors in `[EXTRACTOR_RELATED_THRESHOLD, duplicate threshold)` get bidirectional `related` edges (configurable via env).
- **`GET /pills/{id}/neighbors`**, semantic **`expand_neighbors`**, MCP **`get_pill_neighbors`**, optional proxy graph expansion.

That gives you **explainable “why these two memories are connected”** and a path toward MAGMA-style **multi-view** (semantic edge now; temporal edges from `created_at` later).

### 3. Later — Phase C/D (only when B is stable)

- **Separate indexes / views** (e.g. “by time” vs “by embedding”) are a **product** decision: same MongoDB, different queries.
- **RL / learned policies** for write/retrieve: only worth it once you have **signals** (user thumbs, task success, or janitor merge outcomes). Until then, **heuristics + thresholds + links** carry most of the value.

### 4. What *not* to do yet

- Replacing embeddings with a **full graph DB** wholesale—**extend MongoDB** first.
- Training an RL agent for memory—**instrument and measure** Phase A first.

---

## Versioning

| Date | Note |
|------|------|
| 2026-02-26 | Initial research synthesis + phased roadmap |
| 2026-02-26 | Added “How to proceed with the app you have” |
| 2026-04-02 | Added arXiv:2510.20345 (LLM-KG survey) mapping + Phase B2 (minimal schema & fusion) |
| 2026-10-06 | Added LangGraph memory docs ↔ OpenPill comparison + follow-ups |
| 2026-10-08 | Phase 1 correctness fixes done; follow-ups replaced by a phased improvement plan |
| 2026-10-08 | Phase 2 done: shared retrieval pipeline, similarity floor, memory-quality eval |
