"""
Memory Watchdog – event-driven self-healing for Knowledge Pills.

Uses MongoDB Change Streams to react instantly when new pills are inserted.
For each new pill, runs a targeted analysis:

  1. Embedding pre-filter: find near-neighbors in the same category.
  2. LLM analysis: check the new pill + neighbors for contradictions/redundancies.
  3. Auto-consolidate: merge and archive if issues are found.

This is the real-time complement to the periodic deep scan (janitor.py --daemon).
Together they form the autonomous "Durable Memory" agent.

Usage:
    python watchdog.py                                   # run the watchdog
    python watchdog.py --threshold 0.80                  # lower similarity bar
    python watchdog.py --max-neighbors 10                # limit LLM batch size

Environment:
    JANITOR_MODEL                   LiteLLM model (default: gpt-4o-mini)
    EMBEDDING_MODEL                 Embedding model (default: text-embedding-3-small)
    WATCHDOG_SIMILARITY_THRESHOLD   Similarity threshold (default: 0.85)
    MONGO_URI / MONGO_DB            Database connection
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from datetime import datetime

from db import close, get_collection
from embeddings import cosine_similarity
from janitor import (
    analyze_batch,
    apply_consolidation,
    consolidate_pills,
)

DEFAULT_THRESHOLD = float(os.getenv("WATCHDOG_SIMILARITY_THRESHOLD", "0.85"))
DEFAULT_MAX_NEIGHBORS = 15

_shutdown = asyncio.Event()


def _format_pill(doc: dict) -> dict:
    """Prepare a MongoDB doc for LLM analysis (strip embedding, stringify _id)."""
    out = {k: v for k, v in doc.items() if k != "embedding"}
    out["_id"] = str(out["_id"])
    for key in ("created_at", "updated_at", "expires_at"):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return out


async def find_neighbors(
    col, pill_doc: dict, threshold: float, max_neighbors: int
) -> list[dict]:
    """Find same-category active pills whose embedding is close to the new pill."""
    new_embedding = pill_doc.get("embedding")
    if not new_embedding:
        return []

    category = pill_doc.get("category")
    new_id = pill_doc["_id"]

    neighbors = []
    async for doc in col.find(
        {
            "status": "active",
            "category": category,
            "_id": {"$ne": new_id},
            "embedding": {"$exists": True, "$ne": None},
        }
    ):
        score = cosine_similarity(new_embedding, doc["embedding"])
        if score >= threshold:
            neighbors.append((score, doc))

    neighbors.sort(key=lambda t: t[0], reverse=True)
    return [doc for _, doc in neighbors[:max_neighbors]]


async def handle_new_pill(
    col, pill_doc: dict, threshold: float, max_neighbors: int
) -> None:
    """Process a single newly inserted pill."""
    title = pill_doc.get("title", "<untitled>")
    category = pill_doc.get("category", "<none>")

    if pill_doc.get("source", {}).get("reference", "").startswith("janitor:merged:"):
        return

    print(f"\n  [WATCHDOG] New pill: {title!r} ({category})")

    neighbors = await find_neighbors(col, pill_doc, threshold, max_neighbors)
    if not neighbors:
        print(f"    No near-neighbors (threshold={threshold}). Clean.")
        return

    print(f"    Found {len(neighbors)} near-neighbor(s). Running LLM analysis...")

    batch = [_format_pill(pill_doc)] + [_format_pill(n) for n in neighbors]
    analysis = await analyze_batch(batch)

    if not analysis.contradictions and not analysis.redundancies:
        print("    LLM: No issues found. Clean.")
        return

    for c in analysis.contradictions:
        ids = [c.pill_id_a, c.pill_id_b]
        pair = [d for d in batch if d["_id"] in ids]
        titles = [p["title"] for p in pair]
        print(f"    CONTRADICTION: {titles[0]!r} vs {titles[1]!r}")
        print(f"      Reason: {c.explanation}")

        if len(pair) == 2:
            reason = f"watchdog:contradiction: {c.explanation}"
            merged = await consolidate_pills(pair, reason)
            new_id = await apply_consolidation(col, merged, ids, category, reason)
            print(f"      -> Auto-consolidated into: {merged.title!r} (id: {new_id})")

    for r in analysis.redundancies:
        group = [d for d in batch if d["_id"] in r.pill_ids]
        titles = [p["title"] for p in group]
        print(f"    REDUNDANCY: {titles}")
        print(f"      Reason: {r.explanation}")

        if len(group) >= 2:
            reason = f"watchdog:redundancy: {r.explanation}"
            merged = await consolidate_pills(group, reason)
            new_id = await apply_consolidation(col, merged, r.pill_ids, category, reason)
            print(f"      -> Auto-consolidated into: {merged.title!r} (id: {new_id})")


async def run_watchdog(threshold: float, max_neighbors: int) -> None:
    col = await get_collection()

    print(f"\n{'=' * 60}")
    print("  Memory Watchdog (Change Stream)")
    print(f"  similarity threshold: {threshold}")
    print(f"  max neighbors per check: {max_neighbors}")
    print(f"  watching: {col.full_name}")
    print(f"{'=' * 60}")
    print("  Waiting for new pills...\n")

    pipeline = [{"$match": {"operationType": "insert"}}]

    try:
        async with col.watch(pipeline, full_document="updateLookup") as stream:
            while not _shutdown.is_set():
                if not stream.alive:
                    print("  [WATCHDOG] Change stream closed. Reconnecting...")
                    break

                try:
                    change = await asyncio.wait_for(stream.try_next(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if change is None:
                    continue

                pill_doc = change.get("fullDocument")
                if pill_doc is None:
                    continue

                try:
                    await handle_new_pill(col, pill_doc, threshold, max_neighbors)
                except Exception as exc:
                    pill_id = str(pill_doc.get("_id", "?"))
                    print(f"  [WATCHDOG] Error processing {pill_id}: {exc}")

    except asyncio.CancelledError:
        pass

    print("\n  [WATCHDOG] Shut down.")


def _handle_signal(sig: int, _frame) -> None:
    print(f"\n  [WATCHDOG] Received signal {sig}, shutting down...")
    _shutdown.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Memory Watchdog – react to new pills via Change Stream"
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Embedding similarity threshold (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--max-neighbors", "-n",
        type=int,
        default=DEFAULT_MAX_NEIGHBORS,
        help=f"Max neighbors to include in LLM batch (default: {DEFAULT_MAX_NEIGHBORS})",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        asyncio.run(run_watchdog(
            threshold=args.threshold,
            max_neighbors=args.max_neighbors,
        ))
    finally:
        asyncio.run(close())
