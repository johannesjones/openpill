"""Tests for graph rewire and relation helpers."""

from __future__ import annotations

import pytest
from bson import ObjectId

from pill_relations import expand_semantic_neighbors_hops, relation_doc, rewire_relations_on_merge
from models import PillRelationKind

from tests.fakes import FakeCollection


@pytest.mark.asyncio
async def test_relation_doc_shape():
    d = relation_doc("507f1f77bcf86cd799439011", PillRelationKind.RELATED)
    assert d == {"target_id": "507f1f77bcf86cd799439011", "kind": "related"}


@pytest.mark.asyncio
async def test_rewire_rewrites_incoming_and_merges_outgoing():
    """External pill X points to archived original A; merged pill M inherits edges."""
    col = FakeCollection()
    oid_a = ObjectId()
    oid_b = ObjectId()
    oid_x = ObjectId()
    oid_y = ObjectId()
    oid_m = ObjectId()
    new_id = str(oid_m)
    original_ids = [str(oid_a), str(oid_b)]

    col.docs = [
        {
            "_id": oid_a,
            "status": "archived",
            "relations": [{"target_id": str(oid_x), "kind": "related"}],
        },
        {
            "_id": oid_b,
            "status": "archived",
            "relations": [{"target_id": str(oid_y), "kind": "related"}],
        },
        {
            "_id": oid_x,
            "status": "active",
            "relations": [{"target_id": str(oid_a), "kind": "related"}],
        },
        {
            "_id": oid_m,
            "status": "active",
            "relations": [],
        },
    ]

    await rewire_relations_on_merge(col, original_ids, new_id)

    merged = next(d for d in col.docs if d["_id"] == oid_m)
    targets = {r["target_id"] for r in merged.get("relations", [])}
    assert str(oid_x) in targets
    assert str(oid_y) in targets

    x_doc = next(d for d in col.docs if d["_id"] == oid_x)
    assert x_doc["relations"][0]["target_id"] == new_id

    for oid in (oid_a, oid_b):
        odoc = next(d for d in col.docs if d["_id"] == oid)
        assert odoc.get("relations") == []


@pytest.mark.asyncio
async def test_expand_neighbors_supports_two_hops():
    col = FakeCollection()
    oid_a = ObjectId()
    oid_b = ObjectId()
    oid_c = ObjectId()
    col.docs = [
        {
            "_id": oid_a,
            "status": "active",
            "title": "A",
            "relations": [{"target_id": str(oid_b), "kind": "related"}],
        },
        {
            "_id": oid_b,
            "status": "active",
            "title": "B",
            "relations": [{"target_id": str(oid_c), "kind": "related"}],
        },
        {
            "_id": oid_c,
            "status": "active",
            "title": "C",
            "relations": [],
        },
    ]
    seed = [{"_id": str(oid_a), "title": "A", "similarity": 0.9}]
    out = await expand_semantic_neighbors_hops(
        col,
        seed,
        neighbor_limit=5,
        max_hops=2,
        max_nodes=10,
        hop_decay=0.1,
    )
    ids = [row["_id"] for row in out]
    assert str(oid_b) in ids
    assert str(oid_c) in ids
    c = next(row for row in out if row["_id"] == str(oid_c))
    assert c["hop"] == 2
