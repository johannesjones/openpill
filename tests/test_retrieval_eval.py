"""Memory-quality eval (offline hash embedder) must not drop below its baseline."""

from __future__ import annotations

import pytest

from evals.retrieval_eval import below_baseline, run_eval


@pytest.mark.asyncio
async def test_retrieval_eval_meets_baseline(monkeypatch):
    monkeypatch.delenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", raising=False)
    report = await run_eval("hash")
    assert set(report["summary"]) == {
        "single_hop", "multi_hop", "temporal", "knowledge_update", "abstention"
    }
    assert not below_baseline(report), report["failures"]
