"""Embedding helpers: model resolution and cross-model comparison safety."""

from __future__ import annotations

import logging
import math

import embeddings


def test_embedding_model_resolves_at_call_time(monkeypatch):
    """A .env loaded after import must still apply, so the model is not cached."""
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    assert embeddings.embedding_model() == embeddings.DEFAULT_MODEL
    monkeypatch.setenv("EMBEDDING_MODEL", "ollama/nomic-embed-text")
    assert embeddings.embedding_model() == "ollama/nomic-embed-text"


def test_cosine_similarity_matches_known_values():
    assert embeddings.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert embeddings.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert math.isclose(embeddings.cosine_similarity([1.0, 1.0], [2.0, 2.0]), 1.0)


def test_zero_vector_does_not_divide_by_zero():
    assert embeddings.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_different_dimensions_score_as_no_match(monkeypatch, caplog):
    """Switching EMBEDDING_MODEL must not silently corrupt search results.

    zip() would compare a truncated prefix and return a confident wrong number, so a
    pill embedded by another model has to score zero and say so.
    """
    monkeypatch.setattr(embeddings, "_warned_dimension_mismatch", False)
    query = [1.0, 0.0, 0.0]
    stored_other_model = [1.0, 0.0]

    with caplog.at_level(logging.WARNING, logger="openpill.embeddings"):
        score = embeddings.cosine_similarity(query, stored_other_model)

    assert score == 0.0
    assert "dimension mismatch" in caplog.text


def test_dimension_mismatch_warns_only_once(monkeypatch, caplog):
    """The guard runs per pill inside scoring loops, so it must not flood the log."""
    monkeypatch.setattr(embeddings, "_warned_dimension_mismatch", False)

    with caplog.at_level(logging.WARNING, logger="openpill.embeddings"):
        for _ in range(5):
            embeddings.cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0])

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
