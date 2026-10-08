"""Optional integration checks when MongoDB is available (CI services job)."""

from __future__ import annotations

import os

import pytest
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.mark.asyncio
async def test_mongo_server_ping():
    if os.getenv("RUN_MONGO_INTEGRATION") != "1":
        pytest.skip("Set RUN_MONGO_INTEGRATION=1 to run (CI integration job)")
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    client = AsyncIOMotorClient(uri)
    try:
        reply = await client.admin.command("ping")
        assert reply.get("ok") == 1.0
    finally:
        client.close()


@pytest.mark.asyncio
async def test_idempotency_collection_indexes():
    """Ensures idempotency helpers connect and create indexes (same DB as pills)."""
    if os.getenv("RUN_MONGO_INTEGRATION") != "1":
        pytest.skip("Set RUN_MONGO_INTEGRATION=1 to run (CI integration job)")
    from db import get_idempotency_collection

    col = await get_idempotency_collection()
    indexes = await col.index_information()
    assert "idem_key_route" in indexes
    assert "idem_ttl" in indexes


@pytest.mark.asyncio
async def test_namespaces_expiry_and_ranking_on_real_mongo():
    """Query shapes the in-memory fake only approximates, against a throwaway DB."""
    if os.getenv("RUN_MONGO_INTEGRATION") != "1":
        pytest.skip("Set RUN_MONGO_INTEGRATION=1 to run (CI integration job)")
    from datetime import datetime, timedelta, timezone

    from db import _ensure_indexes, archive_expired
    from extractor import find_near_duplicates
    from namespaces import exact_filter, prefix_filter
    from retrieval import semantic_retrieve

    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    client = AsyncIOMotorClient(uri)
    db = client["openpill_integration_test"]
    col = db["knowledge_pills"]
    try:
        await client.drop_database(db.name)
        # Old deployments have a TTL index; startup must replace it with a plain one.
        await col.create_index("expires_at", expireAfterSeconds=0, name="ttl_expires")
        await _ensure_indexes(col)
        assert "expireAfterSeconds" not in (await col.index_information())["ttl_expires"]

        now = datetime.now(timezone.utc)
        base = {"category": "notes", "status": "active", "confidence": 0.9,
                "created_at": now, "updated_at": now, "relations": []}
        await col.insert_many([
            {**base, "title": "legacy", "content": "c", "embedding": [1.0, 0.0]},
            {**base, "title": "empty ns", "content": "c", "embedding": [1.0, 0.0], "namespace": []},
            {**base, "title": "alice", "content": "c", "embedding": [1.0, 0.0], "namespace": ["alice"]},
            {**base, "title": "alice work", "content": "c", "embedding": [0.0, 1.0],
             "namespace": ["alice", "work"]},
            {**base, "title": "bob", "content": "c", "embedding": [1.0, 0.0], "namespace": ["bob"]},
            {**base, "title": "expired", "content": "c", "embedding": [1.0, 0.0],
             "expires_at": now - timedelta(days=1)},
        ])

        async def titles(query):
            return {d["title"] async for d in col.find(query)}

        assert await titles(prefix_filter(["alice"])) == {"alice", "alice work"}
        assert await titles(exact_filter(["alice"])) == {"alice"}
        assert await titles(exact_filter(None)) == {"legacy", "empty ns", "expired"}

        assert await archive_expired(col) == 1
        assert (await col.find_one({"title": "expired"}))["status"] == "archived"

        dupes = await find_near_duplicates([1.0, 0.0], col, threshold=0.9, namespace=["bob"])
        assert [d["title"] for d in dupes] == ["bob"]

        result = await semantic_retrieve(col, "q", [1.0, 0.0], namespace=["alice"])
        assert [p["title"] for p in result["pills"]] == ["alice", "alice work"]
    finally:
        await client.drop_database(db.name)
        client.close()
