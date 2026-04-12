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
    """Golden hash of canonical OpenAPI JSON (allow known cross-runtime variants)."""
    schema = app.openapi()
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Bump intentionally when routes/schemas/metadata change in api.py.
    # Different Python runtimes can produce equivalent OpenAPI with minor serialized differences.
    assert digest in {
        "34491e3f3c052d4e0f54cc33816de27d9f218c754e9b2c7f97feb47fe8c146ce",  # py3.10
        "c228f3aafcf63adc8a790f9f4d6f27846e95a468c2a457a1ca12c58a78a97696",  # py3.11
        "7624e9eb621b86f87b2978394905852809f8f0e3626434c0afe8cfc3a660b7d1",  # py3.11 (ingest merged_same_source)
    }
