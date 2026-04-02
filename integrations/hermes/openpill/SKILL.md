---
name: openpill
description: Use OpenPill (REST) as durable memory for Hermes: semantic search, neighbors, and idempotent ingest.
version: 0.1.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [memory, rag, retrieval, openpill]
    category: integrations
    requires_toolsets: [terminal]
---

# OpenPill (durable memory bridge)

## When to Use

Use this skill when:

- the user asks about past decisions, project context, conventions, or "what do we know about X?"
- you need reliable, multi-session recall beyond bounded prompt notes
- you want to ingest a stable conclusion/decision so it can be retrieved later

## Setup

Set environment variables (recommended):

- `OPENPILL_API_BASE` (default: `http://localhost:8080`)
- `OPENPILL_API_KEY` (optional, but recommended in any shared/remote deployment)

## Install into Hermes

Hermes loads skills from `~/.hermes/skills/`. You can symlink this skill so it stays updated with this repo:

```bash
mkdir -p ~/.hermes/skills/integrations
ln -s "$(pwd)/integrations/hermes/openpill" ~/.hermes/skills/integrations/openpill
```

## Procedure (Read Path)

1. Run semantic search:

```bash
python3 integrations/hermes/openpill/openpill_client.py semantic "your query" --limit 10
```

2. If the top result is ambiguous or you need extra context, expand neighbors:

```bash
python3 integrations/hermes/openpill/openpill_client.py neighbors "<pill_id>"
```

3. Answer with short, sourced bullets:

- cite `pill_id` and `source_reference` where possible
- do not invent details when retrieval is available

## Procedure (Write Path)

Only ingest durable knowledge (decisions, stable facts, non-obvious conclusions). Avoid transient chatter.

1. Ingest a durable note (idempotent):

```bash
python3 integrations/hermes/openpill/openpill_client.py ingest-text \
  --title "Decision: use local-first policy" \
  --source "hermes:session:<session_id>" \
  --text "We default to local-first model routing; external models are opt-in and guarded."
```

2. Never ingest on pure research prompts unless explicitly asked.

## Pitfalls

- If OpenPill runs in Docker, do not assume `localhost` is reachable from another container. Use service DNS (e.g. `http://openpill-api:8080`) or correct host mapping.
- If auth is enabled, missing/incorrect `OPENPILL_API_KEY` will cause `401`.

## Verification

- `semantic` returns JSON with results.
- `neighbors` returns JSON with related pills.
- `ingest-text` returns `201` and a `pill_id` (retries with same inputs do not duplicate).

