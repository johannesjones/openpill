from __future__ import annotations

from bson import ObjectId
from fastapi.testclient import TestClient

import api as api_module
from models import PillRelationKind
from pill_relations import list_active_conflict_pairs


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._docs):
            raise StopAsyncIteration
        item = self._docs[self._idx]
        self._idx += 1
        return item


def _elem_match_conflicts(filter_doc: dict, doc: dict) -> bool:
    if doc.get("status") != filter_doc.get("status"):
        return False
    em = filter_doc.get("relations", {}).get("$elemMatch", {})
    if em.get("kind") != PillRelationKind.CONFLICTS_WITH.value:
        return False
    for r in doc.get("relations") or []:
        if r.get("kind") != PillRelationKind.CONFLICTS_WITH.value:
            continue
        tid = r.get("target_id")
        if tid in (None, ""):
            continue
        return True
    return False


class _ConflictsFakeCol:
    """Minimal Motor-like collection for conflict-pair listing tests."""

    def __init__(self, docs: list[dict]):
        self._docs = docs

    def find(self, filter_doc: dict | None = None, _projection=None):
        if filter_doc is None:
            return _AsyncCursor(self._docs)
        if "_id" in filter_doc and "$in" in filter_doc.get("_id", {}):
            want = set(filter_doc["_id"]["$in"])
            out = [
                d
                for d in self._docs
                if d["_id"] in want and d.get("status") == filter_doc.get("status")
            ]
            return _AsyncCursor(out)
        out = [d for d in self._docs if _elem_match_conflicts(filter_doc, d)]
        return _AsyncCursor(out)


async def test_list_active_conflict_pairs_dedupes_and_requires_active_target():
    oid_a = ObjectId()
    oid_b = ObjectId()
    oid_c = ObjectId()
    sa, sb = str(oid_a), str(oid_b)
    docs = [
        {
            "_id": oid_a,
            "title": "Claim A",
            "status": "active",
            "relations": [{"target_id": sb, "kind": "conflicts_with"}],
        },
        {
            "_id": oid_b,
            "title": "Claim B",
            "status": "active",
            "relations": [{"target_id": sa, "kind": "conflicts_with"}],
        },
        {
            "_id": oid_c,
            "title": "Stale",
            "status": "archived",
            "relations": [{"target_id": sa, "kind": "conflicts_with"}],
        },
    ]
    col = _ConflictsFakeCol(docs)
    pairs, total = await list_active_conflict_pairs(col, limit=10)
    assert total == 1
    assert len(pairs) == 1
    p = pairs[0]
    assert {p["pill_id_a"], p["pill_id_b"]} == {sa, sb}
    assert p["title_a"] in ("Claim A", "Claim B")
    assert p["title_b"] in ("Claim A", "Claim B")


async def test_list_active_conflict_pairs_respects_limit():
    oid_a = ObjectId()
    oid_b = ObjectId()
    oid_x = ObjectId()
    oid_y = ObjectId()
    docs = [
        {
            "_id": oid_a,
            "title": "A",
            "status": "active",
            "relations": [{"target_id": sb, "kind": "conflicts_with"}],
        },
        {
            "_id": oid_b,
            "title": "B",
            "status": "active",
            "relations": [],
        },
        {
            "_id": oid_x,
            "title": "X",
            "status": "active",
            "relations": [{"target_id": sy, "kind": "conflicts_with"}],
        },
        {
            "_id": oid_y,
            "title": "Y",
            "status": "active",
            "relations": [],
        },
    ]
    col = _ConflictsFakeCol(docs)
    pairs, total = await list_active_conflict_pairs(col, limit=1)
    assert total == 2
    assert len(pairs) == 1


def test_get_pills_conflicts_endpoint(monkeypatch):
    oid_a = ObjectId()
    oid_b = ObjectId()
    sa, sb = str(oid_a), str(oid_b)
    docs = [
        {
            "_id": oid_a,
            "title": "Left",
            "status": "active",
            "relations": [{"target_id": sb, "kind": "conflicts_with"}],
        },
        {
            "_id": oid_b,
            "title": "Right",
            "status": "active",
            "relations": [],
        },
    ]
    col = _ConflictsFakeCol(docs)

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)
    client = TestClient(api_module.app)
    r = client.get("/pills/conflicts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["truncated"] is False
    assert len(body["pairs"]) == 1
    assert body["pairs"][0]["pill_id_a"] == min(sa, sb)
    assert body["pairs"][0]["pill_id_b"] == max(sa, sb)
