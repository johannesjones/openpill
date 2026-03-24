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
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import close, get_collection
from embeddings import cosine_similarity, embed_text_for_pill, get_embedding
from models import KnowledgePill, PillRelation, PillSource, PillStatus, SourceType
from pill_relations import expand_semantic_neighbors_hops, neighbors_for_pill
from topics import build_topic_snapshot

logger = logging.getLogger("openpill.api")

# Optional: if set, all routes except public probes/docs require Bearer or X-API-Key.
# Prefer OPENPILL_API_KEY, keep legacy keys for compatibility.
OPENPILL_API_KEY = os.getenv("OPENPILL_API_KEY")
MEMORA_API_KEY = os.getenv("MEMORA_API_KEY")
KNOWLEDGE_PILL_API_KEY = os.getenv("KNOWLEDGE_PILL_API_KEY")
API_KEY = OPENPILL_API_KEY or MEMORA_API_KEY or KNOWLEDGE_PILL_API_KEY


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


def _api_key_ok(request: Request) -> bool:
    if not API_KEY:
        return True
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        if auth[7:].strip() == API_KEY:
            return True
    x_key = request.headers.get("X-API-Key")
    if x_key and x_key == API_KEY:
        return True
    return False


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


class IngestRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source_reference: str = Field(default="")
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ConversationIngestRequest(BaseModel):
    transcript: str = Field(..., min_length=1)
    source_reference: str = Field(default="")
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_doc(doc: dict) -> dict:
    """Convert a MongoDB document to a JSON-serializable dict."""
    doc["_id"] = str(doc["_id"])
    for key in ("created_at", "updated_at", "expires_at"):
        if isinstance(doc.get(key), datetime):
            doc[key] = doc[key].isoformat()
    doc.pop("embedding", None)
    return doc


def _count_conflict_relations(doc: dict) -> int:
    rels = doc.get("relations") or []
    return sum(1 for r in rels if r.get("kind") == "conflicts_with")


def _freshness_score(dt: datetime | None) -> float:
    """Recency score in [0,1], linear decay over 30 days."""
    if not isinstance(dt, datetime):
        return 0.5
    now = datetime.now(timezone.utc)
    ref = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    age_days = max((now - ref).total_seconds() / 86400.0, 0.0)
    return round(max(0.0, 1.0 - min(age_days / 30.0, 1.0)), 4)


def _attach_consistency_metadata(
    payload: dict,
    *,
    confidence: float,
    freshness: float,
    conflict_count: int,
    is_superseded: bool = False,
    similarity: float | None = None,
) -> dict:
    """Attach retrieval-time consistency hints used by clients/agents."""
    retrieval_score = 0.6 * confidence + 0.25 * freshness
    if similarity is not None:
        retrieval_score += 0.15 * similarity
    if conflict_count > 0:
        retrieval_score -= min(0.2, 0.05 * conflict_count)
    if is_superseded:
        retrieval_score -= 0.15
    payload["confidence_score"] = round(confidence, 4)
    payload["freshness_score"] = round(freshness, 4)
    payload["conflict_count"] = conflict_count
    payload["is_superseded"] = is_superseded
    payload["retrieval_score"] = round(max(0.0, min(1.0, retrieval_score)), 4)
    warnings: list[str] = []
    if conflict_count > 0:
        warnings.append(
            f"This memory has {conflict_count} conflict relation(s); verify recency/context."
        )
    if is_superseded:
        warnings.append(
            "This memory appears superseded by a newer active memory; treat as historical context."
        )
    if warnings:
        payload["consistency_warning"] = " ".join(warnings)
    return payload


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
    if not API_KEY or _is_public_route(request.url.path, request.method):
        return await call_next(request)
    if _api_key_ok(request):
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
async def stats():
    """Aggregate counts for observability (active pills, graph edges)."""
    col = await get_collection()
    active = await col.count_documents({"status": "active"})
    with_rel = await col.count_documents(
        {"status": "active", "relations.0": {"$exists": True}}
    )
    return {
        "active_pills": active,
        "pills_with_relations": with_rel,
    }


