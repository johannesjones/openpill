"""
Temporal validity and version history for pills (after Zep/Graphiti).

- ``valid_at``: when the fact became true (default: ``created_at``).
- ``invalid_at``: when it stopped being true. An invalidated pill stays in the
  store as history but is hidden from default reads (``include_invalid`` shows it).
  Contradicted facts are invalidated, not deleted or merged away.
- ``history``: previous title/content versions, appended on every rewrite
  (PATCH, same-source merge, LLM UPDATE), capped at ``HISTORY_LIMIT``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from models import HISTORY_LIMIT


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value) -> datetime | None:
    """Datetime or ISO string → aware datetime (naive = UTC); else None."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def validity_filter(now: datetime | None = None) -> dict:
    """Mongo filter for pills that are still valid (no ``invalid_at``, or in the future)."""
    return {"$or": [{"invalid_at": None}, {"invalid_at": {"$gt": now or now_utc()}}]}


def is_invalid(doc: dict, now: datetime | None = None) -> bool:
    invalid_at = parse_dt(doc.get("invalid_at"))
    return invalid_at is not None and invalid_at <= (now or now_utc())


def fact_time(doc: dict):
    """The time a fact refers to, for freshness: ``valid_at`` if set, else ``updated_at``."""
    return doc.get("valid_at") or doc.get("updated_at")


def history_push(doc: dict, *, reason: str) -> dict:
    """``$push`` update that records ``doc``'s current title/content before a rewrite."""
    previous = parse_dt(doc.get("updated_at"))
    entry = {
        "title": doc.get("title"),
        "content": doc.get("content"),
        "category": doc.get("category"),
        "previous_updated_at": previous.isoformat() if previous else None,
        "replaced_at": now_utc().isoformat(),
        "reason": reason,
    }
    return {"$push": {"history": {"$each": [entry], "$slice": -HISTORY_LIMIT}}}


def rewrites_text(doc: dict, update_fields: dict) -> bool:
    """True when an update changes the pill's title or content."""
    return any(
        key in update_fields and update_fields[key] != doc.get(key) for key in ("title", "content")
    )
