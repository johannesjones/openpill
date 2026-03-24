"""Tests for classical NLP topic snapshot endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

import api as api_module


def test_topics_snapshot_returns_expected_shape(monkeypatch):
    sample = {
        "summary": {
            "active_docs": 2,
            "vocab_size": 10,
            "eligible_terms": 4,
            "min_doc_freq": 2,
            "min_token_len": 3,
        },
        "top_terms": [{"term": "memory", "score": 12, "tf": 4, "df": 3}],
        "topics_by_category": {"architecture": [{"term": "mcp", "salience": 0.8, "tf": 3}]},
    }

    async def fake_snapshot(**kwargs):
        assert kwargs["top_terms"] == 20
        assert kwargs["per_category"] == 10
        assert kwargs["min_doc_freq"] == 2
        assert kwargs["min_token_len"] == 3
        return sample

    monkeypatch.setattr(api_module, "build_topic_snapshot", fake_snapshot)
    client = TestClient(api_module.app)
    r = client.get("/topics/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"summary", "top_terms", "topics_by_category"}
    assert body["summary"]["active_docs"] == 2
    assert body["top_terms"][0]["term"] == "memory"


def test_topics_snapshot_forwards_query_params(monkeypatch):
    captured = {}

    async def fake_snapshot(**kwargs):
        captured.update(kwargs)
        return {"summary": {}, "top_terms": [], "topics_by_category": {}}

    monkeypatch.setattr(api_module, "build_topic_snapshot", fake_snapshot)
    client = TestClient(api_module.app)
    r = client.get(
        "/topics/snapshot",
        params={
            "top_terms": 7,
            "per_category": 5,
            "min_doc_freq": 3,
            "min_token_len": 4,
        },
    )
    assert r.status_code == 200
    assert captured == {
        "top_terms": 7,
        "per_category": 5,
        "min_doc_freq": 3,
        "min_token_len": 4,
    }


def test_topics_snapshot_rejects_out_of_range_params():
    client = TestClient(api_module.app)
    r = client.get("/topics/snapshot", params={"max_terms": 0, "min_token_len": 1})
    # min_token_len below ge=2 should fail validation
    assert r.status_code == 422
