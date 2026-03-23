"""Minimal in-memory async collection for unit tests (subset of Motor API)."""

from __future__ import annotations

from bson import ObjectId


def _match(doc: dict, q: dict) -> bool:
    if not q:
        return True
    for k, v in q.items():
        if k == "_id":
            if isinstance(v, dict):
                if "$in" in v and doc.get("_id") not in v["$in"]:
                    return False
                if "$nin" in v and doc.get("_id") in v["$nin"]:
                    return False
                if "$ne" in v and doc.get("_id") == v["$ne"]:
                    return False
            elif doc.get("_id") != v:
                return False
        elif k == "status":
            if doc.get("status") != v:
                return False
        elif k == "relations.target_id":
            rels = doc.get("relations") or []
            targets = {r.get("target_id") for r in rels}
            if isinstance(v, dict) and "$in" in v:
                if not targets.intersection(set(v["$in"])):
                    return False
            elif v not in targets:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = list(docs)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self.docs:
            if _match(doc, query):
                return _project(doc, projection)
        return None

    def find(self, query: dict, projection: dict | None = None):
        out = []
        for doc in self.docs:
            if _match(doc, query):
                out.append(_project(doc, projection))
        return _Cursor(out)

    async def update_one(self, query: dict, update: dict) -> MagicResult:
        for doc in self.docs:
            if _match(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return MagicResult(1)
        return MagicResult(0)

    async def update_many(self, query: dict, update: dict) -> MagicResult:
        n = 0
        for doc in self.docs:
            if _match(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                n += 1
        return MagicResult(n)

    async def insert_one(self, doc: dict) -> MagicInsert:
        _id = doc.get("_id") or ObjectId()
        doc = {**doc, "_id": _id}
        self.docs.append(doc)
        return MagicInsert(_id)


def _project(doc: dict, projection: dict | None) -> dict:
    if not projection:
        return doc.copy()
    if projection.get("_id") == 0:
        return {k: v for k, v in doc.items() if k in projection or k == "_id"}
    out = {}
    for k, v in projection.items():
        if v == 1 or v == True:
            out[k] = doc.get(k)
    if "_id" not in out and "_id" in doc:
        out["_id"] = doc["_id"]
    return out


class MagicResult:
    def __init__(self, n: int):
        self.modified_count = n


class MagicInsert:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id