@app.get("/pills/search")
async def search_pills(
    q: Optional[str] = Query(default=None, description="Full-text search query"),
    category: Optional[str] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated tags (AND logic)"),
    status: str = Query(default="active"),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Search knowledge pills by keyword, category, or tags."""
    col = await get_collection()

    filter_doc: dict = {"status": status}
    if q:
        filter_doc["$text"] = {"$search": q}
    if category:
        filter_doc["category"] = category
    if tags:
        filter_doc["tags"] = {"$all": [t.strip() for t in tags.split(",")]}

    cursor = col.find(filter_doc, {"embedding": 0}).sort("created_at", -1).limit(limit)
    results = [_serialize_doc(doc) async for doc in cursor]
    return {"count": len(results), "pills": results}


@app.get("/pills/semantic")
async def semantic_search(
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
):
    """Find pills by meaning using vector similarity + consistency metadata."""
    col = await get_collection()
    query_embedding = await get_embedding(q)

    filter_doc: dict = {"status": "active", "embedding": {"$exists": True, "$ne": None}}
    if category:
        filter_doc["category"] = category

    candidates = []
    superseded_ids: set[str] = set()
    async for doc in col.find(filter_doc):
        score = cosine_similarity(query_embedding, doc["embedding"])
        source_id = str(doc.get("_id"))
        for rel in doc.get("relations") or []:
            if rel.get("kind") == "supersedes" and rel.get("target_id"):
                superseded_ids.add(rel["target_id"])
        serialized = _serialize_doc(doc)
        serialized["similarity"] = round(score, 4)
        _attach_consistency_metadata(
            serialized,
            confidence=float(doc.get("confidence", 1.0)),
            freshness=_freshness_score(doc.get("updated_at")),
            conflict_count=_count_conflict_relations(doc),
            is_superseded=source_id in superseded_ids,
            similarity=score,
        )
        candidates.append(serialized)

    for row in candidates:
        sid = row.get("_id")
        if sid in superseded_ids and not row.get("is_superseded"):
            _attach_consistency_metadata(
                row,
                confidence=float(row.get("confidence", 1.0)),
                freshness=_freshness_score(
                    datetime.fromisoformat(row["updated_at"])
                )
                if isinstance(row.get("updated_at"), str)
                else 0.5,
                conflict_count=_count_conflict_relations(row),
                is_superseded=True,
                similarity=float(row.get("similarity", 0.0)),
            )

    candidates.sort(key=lambda d: d["similarity"], reverse=True)
    results = candidates[:limit]
    if expand_neighbors:
        results = await expand_semantic_neighbors_hops(
            col,
            results,
            neighbor_limit=neighbor_limit,
            max_hops=max_hops,
            max_nodes=max_nodes,
        )
        for row in results:
            row_conf = float(row.get("confidence", 1.0))
            row_fresh = _freshness_score(datetime.fromisoformat(row["updated_at"])) if isinstance(row.get("updated_at"), str) else 0.5
            row_conflicts = _count_conflict_relations(row)
            _attach_consistency_metadata(
                row,
                confidence=row_conf,
                freshness=row_fresh,
                conflict_count=row_conflicts,
                similarity=float(row.get("similarity", 0.0)),
            )
    results.sort(key=lambda d: d.get("retrieval_score", 0.0), reverse=True)
    return {"count": len(results), "pills": results}


@app.get("/pills/{pill_id}/neighbors")
async def get_pill_neighbors(pill_id: str):
    """Outgoing and incoming related pills (1-hop graph edges)."""
    col = await get_collection()
    try:
        center, outgoing, incoming = await neighbors_for_pill(col, pill_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if center is None:
        raise HTTPException(status_code=404, detail="Pill not found")
    for row in outgoing:
        for key in ("created_at", "updated_at", "expires_at"):
            if isinstance(row.get(key), datetime):
                row[key] = row[key].isoformat()
        is_superseded = await _is_superseded_in_db(col, row.get("_id", ""))
        _attach_consistency_metadata(
            row,
            confidence=float(row.get("confidence", 1.0)),
            freshness=_freshness_score(datetime.fromisoformat(row["updated_at"])) if isinstance(row.get("updated_at"), str) else 0.5,
            conflict_count=_count_conflict_relations(row),
            is_superseded=is_superseded,
        )
    for row in incoming:
        for key in ("created_at", "updated_at", "expires_at"):
            if isinstance(row.get(key), datetime):
                row[key] = row[key].isoformat()
        is_superseded = await _is_superseded_in_db(col, row.get("_id", ""))
        _attach_consistency_metadata(
            row,
            confidence=float(row.get("confidence", 1.0)),
            freshness=_freshness_score(datetime.fromisoformat(row["updated_at"])) if isinstance(row.get("updated_at"), str) else 0.5,
            conflict_count=_count_conflict_relations(row),
            is_superseded=is_superseded,
        )
    return {
        "pill_id": pill_id,
        "outgoing": outgoing,
        "incoming": incoming,
    }


@app.get("/pills/{pill_id}")
async def get_pill(pill_id: str):
    """Retrieve a single pill by its ObjectId."""
    col = await get_collection()
    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    doc = await col.find_one({"_id": oid}, {"embedding": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail="Pill not found")
    out = _serialize_doc(doc)
    is_superseded = await _is_superseded_in_db(col, out["_id"])
    _attach_consistency_metadata(
        out,
        confidence=float(doc.get("confidence", 1.0)),
        freshness=_freshness_score(doc.get("updated_at")),
        conflict_count=_count_conflict_relations(doc),
        is_superseded=is_superseded,
    )
    return out


class UpdatePillRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    status: Optional[str] = None
    relations: Optional[list[PillRelation]] = None


@app.patch("/pills/{pill_id}")
async def update_pill(pill_id: str, req: UpdatePillRequest):
    """Update selected fields of a pill and re-embed if title/content changed."""
    col = await get_collection()
    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    doc = await col.find_one({"_id": oid})
    if doc is None:
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

    if not update_fields:
        return _serialize_doc(doc)

    if "title" in update_fields or "content" in update_fields:
        new_title = update_fields.get("title", doc.get("title", ""))
        new_content = update_fields.get("content", doc.get("content", ""))
        try:
            update_fields["embedding"] = await get_embedding(
                embed_text_for_pill(new_title, new_content)
            )
        except (OSError, ValueError):
            pass

    update_fields["updated_at"] = datetime.utcnow()

    await col.update_one({"_id": oid}, {"$set": update_fields})

    updated = await col.find_one({"_id": oid})
    if updated is None:
        raise HTTPException(status_code=404, detail="Pill not found after update")
    return _serialize_doc(updated)


@app.post("/pills", status_code=201)
async def create_pill(req: CreatePillRequest):
    """Create a new knowledge pill (auto-embeds on creation)."""
    col = await get_collection()

    pill = KnowledgePill(
        title=req.title,
        content=req.content,
        category=req.category,
        tags=req.tags,
        source=PillSource(type=SourceType(req.source_type), reference=req.source_reference),
        confidence=req.confidence,
    )

    try:
        pill.embedding = await get_embedding(embed_text_for_pill(req.title, req.content))
    except (OSError, ValueError):
        pass

    result = await col.insert_one(pill.to_mongo())
    return {"message": "Pill created.", "id": str(result.inserted_id), "title": req.title}


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

    replay = await resolve_idempotency(idem_key, route, body_hash)
    if replay is not None:
        return replay

    from extractor import run_extraction

    result = await run_extraction(
        text=req.text,
        source_reference=req.source_reference or "api:ingest",
        dry_run=False,
        min_confidence=req.min_confidence,
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

    replay = await resolve_idempotency(idem_key, route, body_hash)
    if replay is not None:
        return replay

    from extractor import run_conversation_extraction

    result = await run_conversation_extraction(
        transcript=req.transcript,
        source_reference=req.source_reference or "api:ingest-conversation",
        dry_run=False,
        min_confidence=req.min_confidence,
    )
    await store_idempotent_response(idem_key, route, body_hash, result)
    return result


@app.get("/categories")
async def list_categories():
    """List all distinct categories of active pills."""
    col = await get_collection()
    categories = await col.distinct("category", {"status": "active"})
    return {"categories": sorted(categories)}


@app.get("/topics/snapshot")
async def topics_snapshot(
    top_terms: int = Query(default=20, ge=1, le=100),
    per_category: int = Query(default=10, ge=1, le=50),
    min_doc_freq: int = Query(default=2, ge=1, le=20),
    min_token_len: int = Query(default=3, ge=2, le=20),
):
    """Classical NLP topic overview over active pills (read-only analytics)."""
    return await build_topic_snapshot(
        top_terms=top_terms,
        per_category=per_category,
        min_doc_freq=min_doc_freq,
        min_token_len=min_token_len,
    )


@app.delete("/pills/{pill_id}/consolidation")
async def undo_consolidation(pill_id: str):
    """Revert a janitor consolidation: reactivate archived originals."""
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    doc = await col.find_one({"_id": oid})
    if doc is None:
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
async def delete_pill(pill_id: str):
    """Archive (soft-delete) a pill."""
    col = await get_collection()

    try:
        oid = ObjectId(pill_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ObjectId: {pill_id}") from exc

    result = await col.update_one(
        {"_id": oid},
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
