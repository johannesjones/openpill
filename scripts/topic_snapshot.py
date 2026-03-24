#!/usr/bin/env python3
"""
Generate a lightweight topic snapshot from active OpenPill entries.

This script intentionally uses classical NLP-style heuristics
(tokenization + document frequency + term frequency) to provide a cheap,
explainable view over the memory corpus.

Usage:
  python scripts/topic_snapshot.py
  python scripts/topic_snapshot.py --top-terms 15 --per-category 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from topics import build_topic_snapshot


def main() -> None:
    p = argparse.ArgumentParser(description="Classic NLP topic snapshot for OpenPill.")
    p.add_argument("--top-terms", type=int, default=20, help="Number of global terms")
    p.add_argument("--per-category", type=int, default=10, help="Terms per category")
    p.add_argument("--min-doc-freq", type=int, default=2, help="Minimum document frequency")
    p.add_argument("--min-token-len", type=int, default=3, help="Minimum token length")
    args = p.parse_args()

    snapshot = asyncio.run(
        build_topic_snapshot(
            top_terms=max(1, args.top_terms),
            per_category=max(1, args.per_category),
            min_doc_freq=max(1, args.min_doc_freq),
            min_token_len=max(2, args.min_token_len),
        )
    )
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
