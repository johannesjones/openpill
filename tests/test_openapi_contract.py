"""Offline OpenAPI contract checks (no running server)."""

from __future__ import annotations

import hashlib
import json

from api import app

# Required paths for API clients (ChatGPT Actions, scripts, etc.)
REQUIRED_PATHS = frozenset(
    {
        "/health",
        "/stats",
        "/pills/search",
        "/pills/semantic",
        "/pills/ingest",
        "/pills/ingest-conversation",
        "/categories",
    }
)


def test_openapi_required_paths_exist():
    schema = app.openapi()
    paths = set(schema.get("paths", {}).keys())
    missing = REQUIRED_PATHS - paths
    assert not missing, f"OpenAPI missing paths: {sorted(missing)}"


def test_openapi_ingest_conversation_post_present():
    schema = app.openapi()
    conv = schema["paths"].get("/pills/ingest-conversation", {})
    assert "post" in conv, "/pills/ingest-conversation must define POST"


def test_openapi_stable_hash_snapshot():
    """Golden hash of canonical OpenAPI JSON — update intentionally when API changes."""
    schema = app.openapi()
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Bump when you change routes, schemas, or metadata in api.py on purpose.
    assert digest == "d315bf42d14a91f6558e8eba81f70a9eab12bd22cf4439238575ae1edbea369d"
