"""
Memory-quality eval for semantic retrieval (``retrieval.semantic_retrieve``).

Loads ``evals/retrieval_cases.json``: a small keyed corpus plus labeled
questions by type (single_hop, multi_hop, temporal, knowledge_update,
abstention), modelled on LoCoMo / LongMemEval question types.

A case passes when:
  - recall types: an expected pill is in the top ``k`` (default 3) and every
    ``stale`` pill ranks below it (or is absent);
  - abstention: retrieval returns nothing.

Embedders:
  hash  deterministic bag-of-words feature hashing; offline, CI-safe. Measures
        the pipeline (floor, freshness, superseded penalty, expansion), not
        semantic understanding.
  live  the configured EMBEDDING_MODEL via LiteLLM (e.g. Ollama). Real quality.

Usage:
    python evals/retrieval_eval.py                       # hash embedder
    python evals/retrieval_eval.py --embedder live       # needs Ollama/provider
    python evals/retrieval_eval.py --min-similarity 0.55 # override the floor
    python evals/retrieval_eval.py --check               # fail below baseline
    python evals/retrieval_eval.py --verbose             # print failing cases
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bson import ObjectId  # noqa: E402

from embeddings import embed_text_for_pill  # noqa: E402
from retrieval import semantic_retrieve  # noqa: E402
from tests.fakes import FakeCollection  # noqa: E402

CASES_FILE = Path(__file__).resolve().parent / "retrieval_cases.json"
TYPES = ("single_hop", "multi_hop", "temporal", "knowledge_update", "abstention")
HASH_DIM = 4096
_STOPWORDS = {
    "the", "and", "for", "are", "was", "with", "that", "this", "what", "which",
    "where", "when", "how", "did", "does", "should", "from", "into", "now", "not",
    "have", "has", "had", "about", "after", "before", "there", "their", "them",
    "you", "your", "our", "can", "will", "would", "its", "but", "any", "all",
    "get", "got", "use", "used", "uses", "who", "why", "out", "per",
}


# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def hash_embed(text: str) -> list[float]:
    """Deterministic bag-of-words embedding (md5 feature hashing, L2-normalized)."""
    vec = [0.0] * HASH_DIM
    for raw in re.findall(r"[a-z0-9]+", text.lower()):
        if len(raw) < 3 or raw in _STOPWORDS:
            continue
        digest = hashlib.md5(_stem(raw).encode("utf-8")).digest()
        vec[int.from_bytes(digest[:4], "big") % HASH_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def _embedder(name: str):
    if name == "hash":

        async def embed(text: str) -> list[float]:
            return hash_embed(text)

        return embed
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from embeddings import get_embedding

    return get_embedding


# ---------------------------------------------------------------------------
# Corpus and run
# ---------------------------------------------------------------------------


async def build_collection(spec: dict, embed) -> tuple[FakeCollection, dict[str, str]]:
    now = datetime.now(timezone.utc)
    ids = {p["key"]: ObjectId() for p in spec["pills"]}
    col = FakeCollection()
    for p in spec["pills"]:
        updated = now - timedelta(days=float(p.get("age_days", 0)))
        col.docs.append(
            {
                "_id": ids[p["key"]],
                "title": p["title"],
                "content": p["content"],
                "category": p["category"],
                "tags": [],
                "status": "active",
                "confidence": float(p.get("confidence", 0.9)),
                "created_at": updated,
                "updated_at": updated,
                "relations": [
                    {"target_id": str(ids[r["target"]]), "kind": r.get("kind", "related")}
                    for r in p.get("relations", [])
                ],
                "embedding": await embed(embed_text_for_pill(p["title"], p["content"])),
            }
        )
    return col, {str(oid): key for key, oid in ids.items()}


def _judge(case: dict, ranked_keys: list[str]) -> tuple[bool, float]:
    """Return (passed, reciprocal_rank) for one case."""
    expect = case["expect"]
    if not expect:
        return (not ranked_keys), 0.0
    first = next((i for i, k in enumerate(ranked_keys) if k in expect), None)
    if first is None:
        return False, 0.0
    rr = 1.0 / (first + 1)
    if first >= int(case.get("k", 3)):
        return False, rr
    for stale in case.get("stale", []):
        if stale in ranked_keys and ranked_keys.index(stale) < first:
            return False, rr
    return True, rr


async def run_eval(embedder: str = "hash", min_similarity: float | None = None) -> dict:
    spec = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    assert spec.get("version") == 1
    if min_similarity is None:
        min_similarity = (spec.get("settings", {}).get(embedder) or {}).get("min_similarity")
    embed = await _embedder(embedder)
    col, key_by_id = await build_collection(spec, embed)

    per_type: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    failures: list[dict] = []
    for case in spec["cases"]:
        params = {"limit": 10, **case.get("params", {})}
        result = await semantic_retrieve(
            col,
            case["query"],
            await embed(case["query"]),
            min_similarity=min_similarity,
            **params,
        )
        ranked = [key_by_id.get(p["_id"], "?") for p in result["pills"]]
        passed, rr = _judge(case, ranked)
        per_type[case["type"]].append((passed, rr))
        if not passed:
            failures.append({"id": case["id"], "query": case["query"], "expect": case["expect"], "got": ranked[:5]})

    summary = {}
    for t in TYPES:
        rows = per_type.get(t, [])
        if not rows:
            continue
        summary[t] = {
            "cases": len(rows),
            "pass_rate": round(sum(p for p, _ in rows) / len(rows), 3),
            "mrr": round(sum(rr for _, rr in rows) / len(rows), 3) if t != "abstention" else None,
        }
    return {
        "embedder": embedder,
        "min_similarity": min_similarity,
        "summary": summary,
        "failures": failures,
        "baseline": spec.get("baseline", {}).get(embedder, {}),
    }


def below_baseline(report: dict) -> list[str]:
    return [
        f"{t}: {report['summary'][t]['pass_rate']} < {floor}"
        for t, floor in report["baseline"].items()
        if t in report["summary"] and report["summary"][t]["pass_rate"] < floor
    ]


def _print(report: dict, verbose: bool) -> None:
    print(f"embedder={report['embedder']}  min_similarity={report['min_similarity']}")
    print(f"{'type':<18}{'cases':>6}{'pass':>8}{'MRR':>8}")
    for t, row in report["summary"].items():
        mrr = "-" if row["mrr"] is None else f"{row['mrr']:.3f}"
        print(f"{t:<18}{row['cases']:>6}{row['pass_rate']:>8.3f}{mrr:>8}")
    if verbose and report["failures"]:
        print("\nfailures:")
        for f in report["failures"]:
            print(f"  {f['id']}: {f['query']!r} expect={f['expect']} got={f['got']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--embedder", choices=("hash", "live"), default="hash")
    parser.add_argument("--min-similarity", type=float, default=None)
    parser.add_argument("--check", action="store_true", help="exit 1 if below the baseline")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(run_eval(args.embedder, args.min_similarity))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report, args.verbose)
    if args.check:
        regressions = below_baseline(report)
        if regressions:
            print("\nbelow baseline: " + "; ".join(regressions))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
