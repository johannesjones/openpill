"""
Shared embedding utilities for Knowledge Pills.

Uses LiteLLM's embedding API so the provider is switchable via env var:
  EMBEDDING_MODEL=text-embedding-3-small      (OpenAI, default)
  EMBEDDING_MODEL=ollama/nomic-embed-text      (local Ollama)
  EMBEDDING_MODEL=cohere/embed-english-v3.0    (Cohere)
"""

from __future__ import annotations

import math
import os

MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


async def get_embedding(text: str) -> list[float]:
    """Embed a single text string and return the vector."""
    # Lazy import keeps module import lightweight for test collection/CI.
    from litellm import aembedding

    response = await aembedding(model=MODEL, input=[text])
    return response.data[0]["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors without numpy."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_text_for_pill(title: str, content: str) -> str:
    """Build the text representation used for embedding a pill."""
    return f"{title}\n{content}"

