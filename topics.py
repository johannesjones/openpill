"""
Classical NLP topic snapshot helpers for OpenPill.

This module provides a lightweight, dependency-free topic overview over active
memory entries, based on tokenization + TF/DF heuristics.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from db import get_collection

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "using",
    "used",
    "when",
    "where",
    "what",
    "which",
    "your",
    "have",
    "has",
    "are",
    "was",
    "were",
    "will",
    "can",
    "not",
    "but",
    "all",
    "any",
    "more",
    "than",
    "their",
    "about",
    "after",
    "before",
    "also",
    "over",
    "under",
    "between",
    "through",
    "http",
    "https",
    "www",
    "com",
}


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in TOKEN_RE.finditer(text.lower()):
        t = m.group(0)
        if t in STOPWORDS:
            continue
        out.append(t)
    return out


async def build_topic_snapshot(
    *,
    top_terms: int = 20,
    per_category: int = 10,
    min_doc_freq: int = 2,
    min_token_len: int = 3,
) -> dict:
    col = await get_collection()
    docs = []
    async for d in col.find({"status": "active"}, {"title": 1, "content": 1, "category": 1}):
        docs.append(d)

    corpus_df: Counter[str] = Counter()
    corpus_tf: Counter[str] = Counter()
    category_tf: dict[str, Counter[str]] = defaultdict(Counter)

    for d in docs:
        category = d.get("category") or "uncategorized"
        text = f"{d.get('title', '')} {d.get('content', '')}"
        tokens = [t for t in _tokenize(text) if len(t) >= min_token_len]
        if not tokens:
            continue
        unique = set(tokens)
        for t in unique:
            corpus_df[t] += 1
        for t in tokens:
            corpus_tf[t] += 1
            category_tf[category][t] += 1

    eligible_terms = {t for t, df in corpus_df.items() if df >= min_doc_freq}
    global_rank = []
    for t in eligible_terms:
        score = corpus_tf[t] * (1 + corpus_df[t])
        global_rank.append((t, score, corpus_tf[t], corpus_df[t]))
    global_rank.sort(key=lambda x: x[1], reverse=True)

    by_category = {}
    for cat, tf_counter in sorted(category_tf.items()):
        rows = []
        for t, tf in tf_counter.items():
            if t not in eligible_terms:
                continue
            salience = tf / max(corpus_tf[t], 1)
            rows.append((t, salience, tf))
        rows.sort(key=lambda x: (x[1], x[2]), reverse=True)
        by_category[cat] = [
            {"term": t, "salience": round(s, 4), "tf": tf}
            for t, s, tf in rows[:per_category]
        ]

    return {
        "summary": {
            "active_docs": len(docs),
            "vocab_size": len(corpus_tf),
            "eligible_terms": len(eligible_terms),
            "min_doc_freq": min_doc_freq,
            "min_token_len": min_token_len,
        },
        "top_terms": [
            {"term": t, "score": score, "tf": tf, "df": df}
            for t, score, tf, df in global_rank[:top_terms]
        ],
        "topics_by_category": by_category,
    }
