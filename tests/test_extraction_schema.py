"""Extraction JSON schema: legacy pills[] plus optional strict fields (Phase B2)."""

from __future__ import annotations

import json

from extractor import (
    ExtractionResult,
    ExtractedFact,
    _extraction_provenance_from_fact,
    _strict_extraction_schema_enabled,
)
from models import ExtractionProvenance, KnowledgePill, PillSource, SourceType


LEGACY_JSON = json.dumps(
    {
        "pills": [
            {
                "title": "TTL indexes",
                "content": "MongoDB can expire documents with a TTL index.",
                "category": "databases",
                "tags": ["mongodb"],
                "confidence": 0.88,
            }
        ]
    }
)

STRICT_JSON = json.dumps(
    {
        "pills": [
            {
                "title": "TTL indexes",
                "content": "MongoDB can expire documents with a TTL index.",
                "category": "databases",
                "tags": ["mongodb"],
                "confidence": 0.88,
                "entities": ["MongoDB"],
                "relation_hints": [
                    {"target_concept": "Atlas", "kind": "related"},
                    {"target_concept": "SQL", "kind": "conflicts_with"},
                ],
                "evidence_quote": "TTL index on a datetime field",
                "rationale": "Operational pattern worth recalling.",
            }
        ]
    }
)


def test_legacy_extraction_json_parses():
    r = ExtractionResult.model_validate_json(LEGACY_JSON)
    assert len(r.pills) == 1
    p = r.pills[0]
    assert p.title.startswith("TTL")
    assert p.entities == []
    assert p.relation_hints == []
    assert p.evidence_quote is None


def test_strict_extraction_json_parses():
    r = ExtractionResult.model_validate_json(STRICT_JSON)
    p = r.pills[0]
    assert "MongoDB" in p.entities
    assert len(p.relation_hints) == 2
    assert p.relation_hints[1].kind == "conflicts_with"
    assert p.evidence_quote is not None


def test_unknown_top_level_keys_ignored_on_pill():
    raw = json.dumps(
        {
            "pills": [
                {
                    "title": "x",
                    "content": "y" * 20,
                    "category": "other",
                    "tags": [],
                    "confidence": 0.9,
                    "future_field": 123,
                }
            ]
        }
    )
    r = ExtractionResult.model_validate_json(raw)
    assert not hasattr(r.pills[0], "future_field") or getattr(
        r.pills[0], "future_field", None
    ) is None


def test_extraction_provenance_none_when_empty_structured():
    fact = ExtractedFact.model_validate_json(
        json.dumps(
            {
                "title": "a",
                "content": "b" * 20,
                "category": "other",
                "tags": [],
                "confidence": 0.9,
            }
        )
    )
    assert _extraction_provenance_from_fact(fact) is None


def test_extraction_provenance_from_strict_fact():
    fact = ExtractionResult.model_validate_json(STRICT_JSON).pills[0]
    prov = _extraction_provenance_from_fact(fact)
    assert prov is not None
    assert isinstance(prov, ExtractionProvenance)
    assert prov.relation_hints[1].kind == "conflicts_with"


def test_knowledge_pill_round_trip_extraction_meta():
    prov = ExtractionProvenance(
        entities=["Docker"],
        relation_hints=[
            {"target_concept": "Kubernetes", "kind": "related"},
        ],
        evidence_quote="COPY order matters",
        rationale="Build perf",
    )
    pill = KnowledgePill(
        title="Docker cache",
        content="Copy requirements before code in Dockerfile.",
        category="devops",
        tags=[],
        source=PillSource(type=SourceType.MANUAL, reference="test"),
        confidence=1.0,
        extraction_meta=prov,
    )
    doc = pill.to_mongo()
    assert doc.get("extraction_meta") is not None
    assert doc["extraction_meta"]["entities"] == ["Docker"]
    back = KnowledgePill.from_mongo({**doc, "_id": "507f1f77bcf86cd799439011"})
    assert back.extraction_meta is not None
    assert back.extraction_meta.entities == ["Docker"]


def test_relation_hint_invalid_kind_still_parses_then_normalizes_in_provenance():
    raw = json.dumps(
        {
            "pills": [
                {
                    "title": "t",
                    "content": "c" * 20,
                    "category": "other",
                    "tags": [],
                    "confidence": 0.8,
                    "relation_hints": [{"target_concept": "X", "kind": "weird_kind"}],
                    "rationale": "r",
                }
            ]
        }
    )
    fact = ExtractionResult.model_validate_json(raw).pills[0]
    prov = _extraction_provenance_from_fact(fact)
    assert prov is not None
    assert prov.relation_hints[0].kind == "related"


def test_strict_flag_reads_env(monkeypatch):
    monkeypatch.delenv("OPENPILL_STRICT_EXTRACTION_SCHEMA", raising=False)
    assert _strict_extraction_schema_enabled() is False
    monkeypatch.setenv("OPENPILL_STRICT_EXTRACTION_SCHEMA", "true")
    assert _strict_extraction_schema_enabled() is True
