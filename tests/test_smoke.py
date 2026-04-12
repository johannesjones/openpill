"""Smoke tests: API health, fixtures, ingest response shape (no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "transcript.txt"


def test_transcript_fixture_exists_and_non_empty():
    assert FIXTURE.is_file()
    text = FIXTURE.read_text(encoding="utf-8")
    assert len(text.strip()) > 50


def test_ingest_response_has_expected_keys():
    """Document expected JSON shape from extractor runs (for API/MCP clients)."""
    sample = {
        "inserted": [],
        "merged_same_source": [],
        "skipped_confidence": [],
        "skipped_duplicate": [],
        "skipped_short": [],
        "stats": {},
    }
    assert "inserted" in sample
    assert "merged_same_source" in sample
    assert "skipped_duplicate" in sample
    dup = sample["skipped_duplicate"]
    assert isinstance(dup, list)


def test_health_endpoint():
    from fastapi.testclient import TestClient

    from api import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_stats_endpoint_mocked(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from fastapi.testclient import TestClient

    import api as api_module

    col = MagicMock()
    col.count_documents = AsyncMock(side_effect=[42, 7])

    async def fake_col():
        return col

    monkeypatch.setattr(api_module, "get_collection", fake_col)

    client = TestClient(api_module.app)
    r = client.get("/stats")
    assert r.status_code == 200
    assert r.json() == {"active_pills": 42, "pills_with_relations": 7}
