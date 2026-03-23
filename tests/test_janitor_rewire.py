"""Janitor consolidation triggers relation rewire."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from janitor import ConsolidatedPill, apply_consolidation


@pytest.mark.asyncio
async def test_apply_consolidation_calls_rewire_relations(monkeypatch):
    captured: dict = {}

    async def capture_rewire(col, original_ids, new_id):
        captured["original_ids"] = list(original_ids)
        captured["new_id"] = new_id

    col = MagicMock()
    new_oid = ObjectId()
    col.insert_one = AsyncMock(return_value=MagicMock(inserted_id=new_oid))
    col.update_many = AsyncMock()

    monkeypatch.setattr("janitor.rewire_relations_on_merge", capture_rewire)
    monkeypatch.setattr("janitor.write_audit_log", AsyncMock())

    consolidated = ConsolidatedPill(title="Merged", content="Body", tags=[], confidence=0.9)
    oid_a = str(ObjectId())
    oid_b = str(ObjectId())

    new_id = await apply_consolidation(col, consolidated, [oid_a, oid_b], "python", "test reason")

    assert new_id == str(new_oid)
    assert captured["original_ids"] == [oid_a, oid_b]
    assert captured["new_id"] == new_id
    col.insert_one.assert_awaited_once()
    col.update_many.assert_awaited_once()
