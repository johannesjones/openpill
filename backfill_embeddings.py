"""
Backfill embeddings for existing knowledge pills that don't have one yet.

Usage:
    python backfill_embeddings.py              # embed all pills missing embeddings
    python backfill_embeddings.py --category python  # only a specific category
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from db import close, get_collection
from embeddings import embed_text_for_pill, get_embedding


async def backfill(category: str | None = None) -> None:
    col = await get_collection()

    filter_doc: dict = {
        "status": "active",
        "$or": [{"embedding": None}, {"embedding": {"$exists": False}}],
    }
    if category:
        filter_doc["category"] = category

    total = await col.count_documents(filter_doc)
    if total == 0:
        print("All pills already have embeddings.")
        return

    print(f"Backfilling {total} pills...")
    done = 0
    errors = 0

    async for doc in col.find(filter_doc):
        text = embed_text_for_pill(doc["title"], doc["content"])
        try:
            embedding = await get_embedding(text)
            await col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"embedding": embedding}},
            )
            done += 1
            print(f"  [{done}/{total}] {doc['title']}")
        except Exception as exc:
            errors += 1
            print(f"  [ERROR] {doc['title']}: {exc}")

    print(f"\nDone. Embedded: {done}, Errors: {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill embeddings for knowledge pills")
    parser.add_argument("--category", help="Only backfill a specific category")
    args = parser.parse_args()

    try:
        asyncio.run(backfill(category=args.category))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    finally:
        asyncio.run(close())
