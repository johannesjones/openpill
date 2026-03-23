from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from bson import ObjectId
from fastapi.testclient import TestClient

import api as api_module
from models import PillRelationKind


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


class _FakeCol:
    def __init__(self, docs):
        self._docs = docs

    def find(self, *_args, **_kwargs):
        return _AsyncCursor(self._docs)

    async def find_one(self, query, *_args, **_kwargs):
        if "relations" in query and isinstance(query["relations"], dict):
            elem = query["relations"].get("$elemMatch", {})
            target_id = elem.get("target_id")
            kind = elem.get("kind")
            for d in self._docs:
                if query.get("status") and d.get("status") != query["status"]:
                    continue
                for r in d.get("relations") or []:
                    if r.get("target_id") == target_id and r.get("kind") == kind:
                        return d
            return None
        oid = query.get("_id")
        for d in self._docs:
            if d["_id"] == oid:
                return d
        return None


def test_conflicts_relation_kind_available():
    assert PillRelationKind.CONFLICTS_WITH.value == "conflicts_with"


def test_semantic_search_returns_consistency_metadata(monkeypatch):
    doc = {
        "_id": ObjectId(),
        "title": "Conflicting claim",
        "content": "A",
        "category": "memory",
        "status": "active",
        "embedding": [0.2, 0.4],
        "confidence": 0.8,
        "updated_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "relations": [{"target_id": str(ObjectId()), "kind": "conflicts_with"}],
    }
    col = _FakeCol([doc])

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)
    monkeypatch.setattr(api_module, "get_embedding", AsyncMock(return_value=[0.2, 0.4]))

    client = TestClient(api_module.app)
    r = client.get("/pills/semantic", params={"q": "conflict"})
    assert r.status_code == 200
    pill = r.json()["pills"][0]
    assert "retrieval_score" in pill
    assert pill["conflict_count"] == 1
    assert "consistency_warning" in pill


def test_get_pill_returns_consistency_metadata(monkeypatch):
    oid = ObjectId()
    doc = {
        "_id": oid,
        "title": "Stable claim",
        "content": "B",
        "category": "memory",
        "status": "active",
        "confidence": 0.95,
        "updated_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "relations": [],
    }
    col = _FakeCol([doc])

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)

    client = TestClient(api_module.app)
    r = client.get(f"/pills/{oid}")
    assert r.status_code == 200
    body = r.json()
    assert "confidence_score" in body
    assert "freshness_score" in body
    assert body["conflict_count"] == 0


def test_semantic_search_marks_superseded_pills(monkeypatch):
    oid_old = ObjectId()
    oid_new = ObjectId()
    old_doc = {
        "_id": oid_old,
        "title": "Old policy",
        "content": "Use naive keyword retrieval only.",
        "category": "memory",
        "status": "active",
        "embedding": [0.2, 0.4],
        "confidence": 0.9,
        "updated_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "relations": [],
    }
    new_doc = {
        "_id": oid_new,
        "title": "New policy",
        "content": "Prefer semantic retrieval with graph expansion.",
        "category": "memory",
        "status": "active",
        "embedding": [0.2, 0.4],
        "confidence": 0.95,
        "updated_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "relations": [{"target_id": str(oid_old), "kind": "supersedes"}],
    }
    col = _FakeCol([old_doc, new_doc])

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)
    monkeypatch.setattr(api_module, "get_embedding", AsyncMock(return_value=[0.2, 0.4]))

    client = TestClient(api_module.app)
    r = client.get("/pills/semantic", params={"q": "retrieval policy", "limit": 10})
    assert r.status_code == 200
    pills = {p["_id"]: p for p in r.json()["pills"]}
    assert pills[str(oid_old)]["is_superseded"] is True
    assert "consistency_warning" in pills[str(oid_old)]
