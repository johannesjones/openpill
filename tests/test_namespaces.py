"""Namespaces: parsing, REST/MCP isolation, API keys bound to a prefix, embed_text."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import api as api_module
import server as server_module
from extractor import find_near_duplicates
from janitor import fetch_pills_by_category
from namespaces import NamespaceError, parse_namespace, resolve
from pill_relations import find_related_candidates
from tests.fakes import FakeCollection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_parse_and_resolve():
    assert parse_namespace("jjones/job_tracker") == ["jjones", "job_tracker"]
    assert parse_namespace("/jjones/") == ["jjones"]
    assert parse_namespace("") is None
    with pytest.raises(NamespaceError):
        parse_namespace("bad segment")
    assert resolve(None, ["alice"]) == ["alice"]
    assert resolve(["alice", "notes"], ["alice"]) == ["alice", "notes"]
    with pytest.raises(NamespaceError):
        resolve(["bob"], ["alice"])


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


@pytest.fixture
def rest(monkeypatch):
    col = FakeCollection()

    async def fake_col():
        return col

    embed = AsyncMock(return_value=[1.0, 0.0])
    monkeypatch.setattr(api_module, "get_collection", fake_col)
    monkeypatch.setattr(api_module, "get_embedding", embed)
    monkeypatch.setattr(api_module, "AUTH_ENABLED", False)
    monkeypatch.delenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", raising=False)
    return TestClient(api_module.app), col, embed


def _create(client, title, namespace=None, headers=None, **extra):
    body = {"title": title, "content": "Body text", "category": "notes", **extra}
    if namespace is not None:
        body["namespace"] = namespace
    return client.post("/pills", json=body, headers=headers or {})


def test_rest_reads_are_scoped_to_the_namespace(rest):
    client, col, _ = rest
    alice = _create(client, "Alice pill", "alice/notes").json()
    _create(client, "Bob pill", "bob")
    _create(client, "Global pill")
    assert alice["namespace"] == "alice/notes"

    def titles(path, **params):
        if path == "/pills/semantic":
            params["q"] = "x"
        return {p["title"] for p in client.get(path, params=params).json()["pills"]}

    assert titles("/pills/semantic") == {"Alice pill", "Bob pill", "Global pill"}
    assert titles("/pills/semantic", namespace="alice") == {"Alice pill"}
    assert titles("/pills/search", namespace="bob") == {"Bob pill"}

    pill_id = alice["id"]
    assert client.get(f"/pills/{pill_id}", params={"namespace": "alice"}).status_code == 200
    assert client.get(f"/pills/{pill_id}", params={"namespace": "bob"}).status_code == 404
    assert client.patch(f"/pills/{pill_id}", params={"namespace": "bob"}, json={"title": "x"}).status_code == 404
    assert client.delete(f"/pills/{pill_id}", params={"namespace": "bob"}).status_code == 404
    assert client.delete(f"/pills/{pill_id}", params={"namespace": "alice"}).status_code == 200


def test_rest_rejects_invalid_namespace(rest):
    client, _, _ = rest
    assert _create(client, "Bad", "has space").status_code == 400


def test_api_key_bound_to_namespace(rest, monkeypatch):
    client, col, _ = rest
    monkeypatch.setattr(api_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(api_module, "API_KEY", "admin-key")
    monkeypatch.setattr(api_module, "NAMESPACED_API_KEYS", {"alice-key": ["alice"]})
    alice = {"Authorization": "Bearer alice-key"}
    admin = {"X-API-Key": "admin-key"}

    created = _create(client, "Alice pill", headers=alice).json()
    assert created["namespace"] == "alice"
    _create(client, "Bob pill", "bob", headers=admin)

    seen = client.get("/pills/semantic", params={"q": "x"}, headers=alice).json()["pills"]
    assert [p["title"] for p in seen] == ["Alice pill"]
    assert client.get("/pills/semantic", params={"q": "x", "namespace": "bob"}, headers=alice).status_code == 403
    assert _create(client, "Escape", "bob", headers=alice).status_code == 403
    assert _create(client, "Narrowed", "alice/work", headers=alice).status_code == 201

    every = client.get("/pills/semantic", params={"q": "x"}, headers=admin).json()["pills"]
    assert len(every) == 3
    assert client.get("/pills/semantic", params={"q": "x"}, headers={"X-API-Key": "nope"}).status_code == 401


def test_embed_text_controls_what_is_embedded(rest):
    client, col, embed = rest
    created = _create(client, "Acme - Engineer", content='{"company": "Acme"}', embed_text="Acme, applied").json()
    embed.assert_awaited_with("Acme, applied")
    pill_id = created["id"]

    embed.reset_mock()
    client.patch(f"/pills/{pill_id}", json={"content": '{"company": "Acme", "status": "x"}'})
    embed.assert_not_awaited()  # the override did not change, so neither did the vector

    client.patch(f"/pills/{pill_id}", json={"embed_text": "Acme, interview done"})
    embed.assert_awaited_with("Acme, interview done")

    client.patch(f"/pills/{pill_id}", json={"embed_text": ""})
    embed.assert_awaited_with('Acme - Engineer\n{"company": "Acme", "status": "x"}')
    assert col.docs[0]["embed_text"] is None


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_bound_namespace(monkeypatch):
    col = FakeCollection()

    async def fake_col():
        return col

    monkeypatch.setattr(server_module, "get_collection", fake_col)
    monkeypatch.setattr(server_module, "get_embedding", AsyncMock(return_value=[1.0, 0.0]))
    monkeypatch.delenv("OPENPILL_SEMANTIC_MIN_SIMILARITY", raising=False)
    monkeypatch.setenv("OPENPILL_MCP_NAMESPACE", "alice")

    created = json.loads(await server_module.create_pill("Alice pill", "Body text", "notes"))
    assert created["namespace"] == "alice"
    col.docs.append({**col.docs[0], "_id": "other", "title": "Bob pill", "namespace": ["bob"]})

    hits = json.loads(await server_module.semantic_search("x"))
    assert [p["title"] for p in hits["pills"]] == ["Alice pill"]
    assert "error" in json.loads(await server_module.semantic_search("x", namespace="bob"))
    assert "error" in json.loads(await server_module.get_pill(created["id"], namespace="alice/x"))


# ---------------------------------------------------------------------------
# Writes and maintenance stay inside one namespace
# ---------------------------------------------------------------------------


def _doc(title, namespace=None, category="notes"):
    from bson import ObjectId

    doc = {
        "_id": ObjectId(),
        "title": title,
        "content": "Body",
        "category": category,
        "status": "active",
        "embedding": [1.0, 0.0],
    }
    if namespace is not None:
        doc["namespace"] = namespace
    return doc


@pytest.mark.asyncio
async def test_dedup_and_links_never_cross_namespaces():
    col = FakeCollection()
    col.docs = [_doc("legacy"), _doc("empty", []), _doc("alice", ["alice"]), _doc("alice sub", ["alice", "x"])]

    titles = lambda rows: {r["title"] for r in rows}  # noqa: E731
    assert titles(await find_near_duplicates([1.0, 0.0], col, namespace=["alice"])) == {"alice"}
    assert titles(await find_near_duplicates([1.0, 0.0], col)) == {"legacy", "empty"}
    related = await find_related_candidates(
        [1.0, 0.0], col, category="notes", low=0.0, high=1.1,
        exclude_id=None, max_links=10, namespace=["alice", "x"],
    )
    assert titles(related) == {"alice sub"}


@pytest.mark.asyncio
async def test_janitor_groups_by_namespace(monkeypatch):
    monkeypatch.delenv("OPENPILL_MAINTENANCE_EXCLUDE_CATEGORIES", raising=False)
    col = FakeCollection()
    col.docs = [_doc("a"), _doc("b", ["alice"]), _doc("c", ["alice"])]
    groups = await fetch_pills_by_category(col)
    assert {k: len(v) for k, v in groups.items()} == {"notes": 1, "alice::notes": 2}


@pytest.mark.asyncio
async def test_move_to_namespace_moves_only_global_pills_of_the_category():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "move_to_namespace", Path(__file__).resolve().parents[1] / "scripts" / "move_to_namespace.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    col = FakeCollection()
    col.docs = [
        _doc("legacy app", category="job_application"),
        _doc("already moved", ["other"], category="job_application"),
        _doc("other category"),
    ]
    assert await module.move("job_application", ["jjones", "job_tracker"], apply=False, col=col) == 1
    assert "namespace" not in col.docs[0]
    assert await module.move("job_application", ["jjones", "job_tracker"], apply=True, col=col) == 1
    assert col.docs[0]["namespace"] == ["jjones", "job_tracker"]
    assert col.docs[1]["namespace"] == ["other"]
    assert "namespace" not in col.docs[2]
