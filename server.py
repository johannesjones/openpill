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
import logging
from datetime import datetime, timezone
from typing import Optional, Union

from dotenv import load_dotenv

# Must run before modules that read configuration at import time.
load_dotenv()

from bson import ObjectId  # noqa: E402
from bson.errors import InvalidId  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

from db import get_collection  # noqa: E402
from embeddings import embedding_text_for_doc, get_embedding
from models import DATE_FIELDS, KnowledgePill, PillSource, PillStatus, SourceType
from namespaces import (
    NamespaceError,
    format_namespace,
    is_within,
    mcp_namespace,
    parse_namespace,
    prefix_filter,
    resolve as resolve_namespace,
)
from pill_relations import list_active_conflict_pairs, neighbors_for_pill
from retrieval import semantic_retrieve
from temporal import history_push, parse_dt, rewrites_text, validity_filter

logger = logging.getLogger("openpill.mcp")
def _namespace(requested: Optional[str]) -> list[str] | None:
    """Request namespace narrowed to OPENPILL_MCP_NAMESPACE; raises NamespaceError."""
    return resolve_namespace(parse_namespace(requested), mcp_namespace())


def _namespace_error(exc: NamespaceError) -> str:
    return json.dumps({"error": str(exc)})


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
    namespace: Optional[str] = None,
    include_invalid: bool = False,
) -> str:
    """Search knowledge pills by full-text query, category, or tags.

    Args:
        query:    Free-text search across title and content.
        category: Filter by exact category name (e.g. "python", "architecture").
        tags:     Filter by one or more tags (AND logic).
        status:   Filter by status – "active" (default), "archived", or "deprecated".
        limit:    Max results to return (default 20, max 100).
        namespace: Namespace prefix (default: all, or OPENPILL_MCP_NAMESPACE).
        include_invalid: Also return pills that are no longer valid (invalid_at passed).

    Returns:
        JSON array of matching knowledge pills.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()
    limit = min(limit, 100)

    filter_doc: dict = {"status": status, **prefix_filter(ns)}
    if not include_invalid:
        filter_doc.update(validity_filter())

    if query:
        filter_doc["$text"] = {"$search": query}

    if category:
        filter_doc["category"] = category

    if tags:
        filter_doc["tags"] = {"$all": tags}

    projection = {"embedding": 0, "history": 0}

    cursor = col.find(filter_doc, projection).sort("created_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        for key in DATE_FIELDS:
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
async def get_pill(pill_id: str, namespace: Optional[str] = None) -> str:
    """Retrieve a single knowledge pill by its MongoDB ObjectId.

    Args:
        pill_id:   The 24-character hex ObjectId string.
        namespace: Only return the pill if it lies under this prefix.

    Returns:
        JSON object of the pill, or an error message.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError):
        return json.dumps({"error": f"Invalid ObjectId: {pill_id}"})

    doc = await col.find_one({"_id": oid}, {"embedding": 0})
    if doc is None or not is_within(doc.get("namespace"), ns):
        return json.dumps({"error": "Pill not found."})

    doc["_id"] = str(doc["_id"])
    for key in DATE_FIELDS:
        if isinstance(doc.get(key), datetime):
            doc[key] = doc[key].isoformat()

    return json.dumps(doc, ensure_ascii=False)


