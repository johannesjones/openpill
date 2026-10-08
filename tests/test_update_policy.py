"""LLM update decisions (ADD / UPDATE / INVALIDATE / NOOP) and supersedes hints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

import extractor
from extractor import ExtractedFact
from models import RelationConceptHint
from tests.fakes import FakeCollection

OLD_VEC = [1.0, 0.0]
NEW_VEC = [0.99, 0.14]  # cos ≈ 0.99: above the 0.92 duplicate threshold


def _pill(title, embedding=OLD_VEC, **extra):
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "title": title,
        "content": f"{title}. Recorded by the team.",
        "category": "other",
        "tags": [],
        "status": "active",
        "confidence": 0.9,
        "embedding": embedding,
        "source": {"type": "document", "reference": "extractor:notes"},
        "created_at": now,
        "updated_at": now,
        "relations": [],
        **extra,
    }


def _fact(title="Standup moved to 9:30", content="The daily standup now starts at 9:30.", **extra):
    return ExtractedFact(title=title, content=content, category="other", confidence=0.9, **extra)


@pytest.fixture
def run(monkeypatch):
    col = FakeCollection()

    async def fake_col():
        return col

    llm = AsyncMock()
    monkeypatch.setattr(extractor, "get_collection", fake_col)
    monkeypatch.setattr(extractor, "get_embedding", AsyncMock(return_value=NEW_VEC))
    monkeypatch.setattr(extractor, "_complete_json", llm)
    monkeypatch.setenv("OPENPILL_UPDATE_POLICY", "llm")
    monkeypatch.setenv("EXTRACTOR_LINK_ON_INSERT", "false")
    monkeypatch.delenv("OPENPILL_APPLY_SUPERSEDES_HINTS", raising=False)

    async def go(*facts, **kwargs):
        monkeypatch.setattr(extractor, "extract_facts", AsyncMock(return_value=list(facts)))
        return await extractor.run_extraction("text", "notes", dry_run=False, **kwargs)

    return go, col, llm


def _answer(llm, **decision):
    llm.return_value = json.dumps(decision)


@pytest.mark.asyncio
async def test_no_similar_memory_adds_without_an_llm_call(run):
    go, col, llm = run
    col.docs = [_pill("Unrelated", embedding=[0.0, 1.0])]
    result = await go(_fact())
    assert result["inserted"] == ["Standup moved to 9:30"]
    assert result["decisions"][0]["operation"] == "ADD"
    llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_keeps_old_fact_as_history(run):
    go, col, llm = run
    old = _pill("Standup at 10:00")
    col.docs = [old]
    _answer(llm, operation="INVALIDATE", target_id=str(old["_id"]), reason="time changed")

    result = await go(_fact())
    assert result["inserted"] == ["Standup moved to 9:30"]
    assert result["invalidated"][0]["pill_id"] == str(old["_id"])
    new = next(d for d in col.docs if d["title"] == "Standup moved to 9:30")
    assert old["invalid_at"] is not None and old["status"] == "active"
    assert {"target_id": str(old["_id"]), "kind": "supersedes"} in new["relations"]
    assert not any(r["kind"] == "supersedes" for r in old["relations"])


@pytest.mark.asyncio
async def test_update_rewrites_target_with_history(run):
    go, col, llm = run
    old = _pill("Standup time")
    col.docs = [old]
    _answer(
        llm, operation="UPDATE", target_id=str(old["_id"]),
        title="Standup time", content="Daily standup at 9:30 in room B.", reason="adds room",
    )
    result = await go(_fact())
    assert result["inserted"] == []
    assert result["updated"] == [{"title": "Standup time", "pill_id": str(old["_id"])}]
    assert old["content"] == "Daily standup at 9:30 in room B."
    assert old["history"][-1]["content"] == "Standup time. Recorded by the team."
    assert old["history"][-1]["reason"] == "llm_update"


@pytest.mark.asyncio
async def test_noop_skips(run):
    go, col, llm = run
    old = _pill("Standup at 9:30")
    col.docs = [old]
    _answer(llm, operation="NOOP", target_id=str(old["_id"]), reason="same")
    result = await go(_fact())
    assert result["inserted"] == [] and len(col.docs) == 1
    assert result["skipped_duplicate"][0]["similar_pill_id"] == str(old["_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["not json", json.dumps({"operation": "INVALIDATE", "target_id": "nope"})])
async def test_unusable_answer_falls_back_to_threshold_dedup(run, answer):
    go, col, llm = run
    col.docs = [_pill("Standup at 10:00")]
    llm.return_value = answer
    result = await go(_fact())
    assert result["inserted"] == []  # near-duplicate (cos 0.99) → skipped, nothing invalidated
    assert result["invalidated"] == [] and "invalid_at" not in col.docs[0]


@pytest.mark.asyncio
async def test_threshold_policy_never_calls_the_llm(run, monkeypatch):
    go, col, llm = run
    monkeypatch.setenv("OPENPILL_UPDATE_POLICY", "threshold")
    col.docs = [_pill("Standup at 10:00")]
    result = await go(_fact())
    llm.assert_not_awaited()
    assert result["decisions"] == [] and result["inserted"] == []


@pytest.mark.asyncio
async def test_supersedes_hints_invalidate_named_pills(run, monkeypatch):
    go, col, llm = run
    monkeypatch.setenv("OPENPILL_UPDATE_POLICY", "threshold")
    monkeypatch.setenv("OPENPILL_APPLY_SUPERSEDES_HINTS", "true")
    old = _pill("Reply language", embedding=[0.0, 1.0])
    other_ns = _pill("Reply language", embedding=[0.0, 1.0], namespace=["someone"])
    col.docs = [old, other_ns]
    fact = _fact(
        title="Reply language is English",
        content="Answer in English from now on, not German.",
        relation_hints=[RelationConceptHint(target_concept="reply language", kind="supersedes")],
    )
    result = await go(fact)
    assert [i["pill_id"] for i in result["invalidated"]] == [str(old["_id"])]
    assert "invalid_at" not in other_ns
