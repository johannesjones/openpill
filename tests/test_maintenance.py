"""Expiry archiving and category exclusion for janitor/watchdog."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

import watchdog
from db import _ensure_indexes, archive_expired
from janitor import fetch_pills_by_category
from tests.fakes import FakeCollection


def _pill(category: str, **extra) -> dict:
    return {
        "_id": ObjectId(),
        "title": f"{category} pill",
        "content": "Body",
        "category": category,
        "status": "active",
        **extra,
    }


@pytest.mark.asyncio
async def test_archive_expired_soft_deletes_only_past_expiry():
    now = datetime.now(timezone.utc)
    col = FakeCollection()
    expired = _pill("memory", expires_at=now - timedelta(days=1))
    future = _pill("memory", expires_at=now + timedelta(days=1))
    no_expiry = _pill("memory")
    col.docs = [expired, future, no_expiry]

    assert await archive_expired(col) == 1
    statuses = {d["_id"]: d["status"] for d in col.docs}
    assert statuses[expired["_id"]] == "archived"
    assert statuses[future["_id"]] == "active"
    assert statuses[no_expiry["_id"]] == "active"
    assert len(col.docs) == 3


@pytest.mark.asyncio
async def test_janitor_skips_excluded_categories(monkeypatch):
    monkeypatch.setenv("OPENPILL_MAINTENANCE_EXCLUDE_CATEGORIES", "job_application, other")
    col = FakeCollection()
    col.docs = [_pill("job_application"), _pill("python"), _pill("other")]

    groups = await fetch_pills_by_category(col)
    assert set(groups) == {"python"}


@pytest.mark.asyncio
async def test_watchdog_skips_excluded_categories(monkeypatch):
    monkeypatch.setenv("OPENPILL_MAINTENANCE_EXCLUDE_CATEGORIES", "job_application")
    find_neighbors = AsyncMock(return_value=[])
    monkeypatch.setattr(watchdog, "find_neighbors", find_neighbors)

    await watchdog.handle_new_pill(
        FakeCollection(), _pill("job_application", source={"reference": "x"}), 0.85, 15
    )
    find_neighbors.assert_not_awaited()

    await watchdog.handle_new_pill(
        FakeCollection(), _pill("python", source={"reference": "x"}), 0.85, 15
    )
    find_neighbors.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing, dropped", [
    ({"ttl_expires": {"key": [("expires_at", 1)], "expireAfterSeconds": 0}}, True),
    ({"ttl_expires": {"key": [("expires_at", 1)]}}, False),
    ({}, False),
])
async def test_ensure_indexes_replaces_ttl_index(existing, dropped):
    col = MagicMock()
    col.create_index = AsyncMock()
    col.drop_index = AsyncMock()
    col.index_information = AsyncMock(return_value=existing)

    await _ensure_indexes(col)

    assert col.drop_index.await_count == (1 if dropped else 0)
    expiry_calls = [c for c in col.create_index.await_args_list if c.args[0] == "expires_at"]
    assert len(expiry_calls) == 1
    assert "expireAfterSeconds" not in expiry_calls[0].kwargs