@mcp.tool()
async def get_pill_neighbors(pill_id: str, namespace: Optional[str] = None) -> str:
    """Explore the knowledge graph around one pill (1-hop).

    **When to call:** After `semantic_search` or `get_pill` when you need related
    context (dependencies, “see also”, contradictions) without another vector query.

    Args:
        pill_id:   24-char hex ObjectId of the anchor pill.
        namespace: Restrict the anchor and its neighbors to this prefix.

    Returns:
        JSON object: `pill_id`, `outgoing` (list of related target pills this pill
        points to), `incoming` (list of pills that reference this one). Each pill
        dict omits embeddings; dates are ISO strings. Errors return `{"error": "..."}`.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()
    try:
        center, outgoing, incoming = await neighbors_for_pill(col, pill_id, namespace=ns)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    if center is None:
        return json.dumps({"error": "Pill not found."})
    for row in outgoing + incoming:
        for key in DATE_FIELDS:
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
    namespace: Optional[str] = None,
    embed_text: Optional[str] = None,
    valid_at: Optional[str] = None,
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
        namespace:        Namespace to store the pill in (default: global, or
                          OPENPILL_MCP_NAMESPACE).
        embed_text:       Text to embed instead of title + content (e.g. a
                          readable summary when content is JSON).
        valid_at:         ISO datetime when the fact became true (default: now).

    Returns:
        JSON with the new pill's ID and a confirmation.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()

    pill = KnowledgePill(
        title=title,
        content=content,
        category=category,
        tags=tags or [],
        source=PillSource(type=SourceType(source_type), reference=source_reference),
        confidence=confidence,
        namespace=list(ns or []),
        embed_text=embed_text or None,
        valid_at=parse_dt(valid_at),
    )

    try:
        pill.embedding = await get_embedding(embedding_text_for_doc(pill.model_dump()))
    except Exception as exc:
        # The pill is still stored, but it stays invisible to semantic_search
        # until an embedding is backfilled.
        logger.warning("Embedding failed for %r; storing without one: %s", title, exc)

    result = await col.insert_one(pill.to_mongo())

    return json.dumps(
        {
            "message": "Pill created.",
            "id": str(result.inserted_id),
            "title": title,
            "namespace": format_namespace(ns),
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Tool 3b – Update an existing pill (same as REST PATCH /pills/{id})
# ---------------------------------------------------------------------------


@mcp.tool()
async def update_pill(
    pill_id: str,
    title: Optional[str] = None,
    content: Optional[Union[str, dict, list]] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    status: Optional[str] = None,
    embed_text: Optional[str] = None,
    namespace: Optional[str] = None,
    valid_at: Optional[str] = None,
    invalid_at: Optional[str] = None,
) -> str:
    """Update selected fields of an existing pill.

    **When to call:** After `search_pills` / `get_pill` when the fact changed and
    you already have the Mongo ObjectId. Same semantics as REST ``PATCH /pills/{id}``.

    Args:
        pill_id:  24-character hex ObjectId.
        title:    Replacement title (omit to leave unchanged).
        content:  Replacement content (omit to leave unchanged). FastMCP parses a
                  JSON-object string into a dict before validation, so dicts and
                  lists are accepted here and re-serialised.
        category: Replacement category.
        tags:     Replacement tag list (not merged).
        status:   Replacement status, e.g. "active", "archived".
        embed_text: Text to embed instead of title + content; "" clears it.
        namespace:  Only update the pill if it lies under this prefix.
        valid_at:   ISO datetime when the fact became true.
        invalid_at: ISO datetime when the fact stopped being true ("now" for now,
                    "" to make it valid again). Invalid pills are hidden from searches.

    Returns:
        JSON of the updated pill (embedding omitted). Errors: ``{"error": "..."}``.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()
    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError):
        return json.dumps({"error": f"Invalid ObjectId: {pill_id}"})

    doc = await col.find_one({"_id": oid})
    if doc is None or not is_within(doc.get("namespace"), ns):
        return json.dumps({"error": "Pill not found."})

    update_fields: dict = {}
    if title is not None:
        update_fields["title"] = title
    if content is not None:
        update_fields["content"] = (
            content if isinstance(content, str) else json.dumps(content)
        )
    if category is not None:
        update_fields["category"] = category
    if tags is not None:
        update_fields["tags"] = tags
    if status is not None:
        update_fields["status"] = status
    if embed_text is not None:
        update_fields["embed_text"] = embed_text or None
    for key, raw in (("valid_at", valid_at), ("invalid_at", invalid_at)):
        if raw is None:
            continue
        if raw == "":
            update_fields[key] = None
            continue
        value = datetime.now(timezone.utc) if raw == "now" else parse_dt(raw)
        if value is None:
            return json.dumps({"error": f"{key} must be an ISO datetime, 'now' or ''."})
        update_fields[key] = value

    if update_fields:
        new_text = embedding_text_for_doc({**doc, **update_fields})
        if new_text != embedding_text_for_doc(doc):
            try:
                update_fields["embedding"] = await get_embedding(new_text)
            except Exception as exc:
                logger.warning(
                    "Embedding refresh failed for %s; keeping the old vector: %s",
                    pill_id,
                    exc,
                )
        update_fields["updated_at"] = datetime.now(timezone.utc)
        update: dict = {"$set": update_fields}
        if rewrites_text(doc, update_fields):
            update.update(history_push(doc, reason="update_pill"))
        await col.update_one({"_id": oid}, update)
        doc = await col.find_one({"_id": oid}, {"embedding": 0})
        if doc is None:
            return json.dumps({"error": "Pill not found after update."})
    else:
        doc.pop("embedding", None)

    doc["_id"] = str(doc["_id"])
    for key in DATE_FIELDS:
        if isinstance(doc.get(key), datetime):
            doc[key] = doc[key].isoformat()
    return json.dumps(doc, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 3c – Archive a pill (same as REST DELETE /pills/{id})
# ---------------------------------------------------------------------------


@mcp.tool()
async def delete_pill(pill_id: str, namespace: Optional[str] = None) -> str:
    """Archive (soft-delete) a pill.

    **When to call:** The stored fact is wrong or the user asked to forget it.
    Same semantics as REST ``DELETE /pills/{id}`` (status becomes archived).

    Args:
        pill_id:   24-character hex ObjectId.
        namespace: Only archive the pill if it lies under this prefix.

    Returns:
        JSON ``{"message": "Pill archived.", "id": "..."}``.
        Errors: ``{"error": "..."}``.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()
    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError):
        return json.dumps({"error": f"Invalid ObjectId: {pill_id}"})

    result = await col.update_one(
        {"_id": oid, **prefix_filter(ns)},
        {"$set": {"status": PillStatus.ARCHIVED.value}},
    )
    if result.matched_count == 0:
        return json.dumps({"error": "Pill not found."})
    return json.dumps({"message": "Pill archived.", "id": pill_id})


# ---------------------------------------------------------------------------
# Tool 4 – List available categories
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_categories(namespace: Optional[str] = None) -> str:
    """List all distinct categories currently stored in the database.

    Args:
        namespace: Only count pills under this prefix.

    Returns:
        JSON array of category strings.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()
    categories = await col.distinct("category", {"status": "active", **prefix_filter(ns)})
    return json.dumps({"categories": sorted(categories)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 5 – Ingest raw text (auto-extract pills)
# ---------------------------------------------------------------------------


@mcp.tool()
async def ingest_text(
    text: str,
    source_reference: str = "",
    min_confidence: float = 0.5,
    namespace: Optional[str] = None,
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
        namespace:        Namespace for new pills; dedup stays inside it.

    Returns:
        JSON: `inserted`, `skipped_duplicate`, `skipped_confidence`, `skipped_short`,
        `stats`, etc. (same shape as REST `POST /pills/ingest`).
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    from extractor import run_extraction

    result = await run_extraction(
        text=text,
        source_reference=source_reference or "mcp:ingest_text",
        dry_run=False,
        min_confidence=min_confidence,
        namespace=ns,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def ingest_conversation(
    transcript: str,
    source_reference: str = "",
    min_confidence: float = 0.5,
    namespace: Optional[str] = None,
) -> str:
    """Turn a chat transcript into remembered facts (summarize + extract).

    **When to call:** End of session or after a substantive multi-turn chat;
    uses more LLM work than `ingest_text` (summarization step). For raw notes
    without dialogue format, use `ingest_text` instead.

    Args:
        transcript:       Full user/assistant transcript.
        source_reference: Session or chat id for provenance.
        min_confidence:   Min fact confidence (0.0–1.0, default 0.5).
        namespace:        Namespace for new pills; dedup stays inside it.

    Returns:
        JSON: same extraction summary shape as `ingest_text` / REST
        `POST /pills/ingest-conversation`.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    from extractor import run_conversation_extraction

    result = await run_conversation_extraction(
        transcript=transcript,
        source_reference=source_reference or "mcp:ingest_conversation",
        dry_run=False,
        min_confidence=min_confidence,
        namespace=ns,
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
    hybrid: bool = False,
    min_similarity: Optional[float] = None,
    namespace: Optional[str] = None,
    include_invalid: bool = False,
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
        hybrid:            Fuse keyword matches when there are few vector hits.
        min_similarity:    Drop hits below this cosine similarity (default: env
                           OPENPILL_SEMANTIC_MIN_SIMILARITY, unset = no floor).
        namespace:         Namespace prefix (default: all, or OPENPILL_MCP_NAMESPACE).
        include_invalid:   Also return invalidated pills (flagged, ranked last).

    Returns:
        JSON: `count`, `pills` (each with `similarity`, `retrieval_score`,
        `is_superseded`, `_id`, title, content, …), `retrieval_metrics`.
        Ordered by `retrieval_score`. No hits: also a `message`, `count` 0.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()
    limit = min(limit, 50)
    neighbor_limit = min(max(neighbor_limit, 0), 50)
    max_hops = min(max(max_hops, 1), 2)
    max_nodes = min(max(max_nodes, 5), 100)

    query_embedding = await get_embedding(query)
    result = await semantic_retrieve(
        col,
        query,
        query_embedding,
        category=category,
        limit=limit,
        expand_neighbors=expand_neighbors,
        neighbor_limit=neighbor_limit,
        max_hops=max_hops,
        max_nodes=max_nodes,
        hybrid=hybrid,
        min_similarity=min_similarity,
        namespace=ns,
        include_invalid=include_invalid,
    )
    if not result["count"]:
        result["message"] = "No matching pills found."
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 7 – List unresolved contradiction pairs
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_unresolved_conflicts(limit: int = 100, namespace: Optional[str] = None) -> str:
    """List active pills linked by ``conflicts_with`` (deduplicated pairs).

    Same data as ``GET /pills/conflicts``. Use after janitor runs or to audit
    contradictory memories before consolidation.

    Args:
        limit:     Max pairs to return (1–500, default 100). Check ``truncated`` in JSON.
        namespace: Only pairs whose pills lie under this prefix.

    Returns:
        JSON with ``total``, ``pairs`` (pill_id_a/b, title_a/b), ``truncated``.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()
    limit = min(max(int(limit), 1), 500)
    pairs, total = await list_active_conflict_pairs(col, limit=limit, namespace=ns)
    return json.dumps(
        {
            "total": total,
            "limit": limit,
            "truncated": total > len(pairs),
            "pairs": pairs,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Tool 8 – Undo a janitor consolidation
# ---------------------------------------------------------------------------


@mcp.tool()
async def undo_consolidation(pill_id: str, namespace: Optional[str] = None) -> str:
    """Revert a janitor consolidation: reactivate archived originals, archive the merged pill.

    Args:
        pill_id:   ObjectId of the consolidated (merged) pill to undo.
        namespace: Only undo if the merged pill lies under this prefix.

    Returns:
        JSON confirming which pills were reactivated.
    """
    try:
        ns = _namespace(namespace)
    except NamespaceError as exc:
        return _namespace_error(exc)
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError):
        return json.dumps({"error": f"Invalid ObjectId: {pill_id}"})

    doc = await col.find_one({"_id": oid})
    if doc is None or not is_within(doc.get("namespace"), ns):
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
