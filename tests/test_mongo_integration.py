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
