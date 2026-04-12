"""
MongoDB schema definitions for OpenPill memory entries.

Tiered Memory – Long-Term layer:
Each entry is a distilled, atomic fact extracted from conversations,
documents, or manual input, stored for semantic retrieval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PillRelationKind(str, Enum):
    """Edge type between two knowledge pills (Phase B graph links).

    Canonical values (store only these in MongoDB):
    ``related``, ``supersedes``, ``same_topic``, ``conflicts_with``.
    Unknown strings from legacy data normalize to ``related`` (see ``normalize_relation_kind``).
    """

    RELATED = "related"
    SUPERSEDES = "supersedes"
    SAME_TOPIC = "same_topic"
    CONFLICTS_WITH = "conflicts_with"


def normalize_relation_kind(raw: str | None) -> PillRelationKind:
    """Map a string to a canonical ``PillRelationKind``; unknown → ``related``."""
    if raw is None or not str(raw).strip():
        return PillRelationKind.RELATED
    s = str(raw).strip().lower().replace("-", "_")
    for kind in PillRelationKind:
        if kind.value == s:
            return kind
    return PillRelationKind.RELATED


CANONICAL_RELATION_KIND_VALUES: tuple[str, ...] = tuple(e.value for e in PillRelationKind)


class PillRelation(BaseModel):
    """Directed edge to another pill (stored on the source document)."""

    target_id: str = Field(..., description="MongoDB ObjectId of the related pill")
    kind: PillRelationKind = Field(default=PillRelationKind.RELATED)


class PillStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DEPRECATED = "deprecated"


class SourceType(str, Enum):
    CHAT = "chat"
    DOCUMENT = "document"
    MANUAL = "manual"
    CODE = "code"


class PillSource(BaseModel):
    """Tracks where a knowledge pill originated."""

    type: SourceType
    reference: str = Field(
        ..., description="Chat ID, file path, URL, or free-text origin"
    )


class KnowledgePill(BaseModel):
    """
    Core schema for a single Knowledge Pill in MongoDB.

    Collection: `knowledge_pills`

    Indexes (created at startup):
      - text index on `title` + `content` (full-text search)
      - vectorSearch on `embedding` (Atlas vector search)
      - compound index on `category` + `status`
      - TTL index on `expires_at`
    """

    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, description="The distilled fact itself")
    category: str = Field(..., min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list)
    source: PillSource
    embedding: Optional[list[float]] = Field(
        default=None,
        description="Vector embedding (e.g. 1536-dim for text-embedding-3-small)",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: PillStatus = Field(default=PillStatus.ACTIVE)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(
        default=None, description="Optional TTL – pill auto-archives after this date"
    )
    relations: list[PillRelation] = Field(
        default_factory=list,
        description="Outgoing edges to related pills (1-hop graph links)",
    )

    def to_mongo(self) -> dict:
        """Serialize to a MongoDB-ready dict."""
        doc = self.model_dump(mode="json")
        doc["created_at"] = self.created_at
        doc["updated_at"] = self.updated_at
        if self.expires_at:
            doc["expires_at"] = self.expires_at
        return doc

    @classmethod
    def from_mongo(cls, doc: dict) -> KnowledgePill:
        """Deserialize a MongoDB document back into a model."""
        doc.pop("_id", None)
        if doc.get("relations") is None:
            doc["relations"] = []
        return cls.model_validate(doc)


# Public aliases for product naming while keeping code compatibility.
MemoraEntry = KnowledgePill
OpenPillEntry = KnowledgePill
