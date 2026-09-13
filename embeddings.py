"""
Shared embedding utilities for Knowledge Pills.

Uses LiteLLM's embedding API so the provider is switchable via env var:
  EMBEDDING_MODEL=text-embedding-3-small      (OpenAI, default)
  EMBEDDING_MODEL=ollama/nomic-embed-text      (local Ollama)
  EMBEDDING_MODEL=cohere/embed-english-v3.0    (Cohere)
"""

from __future__ import annotations

import logging
import math
import os

logger = logging.getLogger("openpill.embeddings")

DEFAULT_MODEL = "text-embedding-3-small"

_warned_dimension_mismatch = False


def embedding_model() -> str:
    """Resolve the model per call so a .env loaded after import still applies."""
    return os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)


async def get_embedding(text: str) -> list[float]:
    """Embed a single text string and return the vector."""
    # Lazy import keeps module import lightweight for test collection/CI.
    from litellm import aembedding

    response = await aembedding(model=embedding_model(), input=[text])
    return response.data[0]["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors without numpy.

    Vectors of different lengths come from different embedding models and are not
    comparable, so they score as no match. Without this guard zip() would silently
    compare a truncated prefix and return a plausible but meaningless number, which
    turns an EMBEDDING_MODEL change into corrupted search results rather than an error.
    """
    if len(a) != len(b):
        _warn_dimension_mismatch(len(a), len(b))
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _warn_dimension_mismatch(query_dim: int, stored_dim: int) -> None:
    """Warn once per process; this is called from inside per-pill scoring loops."""
    global _warned_dimension_mismatch
    if _warned_dimension_mismatch:
        return
    _warned_dimension_mismatch = True
    logger.warning(
        "Embedding dimension mismatch (query=%d, stored=%d) with EMBEDDING_MODEL=%s. "
        "Pills embedded by a different model cannot be compared and will never match. "
        "Re-embed them after changing the model.",
        query_dim,
        stored_dim,
        embedding_model(),
    )


def embed_text_for_pill(title: str, content: str) -> str:
    """Build the text representation used for embedding a pill."""
    return f"{title}\n{content}"

