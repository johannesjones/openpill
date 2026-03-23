"""
Idempotency for POST ingest routes: Idempotency-Key + body hash → replay stored JSON.

Uses collection `idempotency_keys` with TTL on `created_at` (see db.py).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException

from db import get_idempotency_collection

logger = logging.getLogger("knowledge_pills.idempotency")


async def resolve_idempotency(
    idempotency_key: Optional[str],
    route: str,
    body_hash: str,
) -> Optional[dict[str, Any]]:
    """If key present, return None to proceed, or a dict to replay as the response body.

    Raises HTTPException 409 if the same key was used with a different body.
    """
    if not idempotency_key:
        return None

    col = await get_idempotency_collection()
    doc = await col.find_one(
        {"idempotency_key": idempotency_key, "route": route},
        projection={"body_hash": 1, "response_json": 1},
    )
    if doc is None:
        return None
    if doc.get("body_hash") == body_hash:
        replay = doc.get("response_json")
        if isinstance(replay, dict):
            return replay
        logger.warning("idempotency replay missing or invalid response_json for %s %s", route, idempotency_key)
        return None
    raise HTTPException(
        status_code=409,
        detail="Idempotency-Key was already used with a different request body; use a new key.",
    )


async def store_idempotent_response(
    idempotency_key: Optional[str],
    route: str,
    body_hash: str,
    response_body: dict[str, Any],
) -> None:
    """Persist successful response for replay (TTL managed by Mongo)."""
    if not idempotency_key:
        return
    col = await get_idempotency_collection()
    await col.replace_one(
        {"idempotency_key": idempotency_key, "route": route},
        {
            "idempotency_key": idempotency_key,
            "route": route,
            "body_hash": body_hash,
            "response_json": response_body,
            "created_at": datetime.utcnow(),
        },
        upsert=True,
    )
