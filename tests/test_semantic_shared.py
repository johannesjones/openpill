"""REST and MCP semantic search share retrieval.semantic_retrieve."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

import api as api_module
import server as server_module
from tests.fakes import FakeCollection


def _pill(title: str, embedding: list[float], **extra) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "title": title,
        "content": "Body",
        "category": "memory",
        "status": "active",
        "embedding": embedding,
        "confidence": 0.9,
        "created_at": now,
        "updated_at": now,
        "relations": [],
        **extra,
    }


def _corpus() -> FakeCollection:
    col = FakeCollection()
    col.docs = [
        _pill("Close match", [1.0, 0.0]),
        _pill("Partial match", [0.6, 0.8]),
        _pill("Unrelated", [0.0, 1.0]),
    ]
    return col


def _patch(monkeypatch, module, col):
    async def fake_col():
        return col

    monkeypatch.setattr(module, "get_collection", fake_col)
    monkeypatch.setattr(module, "get_embedding", AsyncMock(return_value=[1.0, 0.0]))


def test_rest_min_similarity_drops_weak_hits(monkeypatch):
    monkeypatch.delenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", raising=False)
    _patch(monkeypatch, api_module, _corpus())
    client = TestClient(api_module.app)

    everything = client.get("/pills/semantic", params={"q": "x"}).json()
    assert everything["count"] == 3
    assert everything["retrieval_metrics"]["min_similarity"] is None

    floored = client.get("/pills/semantic", params={"q": "x", "min_similarity": 0.5}).json()
    assert {p["title"] for p in floored["pills"]} == {"Close match", "Partial match"}
    assert floored["retrieval_metrics"]["below_min_similarity"] == 1


def test_env_min_similarity_is_the_default(monkeypatch):
    monkeypatch.setenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", "0.9")
    _patch(monkeypatch, api_module, _corpus())
    body = TestClient(api_module.app).get("/pills/semantic", params={"q": "x"}).json()
    assert [p["title"] for p in body["pills"]] == ["Close match"]


@pytest.mark.asyncio
async def test_mcp_semantic_matches_rest_shape(monkeypatch):
    monkeypatch.delenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", raising=False)
    _patch(monkeypatch, server_module, _corpus())

    body = json.loads(await server_module.semantic_search("x"))
    assert body["count"] == 3
    top = body["pills"][0]
    for key in ("similarity", "retrieval_score", "is_superseded", "freshness_score"):
        assert key in top
    assert "embedding" not in top
    assert "retrieval_metrics" in body


@pytest.mark.asyncio
async def test_mcp_semantic_reports_no_hits(monkeypatch):
    _patch(monkeypatch, server_module, _corpus())
    body = json.loads(await server_module.semantic_search("x", min_similarity=0.99))
    assert body["count"] == 1  # only the exact match survives
    body = json.loads(await server_module.semantic_search("x", category="none"))
    assert body["count"] == 0
    assert "message" in body
