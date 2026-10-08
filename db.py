"""
MongoDB connection and collection management for OpenPill.

Reads MONGO_URI and DB/collection env vars and creates indexes on first
connection. Supports legacy names for backward compatibility.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, TEXT

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
# New names first; legacy names still supported.
MONGO_DB = (
    os.getenv("OPENPILL_DB")
    or os.getenv("MEMORA_DB")
    or os.getenv("MONGO_DB", "knowledge_pills_db")
)
COLLECTION = (
    os.getenv("OPENPILL_COLLECTION")
    or os.getenv("MEMORA_COLLECTION")
    or os.getenv("KNOWLEDGE_COLLECTION", "knowledge_pills")
)
IDEMPOTENCY_COLLECTION = "idempotency_keys"
# TTL for idempotency records (default 72h); Mongo deletes expired docs automatically.
IDEMPOTENCY_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", str(72 * 3600)))

_client: Optional[AsyncIOMotorClient] = None
_idempotency_indexes_ready = False


async def get_collection() -> AsyncIOMotorCollection:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI)
        await _ensure_indexes(_client[MONGO_DB][COLLECTION])
    return _client[MONGO_DB][COLLECTION]


async def get_idempotency_collection() -> AsyncIOMotorCollection:
    """Collection for ingest idempotency keys (TTL on created_at)."""
    global _client, _idempotency_indexes_ready
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI)
        await _ensure_indexes(_client[MONGO_DB][COLLECTION])
    idem = _client[MONGO_DB][IDEMPOTENCY_COLLECTION]
    if not _idempotency_indexes_ready:
        await _ensure_idempotency_indexes(idem)
        _idempotency_indexes_ready = True
    return idem


async def _ensure_idempotency_indexes(col: AsyncIOMotorCollection) -> None:
    await col.create_index(
        [("idempotency_key", ASCENDING), ("route", ASCENDING)],
        unique=True,
        name="idem_key_route",
    )
    await col.create_index(
        "created_at",
        expireAfterSeconds=IDEMPOTENCY_TTL_SECONDS,
        name="idem_ttl",
    )


async def _ensure_indexes(col: AsyncIOMotorCollection) -> None:
    await col.create_index([("title", TEXT), ("content", TEXT)], name="text_search")
    await col.create_index(
        [("category", ASCENDING), ("status", ASCENDING)], name="category_status"
    )
    await col.create_index("tags", name="tags_idx")
    # Expiry archives (see archive_expired); a Mongo TTL index would hard-delete.
    existing = await col.index_information()
    if "expireAfterSeconds" in existing.get("ttl_expires", {}):
        await col.drop_index("ttl_expires")
    await col.create_index("expires_at", name="ttl_expires")
    await col.create_index([("relations.target_id", ASCENDING)], name="relations_target_idx")


async def archive_expired(col: AsyncIOMotorCollection) -> int:
    """Soft-delete active pills whose ``expires_at`` has passed. Returns the count."""
    now = datetime.now(timezone.utc)
    result = await col.update_many(
        {"status": "active", "expires_at": {"$lte": now}},
        {"$set": {"status": "archived", "updated_at": now}},
    )
    return result.modified_count


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
