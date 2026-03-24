# OpenPill Agent Policy (OpenClaw / tool-capable agents)

Use this as the default behavioral policy for agents connected to OpenPill tools.

## Core retrieval policy

1. Before answering memory-relevant user questions, call `semantic_search` first.
2. If top result confidence is weak or ambiguous, call `get_pill_neighbors` on the top hit(s).
3. Prefer citing retrieved facts over guessing.

## Core write policy

1. Ingest only durable knowledge (decisions, stable facts, non-obvious conclusions).
2. Avoid ingesting transient chatter or obvious generic statements.
3. For free-form text sources, use `ingest_text`.
4. For full multi-turn transcripts, use `ingest_conversation`.

## Markdown memory automation policy

When a markdown file represents memory notes:

1. Detect changed `.md` files in configured memory directories.
2. Ingest changed files via OpenPill (`POST /pills/ingest`) with a deterministic idempotency key.
3. Use `source_reference=md:<relative_path>` to preserve provenance.
4. Skip unchanged files to avoid noise and cost.

Use `scripts/ingest_markdown_memory.py` for this flow.

## Safety and cost policy

- Default to local-first model behavior.
- Respect external-model guards (`AB_GUARDS_ENABLED`, `AB_ALLOW_EXTERNAL`, `AB_MAX_EXTERNAL_CALLS`).
- Do not force external providers unless explicitly enabled.
