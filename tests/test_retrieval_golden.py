"""
Golden retrieval regression: deterministic one-hot embeddings + mocked get_embedding.

Loads ``tests/fixtures/retrieval_golden.json``. Run via ``make retrieval-golden`` or ``pytest tests/``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from fastapi.testclient import TestClient

import api as api_module

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "retrieval_golden.json"


def _one_hot(dim: int, axis: int) -> list[float]:
    return [1.0 if i == axis else 0.0 for i in range(dim)]


class _AsyncCursor:
    def __init__(self, docs: list[dict]):
        self._docs = list(docs)
        self._idx = 0

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._docs):
            raise StopAsyncIteration
        item = self._docs[self._idx]
        self._idx += 1
        return item


def _matches_vector_find(filter_doc: dict, doc: dict) -> bool:
    if filter_doc.get("status") and doc.get("status") != filter_doc["status"]:
        return False
    cat = filter_doc.get("category")
    if cat and doc.get("category") != cat:
        return False
    emb = filter_doc.get("embedding") or {}
    if emb.get("$exists") and doc.get("embedding") is None:
        return False
    if "$ne" in emb and doc.get("embedding") == emb.get("$ne"):
        return False
    return True


def _project(doc: dict, projection: dict | None) -> dict:
    if projection is None:
        return dict(doc)
    if projection.get("relations") == 1:
        return {"_id": doc["_id"], "relations": doc.get("relations", [])}
    if projection.get("embedding") == 0:
        return {k: v for k, v in doc.items() if k != "embedding"}
    out = {"_id": doc["_id"]}
    for k, v in projection.items():
        if v == 1 and k in doc and k != "_id":
            out[k] = doc[k]
    return out


class _GoldenFakeCol:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def find(self, filter_doc: dict | None = None, projection: dict | None = None):
        if filter_doc and "$text" in filter_doc:
            return _AsyncCursor([])
        if filter_doc and "_id" in filter_doc and "$in" in filter_doc["_id"]:
            want = set(filter_doc["_id"]["$in"])
            rows = [d for d in self._docs if d["_id"] in want]
            if filter_doc.get("status"):
                rows = [d for d in rows if d.get("status") == filter_doc["status"]]
            projected = [_project(d, projection) for d in rows]
            return _AsyncCursor(projected)
        rows = [d for d in self._docs if filter_doc is None or _matches_vector_find(filter_doc, d)]
        if projection:
            rows = [_project(d, projection) for d in rows]
        else:
            rows = [dict(d) for d in rows]
        return _AsyncCursor(rows)

    async def find_one(self, query: dict, projection: dict | None = None):
        oid = query.get("_id")
        st = query.get("status")
        for d in self._docs:
            if oid is not None and d["_id"] != oid:
                continue
            if st is not None and d.get("status") != st:
                continue
            return _project(d, projection) if projection else dict(d)
        return None


def _build_corpus(spec: dict) -> tuple[list[dict], int]:
    pills_spec = spec["pills"]
    dim = len(pills_spec)
    for i, p in enumerate(pills_spec):
        assert int(p["axis"]) == i, f"pill axis must equal index {i}, got {p.get('axis')}"
    base = 0x507F1F77BCF86CD799439000
    oids = [ObjectId("%024x" % (base + j)) for j in range(dim)]
    now = datetime.now(timezone.utc)
    docs: list[dict] = []
    for i, p in enumerate(pills_spec):
        rels_out: list[dict] = []
        for r in p.get("relations") or []:
            tid = r.get("target_axis")
            kind = r.get("kind", "related")
            if tid is None:
                continue
            rels_out.append({"target_id": str(oids[int(tid)]), "kind": kind})
        docs.append(
            {
                "_id": oids[i],
                "title": p["title"],
                "content": p["content"],
                "category": p["category"],
                "status": "active",
                "confidence": 0.9,
                "created_at": now,
                "updated_at": now,
                "tags": [],
                "relations": rels_out,
                "embedding": _one_hot(dim, i),
            }
        )
    return docs, dim


def _load_spec() -> dict:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert data.get("version") == 1
    return data


def test_retrieval_golden_fixture(monkeypatch):
    spec = _load_spec()
    docs, dim = _build_corpus(spec)
    col = _GoldenFakeCol(docs)

    async def fake_get_collection():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_get_collection)
    monkeypatch.setattr(api_module, "HYBRID_RETRIEVAL_ENABLED", False)

    client = TestClient(api_module.app)

    for case in spec["cases"]:
        axis = int(case["query_axis"])
        assert 0 <= axis < dim

        async def mock_embed(q: str, ax=axis, d=dim):
            return _one_hot(d, ax)

        monkeypatch.setattr(api_module, "get_embedding", mock_embed)

        params: dict[str, str] = {
            "q": case["query"],
            "limit": str(case.get("limit", 10)),
            "hybrid": "false",
        }
        if case.get("category"):
            params["category"] = case["category"]
        if case.get("expand_neighbors"):
            params["expand_neighbors"] = "true"
            params["neighbor_limit"] = str(case.get("neighbor_limit", 8))
            params["max_hops"] = str(case.get("max_hops", 1))

        r = client.get("/pills/semantic", params=params)
        assert r.status_code == 200, f"{case['id']}: {r.text}"
        body = r.json()
        pills = body.get("pills", [])
        exp = case["expect"]
        needle = exp["title_contains"]
        max_rank = int(exp["max_rank"])
        want_super = exp.get("is_superseded")

        found_rank: int | None = None
        found_pill: dict | None = None
        for idx, pill in enumerate(pills, start=1):
            title = pill.get("title") or ""
            if needle.lower() in title.lower():
                found_rank = idx
                found_pill = pill
                break

        assert found_rank is not None, (
            f"{case['id']}: no title containing {needle!r} in {len(pills)} results"
        )
        assert found_rank <= max_rank, (
            f"{case['id']}: {needle!r} at rank {found_rank}, want <= {max_rank}"
        )
        if want_super is not None:
            assert found_pill is not None
            assert found_pill.get("is_superseded") is want_super, (
                f"{case['id']}: is_superseded={found_pill.get('is_superseded')!r}, "
                f"expected {want_super!r}"
            )
