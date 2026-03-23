"""
MCP Server – OpenPill

Exposes OpenPill memory entries stored in MongoDB as MCP tools so any
MCP-compatible AI client (Cursor, Claude Desktop, ...) can query them.

Run:
    python server.py              # stdio transport (default for Cursor)
    python server.py --sse        # SSE transport for remote clients
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from mcp.server.fastmcp import FastMCP

from db import get_collection
from embeddings import cosine_similarity, embed_text_for_pill, get_embedding
from models import KnowledgePill, PillSource, PillStatus, SourceType
from pill_relations import expand_semantic_neighbors_hops, neighbors_for_pill

mcp = FastMCP(
    "OpenPill",
    instructions=(
        "Long-term memory layer: stores and retrieves distilled memory entries "
        "extracted from conversations, documents, and code."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1 – Search / Retrieve pills
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_pills(
    query: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    status: str = "active",
    limit: int = 20,
) -> str:
    """Search knowledge pills by full-text query, category, or tags.

    Args:
        query:    Free-text search across title and content.
        category: Filter by exact category name (e.g. "python", "architecture").
        tags:     Filter by one or more tags (AND logic).
        status:   Filter by status – "active" (default), "archived", or "deprecated".
        limit:    Max results to return (default 20, max 100).

    Returns:
        JSON array of matching knowledge pills.
    """
    col = await get_collection()
    limit = min(limit, 100)

    filter_doc: dict = {"status": status}

    if query:
        filter_doc["$text"] = {"$search": query}

    if category:
        filter_doc["category"] = category

    if tags:
        filter_doc["tags"] = {"$all": tags}

    projection = {"embedding": 0}

    cursor = col.find(filter_doc, projection).sort("created_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        for key in ("created_at", "updated_at", "expires_at"):
            if isinstance(doc.get(key), datetime):
                doc[key] = doc[key].isoformat()
        results.append(doc)

    if not results:
        return json.dumps({"message": "No pills found.", "count": 0})

    return json.dumps({"count": len(results), "pills": results}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 2 – Get a single pill by ID
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_pill(pill_id: str) -> str:
    """Retrieve a single knowledge pill by its MongoDB ObjectId.

    Args:
        pill_id: The 24-character hex ObjectId string.

    Returns:
        JSON object of the pill, or an error message.
    """
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError):
        return json.dumps({"error": f"Invalid ObjectId: {pill_id}"})

    doc = await col.find_one({"_id": oid}, {"embedding": 0})
    if doc is None:
        return json.dumps({"error": "Pill not found."})

    doc["_id"] = str(doc["_id"])
    for key in ("created_at", "updated_at", "expires_at"):
        if isinstance(doc.get(key), datetime):
            doc[key] = doc[key].isoformat()

    return json.dumps(doc, ensure_ascii=False)


@mcp.tool()
async def get_pill_neighbors(pill_id: str) -> str:
    """Explore the knowledge graph around one pill (1-hop).

    **When to call:** After `semantic_search` or `get_pill` when you need related
    context (dependencies, “see also”, contradictions) without another vector query.

    Args:
        pill_id: 24-char hex ObjectId of the anchor pill.

    Returns:
        JSON object: `pill_id`, `outgoing` (list of related target pills this pill
        points to), `incoming` (list of pills that reference this one). Each pill
        dict omits embeddings; dates are ISO strings. Errors return `{"error": "..."}`.
    """
    col = await get_collection()
    try:
        center, outgoing, incoming = await neighbors_for_pill(col, pill_id)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    if center is None:
        return json.dumps({"error": "Pill not found."})
    for row in outgoing + incoming:
        for key in ("created_at", "updated_at", "expires_at"):
            if isinstance(row.get(key), datetime):
                row[key] = row[key].isoformat()
    return json.dumps(
        {"pill_id": pill_id, "outgoing": outgoing, "incoming": incoming},
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Tool 3 – Create a new pill
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_pill(
    title: str,
    content: str,
    category: str,
    tags: Optional[list[str]] = None,
    source_type: str = "manual",
    source_reference: str = "",
    confidence: float = 1.0,
) -> str:
    """Store a new knowledge pill in the database.

    Args:
        title:            Short descriptive title.
        content:          The distilled fact or knowledge.
        category:         Category (e.g. "python", "devops", "architecture").
        tags:             Optional list of tags for filtering.
        source_type:      Origin type – "chat", "document", "manual", or "code".
        source_reference: Chat ID, file path, or URL that sourced this pill.
        confidence:       Confidence score 0.0-1.0 (default 1.0).

    Returns:
        JSON with the new pill's ID and a confirmation.
    """
    col = await get_collection()

    pill = KnowledgePill(
        title=title,
        content=content,
        category=category,
        tags=tags or [],
        source=PillSource(type=SourceType(source_type), reference=source_reference),
        confidence=confidence,
    )

    try:
        pill.embedding = await get_embedding(embed_text_for_pill(title, content))
    except Exception:
        pass  # non-critical: pill is still useful without an embedding

    result = await col.insert_one(pill.to_mongo())

    return json.dumps(
        {
            "message": "Pill created.",
            "id": str(result.inserted_id),
            "title": title,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Tool 4 – List available categories
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_categories() -> str:
    """List all distinct categories currently stored in the database.

    Returns:
        JSON array of category strings.
    """
    col = await get_collection()
    categories = await col.distinct("category", {"status": "active"})
    return json.dumps({"categories": sorted(categories)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 5 – Ingest raw text (auto-extract pills)
# ---------------------------------------------------------------------------


@mcp.tool()
async def ingest_text(
    text: str,
    source_reference: str = "",
    min_confidence: float = 0.5,
) -> str:
    """Ingest unstructured text into long-term memory (LLM extraction + dedup).

    **When to call:** User pasted notes, logs, or a doc chunk you should remember;
    not for tiny one-liners—prefer `create_pill` for a single explicit fact.

    Uses an LLM to distill atomic facts, merges near-duplicates via embedding
    similarity, and inserts new pills.

    Args:
        text:             Raw text to mine (can be long).
        source_reference: Provenance label (path, URL, chat id); shown on pills.
        min_confidence:   Drop facts below this threshold (0.0–1.0, default 0.5).

    Returns:
        JSON: `inserted`, `skipped_duplicate`, `skipped_confidence`, `skipped_short`,
        `stats`, etc. (same shape as REST `POST /pills/ingest`).
    """
    from extractor import run_extraction

    result = await run_extraction(
        text=text,
        source_reference=source_reference or "mcp:ingest_text",
        dry_run=False,
        min_confidence=min_confidence,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def ingest_conversation(
    transcript: str,
    source_reference: str = "",
    min_confidence: float = 0.5,
) -> str:
    """Turn a chat transcript into remembered facts (summarize + extract).

    **When to call:** End of session or after a substantive multi-turn chat;
    uses more LLM work than `ingest_text` (summarization step). For raw notes
    without dialogue format, use `ingest_text` instead.

    Args:
        transcript:       Full user/assistant transcript.
        source_reference: Session or chat id for provenance.
        min_confidence:   Min fact confidence (0.0–1.0, default 0.5).

    Returns:
        JSON: same extraction summary shape as `ingest_text` / REST
        `POST /pills/ingest-conversation`.
    """
    from extractor import run_conversation_extraction

    result = await run_conversation_extraction(
        transcript=transcript,
        source_reference=source_reference or "mcp:ingest_conversation",
        dry_run=False,
        min_confidence=min_confidence,
    )
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 6 – Semantic search (vector similarity)
# ---------------------------------------------------------------------------


@mcp.tool()
async def semantic_search(
    query: str,
    category: Optional[str] = None,
    limit: int = 10,
    expand_neighbors: bool = False,
    neighbor_limit: int = 10,
    max_hops: int = 1,
    max_nodes: int = 30,
) -> str:
    """Vector search over pills by meaning (primary recall tool for memory).

    **When to call:** Almost any “what do we know about X?” question, or before
    answering from stored knowledge. Prefer over `search_pills` when wording may
    not match stored keywords. Set `expand_neighbors` true to pull 1-hop graph
    context after the top vector hits.

    Args:
        query:             Natural-language question or topic.
        category:          Restrict to one category if known.
        limit:             Top-k similar pills (≤50).
        expand_neighbors:  Add related pills via graph edges (deduped); adds `via_pill_id`.
        neighbor_limit:    Cap on extra neighbor pills (≤50).
        max_hops:          Traversal depth for expansion (1 default, 2 optional).
        max_nodes:         Hard cap on total pills returned after expansion.

    Returns:
        JSON: `count`, `pills` (each with `similarity` float, `_id`, title, content, …).
        Empty store: `{"message": "...", "count": 0}`.
    """
    col = await get_collection()
    limit = min(limit, 50)
    neighbor_limit = min(max(neighbor_limit, 0), 50)
    max_hops = min(max(max_hops, 1), 2)
    max_nodes = min(max(max_nodes, 5), 100)

    query_embedding = await get_embedding(query)

    filter_doc: dict = {"status": "active", "embedding": {"$exists": True, "$ne": None}}
    if category:
        filter_doc["category"] = category

    candidates = []
    async for doc in col.find(filter_doc):
        score = cosine_similarity(query_embedding, doc["embedding"])
        doc["_id"] = str(doc["_id"])
        del doc["embedding"]
        for key in ("created_at", "updated_at", "expires_at"):
            if isinstance(doc.get(key), datetime):
                doc[key] = doc[key].isoformat()
        doc["similarity"] = round(score, 4)
        candidates.append(doc)

    candidates.sort(key=lambda d: d["similarity"], reverse=True)
    results = candidates[:limit]

    if not results:
        return json.dumps({"message": "No pills with embeddings found.", "count": 0})

    if expand_neighbors and neighbor_limit > 0:
        results = await expand_semantic_neighbors_hops(
            col,
            results,
            neighbor_limit=neighbor_limit,
            max_hops=max_hops,
            max_nodes=max_nodes,
        )

    return json.dumps({"count": len(results), "pills": results}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 7 – Undo a janitor consolidation
# ---------------------------------------------------------------------------


@mcp.tool()
async def undo_consolidation(pill_id: str) -> str:
    """Revert a janitor consolidation: reactivate archived originals, archive the merged pill.

    Args:
        pill_id: ObjectId of the consolidated (merged) pill to undo.

    Returns:
        JSON confirming which pills were reactivated.
    """
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError):
        return json.dumps({"error": f"Invalid ObjectId: {pill_id}"})

    doc = await col.find_one({"_id": oid})
    if doc is None:
        return json.dumps({"error": "Pill not found."})

    ref = doc.get("source", {}).get("reference", "")
    if not ref.startswith("janitor:merged:"):
        return json.dumps({"error": "This pill is not a janitor consolidation."})

    original_ids = ref.replace("janitor:merged:", "").split(",")
    original_oids = [ObjectId(oid_str) for oid_str in original_ids if oid_str]

    result = await col.update_many(
        {"_id": {"$in": original_oids}},
        {"$set": {"status": PillStatus.ACTIVE.value}},
    )

    await col.update_one(
        {"_id": oid},
        {"$set": {"status": PillStatus.ARCHIVED.value}},
    )

    return json.dumps(
        {
            "message": "Consolidation undone.",
            "reactivated": len(original_ids),
            "reactivated_ids": original_ids,
            "archived_merged_id": pill_id,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sse", action="store_true", help="Use SSE transport")
    args = parser.parse_args()

    transport = "sse" if args.sse else "stdio"
    mcp.run(transport=transport)
