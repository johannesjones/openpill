"""
Namespaces for OpenPill pills (modelled on LangGraph store namespaces).

A namespace is a short path such as ``jjones/job_tracker``, stored on the pill
as a list (``["jjones", "job_tracker"]``). Pills without one belong to the
global namespace (all pills written before namespaces existed).

- Reads take a namespace *prefix*: ``jjones`` matches ``jjones/job_tracker``.
  No namespace on a read means no filter (backward compatible).
- Writes and maintenance (dedup, same-source merge, auto-links, janitor,
  watchdog) only ever touch pills in the *same exact* namespace.
- An API key or MCP process can be bound to a prefix; requests may narrow it
  but never leave it.
"""

from __future__ import annotations

import json
import os
import re

_SEGMENT = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
MAX_DEPTH = 6


class NamespaceError(ValueError):
    """Invalid namespace, or one outside the caller's allowed prefix."""


def parse_namespace(raw: str | list[str] | None) -> list[str] | None:
    """``"a/b"`` or ``["a", "b"]`` → ``["a", "b"]``; ``None``/``""`` → ``None``."""
    if raw is None:
        return None
    parts = raw if isinstance(raw, list) else str(raw).strip().strip("/").split("/")
    parts = [str(p).strip() for p in parts if str(p).strip()]
    if not parts:
        return None
    if len(parts) > MAX_DEPTH:
        raise NamespaceError(f"Namespace deeper than {MAX_DEPTH} segments.")
    for p in parts:
        if not _SEGMENT.match(p):
            raise NamespaceError(
                f"Invalid namespace segment {p!r}: use letters, digits, _ . @ - (max 64)."
            )
    return parts


def format_namespace(ns: list[str] | None) -> str:
    return "/".join(ns or [])


def is_within(ns: list[str] | None, prefix: list[str] | None) -> bool:
    """True when ``ns`` starts with ``prefix`` (no prefix = everything)."""
    if not prefix:
        return True
    ns = ns or []
    return ns[: len(prefix)] == prefix


def prefix_filter(prefix: list[str] | None) -> dict:
    """Mongo filter for pills under ``prefix`` (empty dict = no restriction)."""
    return {f"namespace.{i}": seg for i, seg in enumerate(prefix or [])}


def exact_filter(ns: list[str] | None) -> dict:
    """Mongo filter for pills in exactly ``ns`` (global = missing or empty)."""
    if ns:
        return {"namespace": list(ns)}
    return {"namespace": {"$in": [None, []]}}


def resolve(requested: list[str] | None, bound: list[str] | None) -> list[str] | None:
    """Combine a request's namespace with the caller's bound prefix.

    No request → the bound prefix. A request must lie within the bound prefix.
    """
    if requested is None:
        return bound
    if not is_within(requested, bound):
        raise NamespaceError(
            f"Namespace {format_namespace(requested)!r} is outside "
            f"{format_namespace(bound)!r}."
        )
    return requested


def api_key_namespaces() -> dict[str, list[str] | None]:
    """``OPENPILL_API_KEYS``: JSON object ``{"<key>": "<namespace prefix>"}``.

    An empty prefix (``""``) gives that key access to every namespace.
    """
    raw = os.getenv("OPENPILL_API_KEYS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NamespaceError("OPENPILL_API_KEYS must be a JSON object.") from exc
    if not isinstance(data, dict):
        raise NamespaceError("OPENPILL_API_KEYS must be a JSON object.")
    return {str(k): parse_namespace(v) for k, v in data.items()}


def mcp_namespace() -> list[str] | None:
    """``OPENPILL_MCP_NAMESPACE``: prefix bound to this MCP server process."""
    return parse_namespace(os.getenv("OPENPILL_MCP_NAMESPACE"))
