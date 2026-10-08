"""Minimal in-memory async collection for unit tests (subset of Motor API)."""

from __future__ import annotations

from bson import ObjectId


_MISSING = object()


def _get_path(doc: dict, path: str):
    """Resolve ``a.b`` / ``namespace.0`` paths; ``_MISSING`` when absent."""
    cur = doc
    for part in path.split("."):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if idx >= len(cur):
                return _MISSING
            cur = cur[idx]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _match(doc: dict, q: dict) -> bool:
    if not q:
        return True
    for k, v in q.items():
        if k == "$text":
            return False  # no text index in the fake
        if k == "$or":
            if not any(_match(doc, sub) for sub in v):
                return False
            continue
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
        elif isinstance(v, dict) and any(op.startswith("$") for op in v):
            found = _get_path(doc, k)
            val = None if found is _MISSING else found
            if "$exists" in v and (found is not _MISSING) != bool(v["$exists"]):
                return False
            if "$ne" in v and val == v["$ne"]:
                return False
            if "$lte" in v and (val is None or not val <= v["$lte"]):
                return False
            if "$gt" in v and (val is None or not val > v["$gt"]):
                return False
            if "$in" in v and val not in v["$in"]:
                return False
        else:
            found = _get_path(doc, k)
            if (None if found is _MISSING else found) != v:
                return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = list(docs)
        self._i = 0

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

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
                _apply_update(doc, update)
                return MagicResult(1)
        return MagicResult(0)

    async def update_many(self, query: dict, update: dict) -> MagicResult:
        n = 0
        for doc in self.docs:
            if _match(doc, query):
                _apply_update(doc, update)
                n += 1
        return MagicResult(n)

    async def insert_one(self, doc: dict) -> MagicInsert:
        _id = doc.get("_id") or ObjectId()
        doc = {**doc, "_id": _id}
        self.docs.append(doc)
        return MagicInsert(_id)


def _apply_update(doc: dict, update: dict) -> None:
    doc.update(update.get("$set", {}))
    for key, value in update.get("$addToSet", {}).items():
        values = list(doc.get(key) or [])
        if value not in values:
            values.append(value)
        doc[key] = values
    for key, spec in update.get("$push", {}).items():
        items = spec["$each"] if isinstance(spec, dict) and "$each" in spec else [spec]
        values = list(doc.get(key) or []) + list(items)
        if isinstance(spec, dict) and "$slice" in spec:
            values = values[spec["$slice"]:] if spec["$slice"] < 0 else values[: spec["$slice"]]
        doc[key] = values


def _project(doc: dict, projection: dict | None) -> dict:
    if not projection:
        return doc.copy()
    if all(v in (0, False) for v in projection.values()):
        return {k: v for k, v in doc.items() if k not in projection}
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
        self.matched_count = n


class MagicInsert:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id
