"""Canonical relation kinds and sanitization (Phase B2)."""

from __future__ import annotations

from models import PillRelationKind, normalize_relation_kind
from pill_relations import sanitize_relations


def test_normalize_relation_kind_known():
    assert normalize_relation_kind("related") == PillRelationKind.RELATED
    assert normalize_relation_kind("CONFLICTS_WITH") == PillRelationKind.CONFLICTS_WITH
    assert normalize_relation_kind("same-topic") == PillRelationKind.SAME_TOPIC


def test_normalize_relation_kind_unknown_defaults():
    assert normalize_relation_kind("nonsense") == PillRelationKind.RELATED
    assert normalize_relation_kind("") == PillRelationKind.RELATED
    assert normalize_relation_kind(None) == PillRelationKind.RELATED


def test_sanitize_relations_normalizes_and_dedupes():
    rels = [
        {"target_id": "507f1f77bcf86cd799439011", "kind": "unknown_kind"},
        {"target_id": "507f1f77bcf86cd799439011", "kind": "related"},
        {"target_id": "", "kind": "related"},
    ]
    out = sanitize_relations(rels)
    assert len(out) == 1
    assert out[0]["target_id"] == "507f1f77bcf86cd799439011"
    assert out[0]["kind"] == "related"
