"""API key and idempotency behavior (mocked Mongo for idempotency)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_stats_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_PILL_API_KEY", "secret-test-key")
    import importlib

    import api as api_module

    importlib.reload(api_module)

    from fastapi.testclient import TestClient

    col = MagicMock()
    col.count_documents = AsyncMock(side_effect=[1, 0])

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)

    client = TestClient(api_module.app)
    r = client.get("/stats")
    assert r.status_code == 401

    r2 = client.get("/stats", headers={"Authorization": "Bearer secret-test-key"})
    assert r2.status_code == 200

    importlib.reload(api_module)
    monkeypatch.delenv("KNOWLEDGE_PILL_API_KEY", raising=False)


def test_health_public_when_api_key_configured(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_PILL_API_KEY", "secret-test-key")
    import importlib

    import api as api_module

    importlib.reload(api_module)

    from fastapi.testclient import TestClient

    client = TestClient(api_module.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    importlib.reload(api_module)
    monkeypatch.delenv("KNOWLEDGE_PILL_API_KEY", raising=False)


def test_x_api_key_header_accepted(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_PILL_API_KEY", "k")
    import importlib

    import api as api_module

    importlib.reload(api_module)

    from unittest.mock import AsyncMock, MagicMock

    col = MagicMock()
    col.count_documents = AsyncMock(side_effect=[1, 0])

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)

    from fastapi.testclient import TestClient

    client = TestClient(api_module.app)
    r = client.get("/stats", headers={"X-API-Key": "k"})
    assert r.status_code == 200

    importlib.reload(api_module)
    monkeypatch.delenv("KNOWLEDGE_PILL_API_KEY", raising=False)


@pytest.mark.asyncio
async def test_idempotency_replays_stored_response(monkeypatch):
    """Same Idempotency-Key + body returns cached JSON without calling extractor."""
    import importlib

    import api as api_module

    importlib.reload(api_module)

    stored = {"inserted": [], "replay": True}

    async def fake_resolve(key, route, body_hash):
        if key == "idem-1":
            return stored
        return None

    async def fake_store(*args, **kwargs):
        pytest.fail("store should not run on replay")

    monkeypatch.setattr(
        "idempotency.resolve_idempotency",
        fake_resolve,
    )
    monkeypatch.setattr(
        "idempotency.store_idempotent_response",
        fake_store,
    )

    from fastapi.testclient import TestClient

    client = TestClient(api_module.app)
    r = client.post(
        "/pills/ingest",
        json={"text": "hello world", "source_reference": "", "min_confidence": 0.5},
        headers={"Idempotency-Key": "idem-1"},
    )
    assert r.status_code == 200
    assert r.json() == stored
