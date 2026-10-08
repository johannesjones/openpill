#!/usr/bin/env python3
"""
Move global (namespace-less) pills of one category into a namespace.

For adopting namespaces on existing data, e.g. the job tracker's records:
pills written before namespaces existed are global, and a client that starts
sending ``namespace=...`` would no longer see them until they are moved.

Dry run by default; ``--apply`` writes. Pills already in a namespace are left
alone. Embeddings are not touched.

Usage:
  python scripts/move_to_namespace.py --category job_application --namespace jjones/job_tracker
  python scripts/move_to_namespace.py --category job_application --namespace jjones/job_tracker --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from db import close, get_collection  # noqa: E402
from namespaces import exact_filter, format_namespace, parse_namespace  # noqa: E402


async def move(category: str, namespace: list[str], *, apply: bool, col=None) -> int:
    """Return how many pills were (or, in a dry run, would be) moved."""
    col = col or await get_collection()
    query = {"category": category, **exact_filter(None)}
    titles = [doc.get("title", "") async for doc in col.find(query, {"title": 1})]
    for title in titles:
        print(f"  {'move' if apply else 'would move'}: {title}")
    if apply and titles:
        await col.update_many(query, {"$set": {"namespace": list(namespace)}})
    return len(titles)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--category", required=True)
    parser.add_argument("--namespace", required=True, help="e.g. jjones/job_tracker")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args()
    namespace = parse_namespace(args.namespace)
    if not namespace:
        parser.error("--namespace must not be empty")

    async def run() -> int:
        try:
            return await move(args.category, namespace, apply=args.apply)
        finally:
            await close()

    count = asyncio.run(run())
    verb = "Moved" if args.apply else "Would move"
    print(f"{verb} {count} pill(s) in category {args.category!r} to {format_namespace(namespace)!r}.")
    if not args.apply and count:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
