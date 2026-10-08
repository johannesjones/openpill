"""
REST API – OpenPill

FastAPI server exposing the same operations as the MCP server over standard
HTTP. Enables universal access from ChatGPT Actions, Open WebUI, scripts,
browser extensions, or any HTTP client.

Run:
    python api.py                          # port 8080
    uvicorn api:app --port 8080 --reload   # with hot-reload

OpenAPI spec available at /docs (Swagger UI) and /openapi.json.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

# Must run before modules that read configuration at import time.
load_dotenv()

from bson import ObjectId  # noqa: E402
from bson.errors import InvalidId  # noqa: E402
from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from db import close, get_collection  # noqa: E402
from embeddings import embedding_text_for_doc, get_embedding
from models import (
    DATE_FIELDS,
    KnowledgePill,
    PillRelation,
    PillSource,
    PillStatus,
    SourceType,
)
from namespaces import (
    NamespaceError,
    api_key_namespaces,
    format_namespace,
    is_within,
    parse_namespace,
    prefix_filter,
    resolve as resolve_namespace,
)
from pill_relations import list_active_conflict_pairs, neighbors_for_pill
from retrieval import (
    attach_consistency_metadata,
    count_conflict_relations,
    freshness_score,
    semantic_retrieve,
)
from temporal import fact_time, history_push, is_invalid, rewrites_text, validity_filter
from topics import build_topic_snapshot

logger = logging.getLogger("openpill.api")

# Optional: if set, all routes except public probes/docs require Bearer or X-API-Key.
# Prefer OPENPILL_API_KEY, keep legacy keys for compatibility.
OPENPILL_API_KEY = os.getenv("OPENPILL_API_KEY")
MEMORA_API_KEY = os.getenv("MEMORA_API_KEY")
KNOWLEDGE_PILL_API_KEY = os.getenv("KNOWLEDGE_PILL_API_KEY")
API_KEY = OPENPILL_API_KEY or MEMORA_API_KEY or KNOWLEDGE_PILL_API_KEY
# Optional: {"<key>": "<namespace prefix>"} keys bound to a namespace (see namespaces.py).
NAMESPACED_API_KEYS = api_key_namespaces()
AUTH_ENABLED = bool(API_KEY or NAMESPACED_API_KEYS)
_SCOPE_NAMESPACE = "openpill.namespace"


def _is_public_route(path: str, method: str) -> bool:
    """Routes that stay unauthenticated when API key auth is enabled."""
    if path == "/health":
        return True
    if path in ("/docs", "/openapi.json", "/redoc"):
        return True
    if path.startswith("/static"):
        return True
    if method == "GET" and path in ("/", "/app"):
        return True
    return False


def _presented_key(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key")


def _same_key(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _authenticate(request: Request) -> tuple[bool, Optional[list[str]]]:
    """Return (ok, bound namespace prefix). The shared API_KEY is unbound."""
    if not AUTH_ENABLED:
        return True, None
    presented = _presented_key(request)
    if not presented:
        return False, None
    if API_KEY and _same_key(presented, API_KEY):
        return True, None
    for key, prefix in NAMESPACED_API_KEYS.items():
        if _same_key(presented, key):
            return True, prefix
    return False, None


def _api_key_ok(request: Request) -> bool:
    return _authenticate(request)[0]


def _request_namespace(request: Request, requested: Optional[str]) -> Optional[list[str]]:
    """The namespace a request may act in: its own, narrowed to the key's prefix."""
    try:
        parsed = parse_namespace(requested)
    except NamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return resolve_namespace(parsed, request.scope.get(_SCOPE_NAMESPACE))
    except NamespaceError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


_NAMESPACE_QUERY = Query(
    default=None,
    description="Namespace prefix such as 'jjones/job_tracker' (default: all, "
    "or the API key's namespace).",
)


def _idempotency_header(request: Request) -> Optional[str]:
    return request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreatePillRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list)
    source_type: str = Field(default="manual")
    source_reference: str = Field(default="")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    namespace: Optional[str] = Field(default=None, description="e.g. 'jjones/job_tracker'")
    embed_text: Optional[str] = Field(
        default=None, description="Text to embed instead of title + content"
    )
    valid_at: Optional[datetime] = Field(
        default=None, description="When the fact became true (default: now)"
    )


class IngestRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source_reference: str = Field(default="")
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    namespace: Optional[str] = Field(default=None, description="Namespace for new pills")


class ConversationIngestRequest(BaseModel):
    transcript: str = Field(..., min_length=1)
    source_reference: str = Field(default="")
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    namespace: Optional[str] = Field(default=None, description="Namespace for new pills")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_doc(doc: dict) -> dict:
    """Convert a MongoDB document to a JSON-serializable dict."""
    doc["_id"] = str(doc["_id"])
    for key in DATE_FIELDS:
        if isinstance(doc.get(key), datetime):
            doc[key] = doc[key].isoformat()
    doc.pop("embedding", None)
    return doc


async def _is_superseded_in_db(col, pill_id: str) -> bool:
    """True if any active pill has an outgoing `supersedes` edge to this pill."""
    doc = await col.find_one(
        {
            "status": "active",
            "relations": {
                "$elemMatch": {"target_id": pill_id, "kind": "supersedes"}
            },
        },
        {"_id": 1},
    )
    return doc is not None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await close()


app = FastAPI(
    title="OpenPill API",
    description=(
        "REST interface for the OpenPill long-term memory system. "
        "Use /docs for interactive Swagger UI, /openapi.json for ChatGPT Actions."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# Last registered middleware runs first. Desired order: logging -> auth ->
# ingest body replay -> routes (so OpenAPI body models still work).
@app.middleware("http")
async def ingest_body_replay_middleware(request: Request, call_next):
    """Buffer POST body for ingest routes so FastAPI can parse models + we can hash bytes."""
    if request.method != "POST" or request.url.path not in (
        "/pills/ingest",
        "/pills/ingest-conversation",
    ):
        return await call_next(request)
    body = await request.body()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scoped = Request(request.scope, receive)
    scoped.state._ingest_body = body
    return await call_next(scoped)


# Middleware order: last registered runs first on the request. We want logging
# outermost, then auth.
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not AUTH_ENABLED or _is_public_route(request.url.path, request.method):
        return await call_next(request)
    ok, bound = _authenticate(request)
    if ok:
        request.scope[_SCOPE_NAMESPACE] = bound
        return await call_next(request)
    return JSONResponse(
        status_code=401,
        content={"detail": "Invalid or missing API key"},
    )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    request_id = request.headers.get("X-Request-Id") or request.headers.get("x-request-id")
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    payload = {
        "event": "request",
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": round(duration_ms, 2),
        "request_id": request_id,
    }
    logger.info(json.dumps(payload, separators=(",", ":"), default=str))
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def stats(request: Request, namespace: Optional[str] = _NAMESPACE_QUERY):
    """Aggregate counts for observability (active pills, graph edges)."""
    col = await get_collection()
    scope = prefix_filter(_request_namespace(request, namespace))
    active = await col.count_documents({"status": "active", **scope})
    with_rel = await col.count_documents(
        {"status": "active", "relations.0": {"$exists": True}, **scope}
    )
    return {
        "active_pills": active,
        "pills_with_relations": with_rel,
    }


@app.get("/pills/conflicts")
async def list_conflicts(
    request: Request,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Max conflict pairs to return (total may be larger).",
    ),
    namespace: Optional[str] = _NAMESPACE_QUERY,
):
    """List unresolved ``conflicts_with`` edges between active pills (deduplicated pairs).

    Populated by the janitor when it persists contradiction pairs, or by any writer
    that adds ``conflicts_with`` relations. Use for human review or agent tooling.
    """
    col = await get_collection()
    pairs, total = await list_active_conflict_pairs(
        col, limit=limit, namespace=_request_namespace(request, namespace)
    )
    return {
        "total": total,
        "limit": limit,
        "truncated": total > len(pairs),
        "pairs": pairs,
    }


@app.get("/pills/search")
async def search_pills(
    request: Request,
    q: Optional[str] = Query(default=None, description="Full-text search query"),
    category: Optional[str] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated tags (AND logic)"),
    status: str = Query(default="active"),
    limit: int = Query(default=20, ge=1, le=100),
    namespace: Optional[str] = _NAMESPACE_QUERY,
    include_invalid: bool = Query(
        default=False, description="Also return pills whose invalid_at has passed."
    ),
):
    """Search knowledge pills by keyword, category, or tags."""
    col = await get_collection()

    filter_doc: dict = {"status": status, **prefix_filter(_request_namespace(request, namespace))}
    if not include_invalid:
        filter_doc.update(validity_filter())
    if q:
        filter_doc["$text"] = {"$search": q}
    if category:
        filter_doc["category"] = category
    if tags:
        filter_doc["tags"] = {"$all": [t.strip() for t in tags.split(",")]}

    cursor = (
        col.find(filter_doc, {"embedding": 0, "history": 0}).sort("created_at", -1).limit(limit)
    )
    results = [_serialize_doc(doc) async for doc in cursor]
    return {"count": len(results), "pills": results}


@app.get("/pills/semantic")
async def semantic_search(
    request: Request,
    q: str = Query(..., description="Natural language query"),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    expand_neighbors: bool = Query(
        default=False,
        description="Include 1-hop related pills (deduped), with via_pill_id",
    ),
    neighbor_limit: int = Query(
        default=10,
        ge=0,
        le=50,
        description="Max extra pills to add from graph expansion",
    ),
    max_hops: int = Query(
        default=1,
        ge=1,
        le=2,
        description="Graph traversal depth for neighbor expansion (1=default, 2=optional).",
    ),
    max_nodes: int = Query(
        default=30,
        ge=5,
        le=100,
        description="Hard cap on total pills returned after expansion.",
    ),
    hybrid: bool = Query(
        default=False,
        description="Enable hybrid retrieval fusion (vector + lexical fallback).",
    ),
    min_similarity: Optional[float] = Query(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Drop vector hits below this cosine similarity "
        "(default: OPENPILL_SEMANTIC_MIN_SIMILARITY, unset = no floor).",
    ),
    namespace: Optional[str] = _NAMESPACE_QUERY,
    include_invalid: bool = Query(
        default=False,
        description="Also return invalidated pills (flagged is_invalid, ranked last).",
    ),
):
    """Find pills by meaning using vector similarity + consistency metadata."""
    ns = _request_namespace(request, namespace)
    col = await get_collection()
    query_embedding = await get_embedding(q)
    return await semantic_retrieve(
        col,
        q,
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


@app.get("/pills/{pill_id}/neighbors")
async def get_pill_neighbors(
    pill_id: str, request: Request, namespace: Optional[str] = _NAMESPACE_QUERY
):
    """Outgoing and incoming related pills (1-hop graph edges)."""
    ns = _request_namespace(request, namespace)
    col = await get_collection()
    try:
        center, outgoing, incoming = await neighbors_for_pill(col, pill_id, namespace=ns)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if center is None:
        raise HTTPException(status_code=404, detail="Pill not found")
    for row in outgoing:
        for key in DATE_FIELDS:
            if isinstance(row.get(key), datetime):
                row[key] = row[key].isoformat()
        is_superseded = await _is_superseded_in_db(col, row.get("_id", ""))
        attach_consistency_metadata(
            row,
            confidence=float(row.get("confidence", 1.0)),
            freshness=freshness_score(row.get("updated_at")),
            conflict_count=count_conflict_relations(row),
            is_superseded=is_superseded,
        )
    for row in incoming:
        for key in DATE_FIELDS:
            if isinstance(row.get(key), datetime):
                row[key] = row[key].isoformat()
        is_superseded = await _is_superseded_in_db(col, row.get("_id", ""))
        attach_consistency_metadata(
            row,
            confidence=float(row.get("confidence", 1.0)),
            freshness=freshness_score(row.get("updated_at")),
            conflict_count=count_conflict_relations(row),
            is_superseded=is_superseded,
        )
    return {
        "pill_id": pill_id,
        "outgoing": outgoing,
        "incoming": incoming,
    }


@app.get("/pills/{pill_id}")
async def get_pill(pill_id: str, request: Request, namespace: Optional[str] = _NAMESPACE_QUERY):
    """Retrieve a single pill by its ObjectId."""
    ns = _request_namespace(request, namespace)
    col = await get_collection()
    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    doc = await col.find_one({"_id": oid}, {"embedding": 0})
    if doc is None or not is_within(doc.get("namespace"), ns):
        raise HTTPException(status_code=404, detail="Pill not found")
    out = _serialize_doc(doc)
    is_superseded = await _is_superseded_in_db(col, out["_id"])
    attach_consistency_metadata(
        out,
        confidence=float(doc.get("confidence", 1.0)),
        freshness=freshness_score(fact_time(doc)),
        conflict_count=count_conflict_relations(doc),
        is_superseded=is_superseded,
    )
    out["is_invalid"] = is_invalid(doc)
    return out


class UpdatePillRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    status: Optional[str] = None
    relations: Optional[list[PillRelation]] = None
    embed_text: Optional[str] = Field(
        default=None,
        description="Text to embed instead of title + content; '' clears the override",
    )
    valid_at: Optional[datetime] = Field(default=None, description="When the fact became true")
    invalid_at: Optional[datetime] = Field(
        default=None,
        description="When the fact stopped being true; send null explicitly to revalidate",
    )


@app.patch("/pills/{pill_id}")
async def update_pill(
    pill_id: str,
    req: UpdatePillRequest,
    request: Request,
    namespace: Optional[str] = _NAMESPACE_QUERY,
):
    """Update selected fields of a pill and re-embed if its embedding text changed."""
    ns = _request_namespace(request, namespace)
    col = await get_collection()
    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    doc = await col.find_one({"_id": oid})
    if doc is None or not is_within(doc.get("namespace"), ns):
        raise HTTPException(status_code=404, detail="Pill not found")

    update_fields: dict = {}

    if req.title is not None:
        update_fields["title"] = req.title
    if req.content is not None:
        update_fields["content"] = req.content
    if req.category is not None:
        update_fields["category"] = req.category
    if req.tags is not None:
        update_fields["tags"] = req.tags
    if req.status is not None:
        update_fields["status"] = req.status
    if req.relations is not None:
        update_fields["relations"] = [r.model_dump(mode="json") for r in req.relations]
    if req.embed_text is not None:
        update_fields["embed_text"] = req.embed_text or None
    # Explicit null clears a date, so check what was sent rather than the value.
    for key in ("valid_at", "invalid_at"):
        if key in req.model_fields_set:
            update_fields[key] = getattr(req, key)

    if not update_fields:
        return _serialize_doc(doc)

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
        update.update(history_push(doc, reason="patch"))
    await col.update_one({"_id": oid}, update)

    updated = await col.find_one({"_id": oid})
    if updated is None:
        raise HTTPException(status_code=404, detail="Pill not found after update")
    return _serialize_doc(updated)


@app.post("/pills", status_code=201)
async def create_pill(req: CreatePillRequest, request: Request):
    """Create a new knowledge pill (auto-embeds on creation)."""
    ns = _request_namespace(request, req.namespace)
    col = await get_collection()

    pill = KnowledgePill(
        title=req.title,
        content=req.content,
        category=req.category,
        tags=req.tags,
        source=PillSource(type=SourceType(req.source_type), reference=req.source_reference),
        confidence=req.confidence,
        namespace=list(ns or []),
        embed_text=req.embed_text or None,
        valid_at=req.valid_at,
    )

    try:
        pill.embedding = await get_embedding(embedding_text_for_doc(pill.model_dump()))
    except Exception as exc:
        # The pill is still stored, but it stays invisible to /pills/semantic
        # until an embedding is backfilled.
        logger.warning(
            "Embedding failed for %r; storing without one: %s", req.title, exc
        )

    result = await col.insert_one(pill.to_mongo())
    return {
        "message": "Pill created.",
        "id": str(result.inserted_id),
        "title": req.title,
        "namespace": format_namespace(ns),
    }


@app.post("/pills/ingest")
async def ingest_text(request: Request, req: IngestRequest):
    """Extract knowledge pills from raw text via LLM.

    Optional header ``Idempotency-Key``: same key + same JSON body replays the
    first successful response within the TTL window (see docs/OPS.md).
    """
    from idempotency import resolve_idempotency, store_idempotent_response

    body_bytes = getattr(request.state, "_ingest_body", b"")
    route = "/pills/ingest"
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    idem_key = _idempotency_header(request)

    ns = _request_namespace(request, req.namespace)
    replay = await resolve_idempotency(idem_key, route, body_hash)
    if replay is not None:
        return replay

    from extractor import run_extraction

    result = await run_extraction(
        text=req.text,
        source_reference=req.source_reference or "api:ingest",
        dry_run=False,
        min_confidence=req.min_confidence,
        namespace=ns,
    )
    await store_idempotent_response(idem_key, route, body_hash, result)
    return result


@app.post("/pills/ingest-conversation")
async def ingest_conversation(request: Request, req: ConversationIngestRequest):
    """Summarize a conversation transcript and extract pills via LLM.

    Optional ``Idempotency-Key`` header (same semantics as ``POST /pills/ingest``).
    """
    from idempotency import resolve_idempotency, store_idempotent_response

    body_bytes = getattr(request.state, "_ingest_body", b"")
    route = "/pills/ingest-conversation"
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    idem_key = _idempotency_header(request)

    ns = _request_namespace(request, req.namespace)
    replay = await resolve_idempotency(idem_key, route, body_hash)
    if replay is not None:
        return replay

    from extractor import run_conversation_extraction

    result = await run_conversation_extraction(
        transcript=req.transcript,
        source_reference=req.source_reference or "api:ingest-conversation",
        dry_run=False,
        min_confidence=req.min_confidence,
        namespace=ns,
    )
    await store_idempotent_response(idem_key, route, body_hash, result)
    return result


@app.get("/categories")
async def list_categories(request: Request, namespace: Optional[str] = _NAMESPACE_QUERY):
    """List all distinct categories of active pills."""
    scope = prefix_filter(_request_namespace(request, namespace))
    col = await get_collection()
    categories = await col.distinct("category", {"status": "active", **scope})
    return {"categories": sorted(categories)}


@app.get("/topics/snapshot")
async def topics_snapshot(
    request: Request,
    top_terms: int = Query(default=20, ge=1, le=100),
    per_category: int = Query(default=10, ge=1, le=50),
    min_doc_freq: int = Query(default=2, ge=1, le=20),
    min_token_len: int = Query(default=3, ge=2, le=20),
    namespace: Optional[str] = _NAMESPACE_QUERY,
):
    """Classical NLP topic overview over active pills (read-only analytics)."""
    ns = _request_namespace(request, namespace)
    return await build_topic_snapshot(
        top_terms=top_terms,
        per_category=per_category,
        min_doc_freq=min_doc_freq,
        min_token_len=min_token_len,
        namespace=ns,
    )


@app.delete("/pills/{pill_id}/consolidation")
async def undo_consolidation(
    pill_id: str, request: Request, namespace: Optional[str] = _NAMESPACE_QUERY
):
    """Revert a janitor consolidation: reactivate archived originals."""
    ns = _request_namespace(request, namespace)
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    doc = await col.find_one({"_id": oid})
    if doc is None or not is_within(doc.get("namespace"), ns):
        raise HTTPException(status_code=404, detail="Pill not found")

    ref = doc.get("source", {}).get("reference", "")
    if not ref.startswith("janitor:merged:"):
        raise HTTPException(status_code=400, detail="This pill is not a janitor consolidation")

    original_ids = ref.replace("janitor:merged:", "").split(",")
    original_oids = [ObjectId(oid_str) for oid_str in original_ids if oid_str]

    await col.update_many(
        {"_id": {"$in": original_oids}},
        {"$set": {"status": PillStatus.ACTIVE.value}},
    )
    await col.update_one(
        {"_id": oid},
        {"$set": {"status": PillStatus.ARCHIVED.value}},
    )

    return {
        "message": "Consolidation undone.",
        "reactivated": len(original_ids),
        "reactivated_ids": original_ids,
        "archived_merged_id": pill_id,
    }


@app.delete("/pills/{pill_id}")
async def delete_pill(pill_id: str, request: Request, namespace: Optional[str] = _NAMESPACE_QUERY):
    """Archive (soft-delete) a pill."""
    ns = _request_namespace(request, namespace)
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    result = await col.update_one(
        {"_id": oid, **prefix_filter(ns)},
        {"$set": {"status": PillStatus.ARCHIVED.value}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Pill not found")

    return {"message": "Pill archived.", "id": pill_id}


@app.get("/", response_class=HTMLResponse)
@app.get("/app", response_class=HTMLResponse)
async def web_app() -> HTMLResponse:
    """Serve the small web UI for saving chats and managing pills."""
    with open("static/index.html", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
