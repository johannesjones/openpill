"""
Pill-to-pill graph edges: bidirectional linking and neighbor discovery.

Used by extractor (post-insert linking) and API/MCP (neighbor retrieval).
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId

from embeddings import cosine_similarity
from models import PillRelationKind, normalize_relation_kind


def sanitize_relations(relations: list[dict] | None) -> list[dict]:
    """
    Normalize relation ``kind`` strings and drop invalid/duplicate (target_id, kind) pairs.
    First occurrence wins.
    """
    if not relations:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in relations:
        if not isinstance(r, dict):
            continue
        tid = r.get("target_id")
        if not tid:
            continue
        kind = normalize_relation_kind(r.get("kind"))
        key = (str(tid), kind.value)
        if key in seen:
            continue
        seen.add(key)
        out.append({"target_id": str(tid), "kind": kind.value})
    return out


def relation_doc(target_id: str, kind: PillRelationKind) -> dict:
    """MongoDB-safe relation subdocument (stable key order for $addToSet)."""
    return {"target_id": target_id, "kind": kind.value}


async def add_bidirectional_relation(
    col,
    id_a: str | ObjectId,
    id_b: str | ObjectId,
    kind: PillRelationKind = PillRelationKind.RELATED,
) -> None:
    """Add symmetric edges A→B and B→A if not already present."""
    oid_a = id_a if isinstance(id_a, ObjectId) else ObjectId(id_a)
    oid_b = id_b if isinstance(id_b, ObjectId) else ObjectId(id_b)
    sid_a, sid_b = str(oid_a), str(oid_b)
    if oid_a == oid_b:
        return

    ra = relation_doc(sid_b, kind)
    rb = relation_doc(sid_a, kind)

    await col.update_one(
        {"_id": oid_a},
        {
            "$addToSet": {"relations": ra},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    await col.update_one(
        {"_id": oid_b},
        {
            "$addToSet": {"relations": rb},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )


async def find_related_candidates(
    embedding: list[float],
    col,
    *,
    category: str,
    low: float,
    high: float,
    exclude_id: ObjectId | None,
    max_links: int,
) -> list[dict]:
    """
    Pills with same category and cosine similarity in [low, high), excluding exclude_id.
    Sorted by similarity descending, capped at max_links.
    """
    matches: list[dict] = []
    async for doc in col.find(
        {
            "status": "active",
            "category": category,
            "embedding": {"$exists": True, "$ne": None},
        },
        {"title": 1, "embedding": 1},
    ):
        if exclude_id is not None and doc["_id"] == exclude_id:
            continue
        score = cosine_similarity(embedding, doc["embedding"])
        if low <= score < high:
            matches.append(
                {
                    "id": str(doc["_id"]),
                    "title": doc.get("title", ""),
                    "similarity": round(score, 4),
                }
            )
    matches.sort(key=lambda x: x["similarity"], reverse=True)
    return matches[:max_links]


def parse_object_id(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except (InvalidId, TypeError) as e:
        raise ValueError(f"Invalid ObjectId: {s}") from e


async def fetch_pills_by_ids(
    col,
    ids: list[str],
    *,
    projection: dict | None = None,
) -> dict[str, dict]:
    """Load pills by string ObjectIds; returns id -> raw doc."""
    if not ids:
        return {}
    oids = []
    seen: set[str] = set()
    for s in ids:
        if s in seen:
            continue
        seen.add(s)
        try:
            oids.append(ObjectId(s))
        except (InvalidId, TypeError):
            continue
    if not oids:
        return {}
    proj = projection if projection is not None else {"embedding": 0}
    out: dict[str, dict] = {}
    async for doc in col.find({"_id": {"$in": oids}}, proj):
        out[str(doc["_id"])] = doc
    return out


async def neighbors_for_pill(col, pill_id: str) -> tuple[dict | None, list[dict], list[dict]]:
    """
    Returns (center_doc_or_none, outgoing_serialized, incoming_serialized).
    Caller serializes docs; we return raw docs with embedding stripped for lists.
    """
    oid = parse_object_id(pill_id)
    center = await col.find_one({"_id": oid})
    if center is None:
        return None, [], []

    rels = center.get("relations") or []
    target_ids = [r.get("target_id") for r in rels if r.get("target_id")]
    targets = await fetch_pills_by_ids(col, target_ids, projection={"embedding": 0})

    outgoing: list[dict] = []
    for r in rels:
        tid = r.get("target_id")
        if not tid or tid not in targets:
            continue
        d = targets[tid].copy()
        d["_id"] = str(d["_id"])
        d["edge_kind"] = r.get("kind", "related")
        outgoing.append(d)

    incoming_raw: list[dict] = []
    async for doc in col.find(
        {"relations.target_id": pill_id, "status": "active"},
        {"embedding": 0},
    ):
        incoming_raw.append(doc)

    incoming: list[dict] = []
    for doc in incoming_raw:
        d = doc.copy()
        d["_id"] = str(d["_id"])
        for r in d.get("relations") or []:
            if r.get("target_id") == pill_id:
                d["edge_kind"] = r.get("kind", "related")
                break
        else:
            d["edge_kind"] = "related"
        incoming.append(d)

    return center, outgoing, incoming


def serialize_pill_doc(doc: dict) -> dict:
    """Strip embedding and normalize _id / datetimes for JSON."""
    d = doc.copy()
    d["_id"] = str(d["_id"])
    for key in ("created_at", "updated_at", "expires_at"):
        if isinstance(d.get(key), datetime):
            d[key] = d[key].isoformat()
    d.pop("embedding", None)
    return d


async def expand_semantic_neighbors(
    col,
    seed_results: list[dict],
    neighbor_limit: int,
) -> list[dict]:
    """Backward-compatible 1-hop expansion wrapper."""
    return await expand_semantic_neighbors_hops(
        col,
        seed_results,
        neighbor_limit=neighbor_limit,
        max_hops=1,
        max_nodes=max(len(seed_results) + neighbor_limit, 1),
        hop_decay=0.12,
    )


async def expand_semantic_neighbors_hops(
    col,
    seed_results: list[dict],
    *,
    neighbor_limit: int,
    max_hops: int = 1,
    max_nodes: int = 30,
    hop_decay: float = 0.12,
) -> list[dict]:
    """Expand semantic hits through graph neighbors with optional multi-hop traversal.

    Guardrails:
    - neighbor_limit: cap total added neighbors.
    - max_hops: traversal depth (1 or 2 recommended).
    - max_nodes: hard cap on total returned rows.
    - hop_decay: per-hop penalty applied to inherited similarity.
    """
    if neighbor_limit <= 0 or not seed_results:
        return seed_results
    max_hops = min(max(max_hops, 1), 2)
    max_nodes = max(max_nodes, len(seed_results))
    seen = {r["_id"] for r in seed_results}
    extras: list[dict] = []
    queue: list[tuple[dict, int, str]] = [(seed, 1, seed["_id"]) for seed in seed_results]

    while queue and len(extras) < neighbor_limit and (len(seed_results) + len(extras)) < max_nodes:
        seed, hop, root_id = queue.pop(0)
        if hop > max_hops:
            continue
        try:
            oid = ObjectId(seed["_id"])
        except (InvalidId, TypeError):
            continue
        # Use inclusion-only projection to avoid Mongo projection mix errors.
        doc = await col.find_one({"_id": oid}, {"relations": 1})
        if not doc or not doc.get("relations"):
            continue
        for rel in doc["relations"]:
            if len(extras) >= neighbor_limit or (len(seed_results) + len(extras)) >= max_nodes:
                break
            tid = rel.get("target_id")
            if not tid or tid in seen:
                continue
            try:
                noid = ObjectId(tid)
            except (InvalidId, TypeError):
                continue
            neighbor = await col.find_one({"_id": noid, "status": "active"}, {"embedding": 0})
            if neighbor:
                ser = serialize_pill_doc(neighbor.copy())
                ser["via_pill_id"] = seed["_id"]
                ser["via_root_pill_id"] = root_id
                ser["hop"] = hop
                inherited = float(seed.get("similarity", 0.0))
                ser["similarity"] = round(max(0.0, inherited - hop_decay), 4)
                extras.append(ser)
                seen.add(tid)
                if hop < max_hops:
                    queue.append((ser, hop + 1, root_id))
    return seed_results + extras


async def rewire_relations_on_merge(
    col,
    original_ids: list[str],
    new_id: str,
) -> None:
    """
    After janitor/watchdog merges originals into one pill:
    - Union outgoing relations from archived originals onto the merged pill (remap targets).
    - Rewire any active pill whose edge targeted an original to target new_id instead.
    - Clear relations on archived original documents.
    """
    if not original_ids or not new_id:
        return
    orig_set = set(original_ids)
    if new_id in orig_set:
        return
    now = datetime.now(timezone.utc)
    oid_new = ObjectId(new_id)
    oids_orig = []
    for s in original_ids:
        try:
            oids_orig.append(ObjectId(s))
        except (InvalidId, TypeError):
            continue
    if not oids_orig:
        return

    # 1) Build merged relations from originals' outgoing edges
    merged: list[dict] = []
    seen_target: set[str] = set()

    def _add_edge(tid: str, kind_raw: str | None) -> None:
        if not tid or tid == new_id:
            return
        if tid in orig_set:
            tid = new_id
        if tid == new_id:
            return
        if tid in seen_target:
            return
        seen_target.add(tid)
        kind = normalize_relation_kind(kind_raw).value
        merged.append({"target_id": tid, "kind": kind})

    for oid in oids_orig:
        doc = await col.find_one({"_id": oid}, {"relations": 1})
        if not doc:
            continue
        for r in doc.get("relations") or []:
            tid = r.get("target_id")
            if not tid:
                continue
            if tid in orig_set:
                tid = new_id
            _add_edge(tid, r.get("kind"))

    merged = sanitize_relations(merged)

    await col.update_one(
        {"_id": oid_new},
        {"$set": {"relations": merged, "updated_at": now}},
    )

    # 2) Rewire incoming: any active pill (except merged) pointing at an original -> new_id
    async for doc in col.find(
        {
            "status": "active",
            "_id": {"$nin": [oid_new]},
            "relations.target_id": {"$in": list(orig_set)},
        },
        {"_id": 1, "relations": 1},
    ):
        rels = doc.get("relations") or []
        new_rels: list[dict] = []
        seen_t: set[str] = set()
        sid = str(doc["_id"])
        for r in rels:
            tid = r.get("target_id")
            if not tid:
                continue
            if tid in orig_set:
                tid = new_id
            if tid == sid:
                continue
            if tid in seen_t:
                continue
            seen_t.add(tid)
            kind = normalize_relation_kind(r.get("kind")).value
            new_rels.append({"target_id": tid, "kind": kind})
        new_rels = sanitize_relations(new_rels)
        await col.update_one(
            {"_id": doc["_id"]},
            {"$set": {"relations": new_rels, "updated_at": now}},
        )

    # 3) Clear relations on archived originals
    await col.update_many(
        {"_id": {"$in": oids_orig}},
        {"$set": {"relations": [], "updated_at": now}},
    )
