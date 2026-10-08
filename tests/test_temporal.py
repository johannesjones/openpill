"""Temporal validity (valid_at / invalid_at) and version history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

import api as api_module
import server as server_module
from extractor import find_near_duplicates
from models import HISTORY_LIMIT
from tests.fakes import FakeCollection

NOW = datetime.now(timezone.utc)


def _doc(title, *, embedding=(1.0, 0.0), **extra):
    return {
        "_id": ObjectId(),
        "title": title,
        "content": f"{title} body",
        "category": "team",
        "status": "active",
        "confidence": 0.9,
        "embedding": list(embedding),
        "created_at": NOW,
        "updated_at": NOW,
        "relations": [],
        **extra,
    }


@pytest.fixture
def rest(monkeypatch):
    col = FakeCollection()

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)
    monkeypatch.setattr(api_module, "get_embedding", AsyncMock(return_value=[1.0, 0.0]))
    monkeypatch.setattr(api_module, "AUTH_ENABLED", False)
    monkeypatch.delenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", raising=False)
    return TestClient(api_module.app), col


def test_invalidated_pills_are_hidden_unless_requested(rest):
    client, col = rest
    col.docs = [
        _doc("Standup at 10:00", invalid_at=NOW - timedelta(days=1)),
        _doc("Standup at 9:30"),
        _doc("Scheduled to expire", invalid_at=NOW + timedelta(days=5)),
    ]

    def titles(path, **params):
        return [p["title"] for p in client.get(path, params=params).json()["pills"]]

    assert set(titles("/pills/semantic", q="standup")) == {"Standup at 9:30", "Scheduled to expire"}
    assert set(titles("/pills/search")) == {"Standup at 9:30", "Scheduled to expire"}

    every = client.get("/pills/semantic", params={"q": "standup", "include_invalid": "true"}).json()
    assert every["pills"][-1]["title"] == "Standup at 10:00"
    assert every["pills"][-1]["is_invalid"] is True
    assert "stopped being valid" in every["pills"][-1]["consistency_warning"]
    assert len(titles("/pills/search", include_invalid="true")) == 3


def test_patch_invalidates_and_revalidates(rest):
    client, col = rest
    col.docs = [_doc("Old fact")]
    pill_id = str(col.docs[0]["_id"])

    when = (NOW - timedelta(hours=1)).isoformat()
    assert client.patch(f"/pills/{pill_id}", json={"invalid_at": when}).status_code == 200
    assert client.get(f"/pills/{pill_id}").json()["is_invalid"] is True
    assert client.get("/pills/semantic", params={"q": "x"}).json()["count"] == 0

    client.patch(f"/pills/{pill_id}", json={"invalid_at": None})
    assert client.get(f"/pills/{pill_id}").json()["is_invalid"] is False
    assert client.get("/pills/semantic", params={"q": "x"}).json()["count"] == 1


def test_rewrites_keep_capped_history(rest):
    client, col = rest
    col.docs = [_doc("v0")]
    pill_id = str(col.docs[0]["_id"])

    client.patch(f"/pills/{pill_id}", json={"tags": ["no-text-change"]})
    assert "history" not in col.docs[0] or col.docs[0]["history"] == []

    for i in range(1, HISTORY_LIMIT + 3):
        client.patch(f"/pills/{pill_id}", json={"content": f"version {i}"})
    history = client.get(f"/pills/{pill_id}").json()["history"]
    assert len(history) == HISTORY_LIMIT
    assert history[-1]["content"] == f"version {HISTORY_LIMIT + 1}"
    assert history[-1]["reason"] == "patch"

    listed = client.get("/pills/semantic", params={"q": "x"}).json()["pills"][0]
    assert "history" not in listed
    assert "history" not in client.get("/pills/search").json()["pills"][0]


def test_freshness_follows_valid_at(rest):
    client, col = rest
    col.docs = [_doc("Old fact, edited today", valid_at=NOW - timedelta(days=60))]
    pill = client.get("/pills/semantic", params={"q": "x"}).json()["pills"][0]
    assert pill["freshness_score"] == 0.0


@pytest.mark.asyncio
async def test_mcp_update_pill_can_invalidate(monkeypatch):
    col = FakeCollection()
    col.docs = [_doc("Old fact"), _doc("Other fact")]

    async def fake_col():
        return col

    monkeypatch.setattr(server_module, "get_collection", fake_col)
    monkeypatch.setattr(server_module, "get_embedding", AsyncMock(return_value=[1.0, 0.0]))
    monkeypatch.delenv("OPENPILL_MCP_NAMESPACE", raising=False)
    monkeypatch.delenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", raising=False)

    pill_id = str(col.docs[0]["_id"])
    out = json.loads(await server_module.update_pill(pill_id, invalid_at="now"))
    assert out["invalid_at"]
    hits = json.loads(await server_module.semantic_search("x"))
    assert [p["title"] for p in hits["pills"]] == ["Other fact"]
    assert "error" in json.loads(await server_module.update_pill(pill_id, invalid_at="soon"))
    json.loads(await server_module.update_pill(pill_id, invalid_at=""))
    assert json.loads(await server_module.semantic_search("x"))["count"] == 2


@pytest.mark.asyncio
async def test_dedup_ignores_invalidated_pills():
    col = FakeCollection()
    col.docs = [_doc("old", invalid_at=NOW - timedelta(days=1)), _doc("current")]
    dupes = await find_near_duplicates([1.0, 0.0], col, threshold=0.9)
    assert [d["title"] for d in dupes] == ["current"]
