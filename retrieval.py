"""
Semantic retrieval shared by the REST API (``GET /pills/semantic``) and the MCP
``semantic_search`` tool, so ranking changes happen in one place.

Pipeline: vector candidates (brute-force cosine over active pills with an
embedding) → optional similarity floor → top-k by similarity → relevance-led
``retrieval_score`` with superseded/conflict penalties → optional lexical
fusion → optional graph expansion → order by ``retrieval_score``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from embeddings import cosine_similarity
from namespaces import prefix_filter
from temporal import fact_time, is_invalid, now_utc, validity_filter
from pill_relations import expand_semantic_neighbors_hops, serialize_pill_doc

HYBRID_RETRIEVAL_ENABLED = os.getenv("HYBRID_RETRIEVAL_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
HYBRID_VECTOR_WEIGHT = float(os.getenv("HYBRID_VECTOR_WEIGHT", "0.7"))
HYBRID_LEXICAL_WEIGHT = float(os.getenv("HYBRID_LEXICAL_WEIGHT", "0.3"))
HYBRID_LEXICAL_LIMIT = int(os.getenv("HYBRID_LEXICAL_LIMIT", "30"))
HYBRID_LEXICAL_FALLBACK_MIN_VECTOR = int(
    os.getenv("HYBRID_LEXICAL_FALLBACK_MIN_VECTOR", "3")
)


# Search ranking (Park et al. 2023, "Generative Agents": relevance, importance and
# recency, with relevance min-max normalized over the candidate set). Relevance
# dominates so a fresher but unrelated pill cannot outrank the best match.
RELEVANCE_WEIGHT = 0.6
CONFIDENCE_WEIGHT = 0.2
FRESHNESS_WEIGHT = 0.2


def default_min_similarity() -> float | None:
    """``OPENPILL_SEMANTIC_MIN_SIMILARITY``; unset or empty means no floor."""
    raw = os.getenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", "").strip()
    return float(raw) if raw else None


# ---------------------------------------------------------------------------
# Consistency metadata
# ---------------------------------------------------------------------------


def count_conflict_relations(doc: dict) -> int:
    rels = doc.get("relations") or []
    return sum(1 for r in rels if r.get("kind") == "conflicts_with")


def freshness_score(dt: datetime | str | None) -> float:
    """Recency score in [0,1], linear decay over 30 days.

    Accepts an ISO string (serialized docs) as well as a datetime.
    """
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return 0.5
    if not isinstance(dt, datetime):
        return 0.5
    now = datetime.now(timezone.utc)
    ref = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    age_days = max((now - ref).total_seconds() / 86400.0, 0.0)
    return round(max(0.0, 1.0 - min(age_days / 30.0, 1.0)), 4)


def attach_consistency_metadata(
    payload: dict,
    *,
    confidence: float,
    freshness: float,
    conflict_count: int,
    is_superseded: bool = False,
    relevance: float | None = None,
) -> dict:
    """Attach retrieval-time consistency hints used by clients/agents.

    With ``relevance`` (search results, in [0,1]) the score is relevance-led;
    without it (single-pill reads) it reflects confidence and freshness only.
    """
    if relevance is not None:
        retrieval_score = (
            RELEVANCE_WEIGHT * relevance
            + CONFIDENCE_WEIGHT * confidence
            + FRESHNESS_WEIGHT * freshness
        )
        payload["relevance_score"] = round(relevance, 4)
    else:
        retrieval_score = 0.6 * confidence + 0.25 * freshness
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


def _score_row(row: dict, *, is_superseded: bool, relevance: float) -> dict:
    return attach_consistency_metadata(
        row,
        confidence=float(row.get("confidence", 1.0)),
        freshness=freshness_score(fact_time(row)),
        conflict_count=count_conflict_relations(row),
        is_superseded=is_superseded,
        relevance=relevance,
    )


def _mark_invalid(row: dict) -> None:
    row["is_invalid"] = True
    note = f"This memory stopped being valid at {row.get('invalid_at')}; treat as history."
    previous = row.get("consistency_warning")
    row["consistency_warning"] = f"{previous} {note}" if previous else note


def _relevance_scale(similarities: list[float]):
    """Min-max normalize similarity over the scanned pills; clamp to [0,1]."""
    lo = min(similarities, default=0.0)
    hi = max(similarities, default=0.0)

    def scale(sim: float) -> float:
        if hi <= lo:
            return 1.0 if sim >= hi and similarities else 0.0
        return max(0.0, min(1.0, (sim - lo) / (hi - lo)))

    return scale


def _place_superseded_after_successors(
    rows: list[dict], superseded_by: dict[str, set[str]]
) -> list[dict]:
    """Keep score order, but never rank a superseded pill above a pill that
    supersedes it: move it to just after its last-ranked successor in ``rows``."""
    ordered = list(rows)
    for row in rows:
        successors = superseded_by.get(row["_id"], set())
        if not successors:
            continue
        ids = [r["_id"] for r in ordered]
        last = max((ids.index(sid) for sid in successors if sid in ids), default=-1)
        here = ids.index(row["_id"])
        if last > here:
            ordered.pop(here)
            ordered.insert(last, row)  # `last` shifted down by one after the pop
    return ordered


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def semantic_retrieve(
    col,
    query: str,
    query_embedding: list[float],
    *,
    category: str | None = None,
    limit: int = 10,
    expand_neighbors: bool = False,
    neighbor_limit: int = 10,
    max_hops: int = 1,
    max_nodes: int = 30,
    hybrid: bool = False,
    min_similarity: float | None = None,
    namespace: list[str] | None = None,
    include_invalid: bool = False,
) -> dict:
    """Run semantic retrieval and return ``{count, pills, retrieval_metrics}``.

    ``namespace`` restricts hits, keyword matches and graph neighbors to that prefix.
    Invalidated pills (``invalid_at`` passed) are hidden unless ``include_invalid``;
    then they are flagged ``is_invalid`` and ranked after every valid pill.
    """
    if min_similarity is None:
        min_similarity = default_min_similarity()
    now = now_utc()
    validity = {} if include_invalid else validity_filter(now)

    filter_doc: dict = {
        "status": "active",
        "embedding": {"$exists": True, "$ne": None},
        **prefix_filter(namespace),
        **validity,
    }
    if category:
        filter_doc["category"] = category

    candidates: list[dict] = []
    superseded_by: dict[str, set[str]] = {}
    scanned_similarities: list[float] = []
    async for doc in col.find(filter_doc):
        # Supersedes edges count even when the superseding pill is not a hit.
        for rel in doc.get("relations") or []:
            if rel.get("kind") == "supersedes" and rel.get("target_id"):
                superseded_by.setdefault(rel["target_id"], set()).add(str(doc["_id"]))
        score = cosine_similarity(query_embedding, doc["embedding"])
        scanned_similarities.append(score)
        if min_similarity is not None and score < min_similarity:
            continue
        row = serialize_pill_doc(doc)
        row["similarity"] = round(score, 4)
        candidates.append(row)

    candidates.sort(key=lambda d: d["similarity"], reverse=True)
    results = candidates[:limit]
    # Normalize over everything scanned, not just the hits: close matches stay
    # close (so freshness and supersedes can order them), unrelated pills stay low.
    relevance = _relevance_scale(scanned_similarities)
    for row in results:
        _score_row(
            row,
            is_superseded=row["_id"] in superseded_by,
            relevance=relevance(row["similarity"]),
        )

    lexical_candidates: list[dict] = []
    fusion_enabled = hybrid or HYBRID_RETRIEVAL_ENABLED
    lexical_fallback_used = fusion_enabled and (
        len(candidates) < HYBRID_LEXICAL_FALLBACK_MIN_VECTOR
    )
    if lexical_fallback_used:
        lexical_filter: dict = {
            "status": "active",
            "$text": {"$search": query},
            **prefix_filter(namespace),
            **validity,
        }
        if category:
            lexical_filter["category"] = category
        cursor = (
            col.find(lexical_filter, {"embedding": 0, "score": {"$meta": "textScore"}})
            .sort([("score", {"$meta": "textScore"})])
            .limit(max(limit * 2, HYBRID_LEXICAL_LIMIT))
        )
        async for doc in cursor:
            row = serialize_pill_doc(doc)
            row["lexical_score"] = round(float(doc.get("score", 0.0)), 4)
            lexical_candidates.append(row)

    if lexical_fallback_used and lexical_candidates:
        merged: dict[str, dict] = {r["_id"]: r for r in results}
        max_lex = max(
            (float(r.get("lexical_score", 0.0)) for r in lexical_candidates),
            default=1.0,
        ) or 1.0
        for row in lexical_candidates:
            rid = row["_id"]
            if rid not in merged:
                row["similarity"] = 0.0
                _score_row(row, is_superseded=rid in superseded_by, relevance=0.0)
                merged[rid] = row
            lex_norm = float(row.get("lexical_score", 0.0)) / max_lex
            vec = float(merged[rid].get("similarity", 0.0))
            hybrid_score = HYBRID_VECTOR_WEIGHT * vec + HYBRID_LEXICAL_WEIGHT * lex_norm
            merged[rid]["hybrid_score"] = round(hybrid_score, 4)
            merged[rid]["retrieval_score"] = round(
                max(
                    0.0,
                    min(1.0, 0.7 * float(merged[rid]["retrieval_score"]) + 0.3 * hybrid_score),
                ),
                4,
            )
        results = list(merged.values())

    if expand_neighbors and neighbor_limit > 0:
        results = await expand_semantic_neighbors_hops(
            col,
            results,
            neighbor_limit=neighbor_limit,
            max_hops=max_hops,
            max_nodes=max_nodes,
            namespace=namespace,
        )
        for row in results:
            # Direct hits already carry their score; only score added neighbors.
            if "retrieval_score" not in row:
                _score_row(
                    row,
                    is_superseded=row["_id"] in superseded_by,
                    relevance=relevance(float(row.get("similarity", 0.0))),
                )

    if include_invalid:
        for row in results:
            if is_invalid(row, now):
                _mark_invalid(row)
    else:
        # Graph expansion can reach invalidated neighbors; drop them.
        results = [row for row in results if not is_invalid(row, now)]

    results.sort(
        key=lambda d: (not d.get("is_invalid", False), d.get("retrieval_score", 0.0)),
        reverse=True,
    )
    final = _place_superseded_after_successors(results, superseded_by)[:max_nodes]
    return {
        "count": len(final),
        "pills": final,
        "retrieval_metrics": {
            "hybrid_enabled": fusion_enabled,
            "lexical_fallback_used": lexical_fallback_used,
            "vector_candidates": len(candidates),
            "lexical_candidates": len(lexical_candidates),
            "min_similarity": min_similarity,
            "below_min_similarity": len(scanned_similarities) - len(candidates),
        },
    }
