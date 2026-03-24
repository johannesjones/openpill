"""
Knowledge Pill Extractor – auto-distill facts from raw text.

Takes chat transcripts, markdown notes, or any free-form text and uses an
LLM to extract atomic knowledge pills. Checks for near-duplicates via
embedding similarity before inserting.

Usage:
    python extractor.py --file path/to/notes.md
    python extractor.py --file transcript.txt --dry-run
    cat notes.md | python extractor.py --stdin
    python extractor.py --file notes.md --min-confidence 0.7

Environment:
    EXTRACTOR_MODEL    LiteLLM model string (default: gpt-4o-mini)
    EXTRACTOR_MODEL_POLICY               local_only|local_first|external_first (default local_first)
    EXTRACTOR_EXTERNAL_MODEL             Optional external model for policy/escalation
    EXTRACTOR_ESCALATION_ENABLED         Enable local_first escalation gate (default false)
    EXTRACTOR_ESCALATION_MIN_TEXT_LEN    Min transcript length for escalation (default 8000)
    EMBEDDING_MODEL    Embedding model (default: text-embedding-3-small)
    EXTRACTOR_DUPLICATE_THRESHOLD         Similarity threshold for dedup (default 0.92).
    EXTRACTOR_CONVERSATION_DUPLICATE_THRESHOLD  Stricter threshold for conversation pills (default 0.95).
    EXTRACTOR_MAX_PILLS                   Max pills per run (default 50).
    EXTRACTOR_MIN_PILL_TITLE_LEN          Min title length to accept (default 3).
    EXTRACTOR_MIN_PILL_CONTENT_LEN        Min content length to accept (default 15).
    EXTRACTOR_CONVERSATION_SUMMARY_CHARS  Summary length range as MIN,MAX (default 600,1200).
    EXTRACTOR_CONVERSATION_EXCERPT_TRANSCRIPT_MIN_LEN  Min transcript length to add excerpt (default 2000).
    EXTRACTOR_CONVERSATION_EXCERPT_LEN    First/last N chars for excerpt (default 500).
    EXTRACTOR_RELATED_THRESHOLD           Min similarity to auto-link related pills (default 0.88).
    EXTRACTOR_RELATED_MAX_LINKS           Max neighbor links per new pill (default 3).
    EXTRACTOR_LINK_ON_INSERT              If true, link new pills to similar same-category neighbors (default true).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass

from litellm import acompletion
from pydantic import BaseModel, Field, ValidationError

from db import close, get_collection
from embeddings import cosine_similarity, embed_text_for_pill, get_embedding
from models import KnowledgePill, PillRelationKind, PillSource, SourceType
from pill_relations import add_bidirectional_relation, find_related_candidates

MODEL = os.getenv("EXTRACTOR_MODEL", "gpt-4o-mini")
MODEL_POLICY = os.getenv("EXTRACTOR_MODEL_POLICY", "local_first").strip().lower()
EXTERNAL_MODEL = os.getenv("EXTRACTOR_EXTERNAL_MODEL")
ESCALATION_ENABLED = os.getenv("EXTRACTOR_ESCALATION_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
ESCALATION_MIN_TEXT_LEN = int(os.getenv("EXTRACTOR_ESCALATION_MIN_TEXT_LEN", "8000"))
AB_GUARDS_ENABLED = os.getenv("AB_GUARDS_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
AB_ALLOW_EXTERNAL = os.getenv("AB_ALLOW_EXTERNAL", "false").lower() in (
    "1",
    "true",
    "yes",
)
AB_MAX_EXTERNAL_CALLS = int(os.getenv("AB_MAX_EXTERNAL_CALLS", "0"))
_ab_external_calls_used = 0
ALLOWED_MODEL_POLICIES = {"local_only", "local_first", "external_first"}
if MODEL_POLICY not in ALLOWED_MODEL_POLICIES:
    MODEL_POLICY = "local_first"

# Canonical categories for normalization; LLM output is mapped to these or "other"
ALLOWED_CATEGORIES = (
    "python",
    "javascript",
    "architecture",
    "devops",
    "databases",
    "security",
    "ai",
    "networking",
    "other",
)
_CATEGORY_ALIASES: dict[str, str] = {
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "arch": "architecture",
    "db": "databases",
    "ml": "ai",
    "machine learning": "ai",
    "infra": "devops",
    "ops": "devops",
    "sec": "security",
    "net": "networking",
}


def _get_duplicate_threshold(for_conversation: bool = False) -> float:
    """Deduplication similarity threshold; conversation path can use a stricter default."""
    if for_conversation:
        raw = os.getenv("EXTRACTOR_CONVERSATION_DUPLICATE_THRESHOLD")
        if raw is not None:
            return float(raw)
        raw = os.getenv("EXTRACTOR_DUPLICATE_THRESHOLD")
        if raw is not None:
            return float(raw)
        return 0.95
    raw = os.getenv("EXTRACTOR_DUPLICATE_THRESHOLD")
    return float(raw) if raw is not None else 0.92


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    return int(raw) if raw is not None else default


def _env_range(key: str, default_lo: int, default_hi: int) -> tuple[int, int]:
    """Parse MIN_CHARS,MAX_CHARS from env (e.g. CONVERSATION_SUMMARY_CHARS=600,1200)."""
    raw = os.getenv(key)
    if not raw:
        return (default_lo, default_hi)
    parts = raw.split(",")
    if len(parts) != 2:
        return (default_lo, default_hi)
    try:
        lo, hi = int(parts[0].strip()), int(parts[1].strip())
        if lo <= hi:
            return (lo, hi)
    except ValueError:
        pass
    return (default_lo, default_hi)


DUPLICATE_THRESHOLD = _get_duplicate_threshold(for_conversation=False)
MAX_PILLS_PER_RUN = _env_int("EXTRACTOR_MAX_PILLS", 50)
MIN_PILL_TITLE_LEN = _env_int("EXTRACTOR_MIN_PILL_TITLE_LEN", 3)
MIN_PILL_CONTENT_LEN = _env_int("EXTRACTOR_MIN_PILL_CONTENT_LEN", 15)
CONVERSATION_SUMMARY_MIN_CHARS, CONVERSATION_SUMMARY_MAX_CHARS = _env_range(
    "EXTRACTOR_CONVERSATION_SUMMARY_CHARS", 600, 1200
)
CONVERSATION_EXCERPT_TRANSCRIPT_MIN_LEN = _env_int(
    "EXTRACTOR_CONVERSATION_EXCERPT_TRANSCRIPT_MIN_LEN", 2000
)
CONVERSATION_EXCERPT_LEN = _env_int("EXTRACTOR_CONVERSATION_EXCERPT_LEN", 500)


def _get_related_threshold() -> float:
    raw = os.getenv("EXTRACTOR_RELATED_THRESHOLD")
    return float(raw) if raw is not None else 0.88


def _get_related_max_links() -> int:
    return _env_int("EXTRACTOR_RELATED_MAX_LINKS", 3)


def _link_on_insert() -> bool:
    return os.getenv("EXTRACTOR_LINK_ON_INSERT", "true").lower() in ("1", "true", "yes")


def normalize_category(category: str) -> str:
    """Map LLM category to a canonical allowed value or 'other'."""
    if not category or not category.strip():
        return "other"
    c = category.strip().lower()
    if c in ALLOWED_CATEGORIES:
        return c
    return _CATEGORY_ALIASES.get(c, "other")


def adjust_confidence(fact: "ExtractedFact") -> float:
    """
    Lightweight confidence re-scoring: boost for concrete signals (versions, tools),
    slight penalty for very short or generic-looking content.
    """
    conf = max(0.0, min(1.0, fact.confidence))
    content = (fact.title + " " + fact.content).lower()
    # Boost: version-like patterns (e.g. 1.2.3, v2, Python 3.11)
    if re.search(r"\b(v?\d+\.\d+(\.\d+)?|\d+\.\d+)\b", content):
        conf = min(1.0, conf + 0.05)
    # Boost: explicit tool/tech names (common patterns)
    if re.search(r"\b(use|using|prefer|chose|chosen)\s+[\w\-]+\b", content):
        conf = min(1.0, conf + 0.03)
    # Slight penalty: very short content
    if len(fact.content.strip()) < 40:
        conf = max(0.0, conf - 0.05)
    return round(conf, 2)


# ---------------------------------------------------------------------------
# Pydantic model for LLM extraction response
# ---------------------------------------------------------------------------


class ExtractedFact(BaseModel):
    title: str
    content: str
    category: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    pills: list[ExtractedFact] = Field(default_factory=list)


@dataclass(frozen=True)
class ModelDecision:
    model: str
    policy: str
    escalated: bool
    reason: str


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You are a Knowledge Extractor for an AI agent's long-term memory.
You will receive raw text (chat transcript, notes, documentation, etc.).

Your task: extract atomic, distilled facts as knowledge pills.
Each pill should be a single, self-contained piece of knowledge.

Rules:
- Be selective: only extract genuinely useful, non-obvious facts.
- Each pill must stand alone without needing the original context.
- Assign a confidence score (0.0-1.0) based on how certain the fact is.
- Choose a category from common domains: python, javascript, architecture,
  devops, databases, security, ai, networking, or create a new one if needed.
- Keep titles concise (<100 chars) and content under 500 chars.

Do NOT extract: common knowledge, filler, greetings, or pure code snippets
without explanation. Prefer decisions, gotchas, and conclusions over generic statements.

Example good pill: title "Use EXTRACTOR_DUPLICATE_THRESHOLD for dedup tuning", content "Dedup threshold is configurable via env; conversation path can use a stricter default (e.g. 0.95).", category "devops", confidence 0.9.
Example bad pill: title "Python is a language", content "Python is a programming language.", category "python", confidence 0.5 — too generic.

Return ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "pills": [
    {
      "title": "<concise title>",
      "content": "<the distilled fact>",
      "category": "<category>",
      "tags": ["<tag1>", "<tag2>"],
      "confidence": <0.0-1.0>
    }
  ]
}

If the text contains no extractable knowledge, return: {"pills": []}\
"""

CONVERSATION_EXTRACTION_SYSTEM_PROMPT = """\
You are a Knowledge Extractor for an AI agent's long-term memory.
The input is a SUMMARY of a conversation (not the full transcript).

Your task: extract atomic, distilled facts as knowledge pills. Prefer:
- Decisions and conclusions the user or assistant committed to.
- Gotchas, caveats, and non-obvious tips that came up.
- Concrete choices (tools, versions, patterns) that were decided.

Rules:
- Be selective: only extract genuinely useful, non-obvious facts.
- Each pill must stand alone. Do NOT extract generic statements or small talk.
- Assign confidence (0.0-1.0). Prefer higher confidence for clear decisions.
- Category: python, javascript, architecture, devops, databases, security, ai, networking, or other.
- Titles <100 chars, content under 500 chars.

Do NOT extract: greetings, "we discussed X" without the actual conclusion, or common knowledge.

Good example: title "Use Ollama for conversation extraction when offline", content "API and extractor support EXTRACTOR_MODEL=ollama/llama3; use with EMBEDDING_MODEL=ollama/nomic-embed-text for full local pipeline.", category "ai", confidence 0.9.
Bad example: title "We talked about the project", content "The user and assistant discussed the project.", category "other", confidence 0.3 — no concrete fact.

Return ONLY valid JSON: {"pills": [{"title": "...", "content": "...", "category": "...", "tags": [], "confidence": 0.9}]}
If nothing extractable: {"pills": []}\
"""


def _resolve_model_for_task(
    *,
    task_name: str,
    input_len: int,
) -> ModelDecision:
    """Resolve local-first model routing with optional external escalation."""
    global _ab_external_calls_used
    local_model = MODEL
    external_model = (EXTERNAL_MODEL or "").strip()
    escalation_allowed = (
        ESCALATION_ENABLED
        and bool(external_model)
        and input_len >= ESCALATION_MIN_TEXT_LEN
        and task_name in {"summary", "conversation_extraction"}
    )

    if MODEL_POLICY == "local_only" or not external_model:
        return ModelDecision(
            model=local_model,
            policy=MODEL_POLICY,
            escalated=False,
            reason="local_only_or_no_external_model",
        )
    if AB_GUARDS_ENABLED:
        if not AB_ALLOW_EXTERNAL:
            return ModelDecision(
                model=local_model,
                policy=MODEL_POLICY,
                escalated=False,
                reason="ab_guard_external_not_allowed",
            )
        if AB_MAX_EXTERNAL_CALLS > 0 and _ab_external_calls_used >= AB_MAX_EXTERNAL_CALLS:
            return ModelDecision(
                model=local_model,
                policy=MODEL_POLICY,
                escalated=False,
                reason="ab_guard_external_call_cap_reached",
            )
    if MODEL_POLICY == "external_first":
        if AB_GUARDS_ENABLED:
            _ab_external_calls_used += 1
        return ModelDecision(
            model=external_model,
            policy=MODEL_POLICY,
            escalated=True,
            reason="external_first",
        )
    if escalation_allowed:
        if AB_GUARDS_ENABLED:
            _ab_external_calls_used += 1
        return ModelDecision(
            model=external_model,
            policy=MODEL_POLICY,
            escalated=True,
            reason="local_first_escalation_gate",
        )
    return ModelDecision(
        model=local_model,
        policy=MODEL_POLICY,
        escalated=False,
        reason="local_first_default",
    )


async def extract_facts(
    text: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
) -> list[ExtractedFact]:
    """Send raw text to the LLM and parse extracted facts. Retries once on JSON parse failure."""
    prompt = system_prompt if system_prompt is not None else EXTRACTION_SYSTEM_PROMPT
    messages: list[dict] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text},
    ]
    for attempt in range(2):
        response = await acompletion(
            model=model or MODEL,
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        try:
            result = ExtractionResult.model_validate_json(raw)
            return result.pills
        except (ValidationError, ValueError) as e:
            if attempt == 0:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": "Your previous response was not valid JSON. Return ONLY a single JSON object with a 'pills' array; no markdown, no explanation.",
                })
                continue
            raise ValueError(f"Failed to parse extraction JSON after retry: {e}") from e
    return []


def _conversation_summary_prompt() -> str:
    """Build summary prompt with configurable character range from env."""
    lo, hi = CONVERSATION_SUMMARY_MIN_CHARS, CONVERSATION_SUMMARY_MAX_CHARS
    return f"""\
You are a summarizer for an AI agent's long-term memory.
You will receive a single conversation transcript (user and assistant turns).

Your task:
- Summarize the main topics, decisions, conclusions, and non-obvious facts.
- Focus on durable knowledge that should be remembered beyond this chat.
- Include gotchas, chosen tools/versions, and concrete conclusions.
- Omit small talk, greetings, and one-off details.

Return a plain-text summary (no markdown), between {lo} and {hi} characters.\
"""


async def summarize_transcript(transcript: str, *, model: str | None = None) -> str:
    """Summarize a conversation transcript into a compact description."""
    response = await acompletion(
        model=model or MODEL,
        messages=[
            {"role": "system", "content": _conversation_summary_prompt()},
            {"role": "user", "content": transcript},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


async def find_near_duplicates(
    embedding: list[float], col, threshold: float | None = None
) -> list[dict]:
    """Return existing pills whose embedding is above the similarity threshold."""
    if threshold is None:
        threshold = _get_duplicate_threshold(for_conversation=False)
    duplicates = []
    async for doc in col.find(
        {"status": "active", "embedding": {"$exists": True, "$ne": None}},
        {"title": 1, "embedding": 1},
    ):
        score = cosine_similarity(embedding, doc["embedding"])
        if score >= threshold:
            duplicates.append({"id": str(doc["_id"]), "title": doc["title"], "similarity": round(score, 4)})
    duplicates.sort(key=lambda x: x["similarity"], reverse=True)
    return duplicates


async def run_extraction(
    text: str,
    source_reference: str,
    dry_run: bool = True,
    min_confidence: float = 0.5,
    max_pills: int = MAX_PILLS_PER_RUN,
) -> dict:
    """Extract pills from text, deduplicate, and optionally insert into MongoDB."""
    col = await get_collection()

    print(f"\n{'=' * 60}")
    model_decision = _resolve_model_for_task(task_name="extract", input_len=len(text))
    print(f"  Knowledge Extractor  |  model: {model_decision.model}")
    print(f"  source: {source_reference}")
    print(f"  mode: {'DRY RUN' if dry_run else 'INSERT'}")
    print(f"{'=' * 60}\n")

    facts = await extract_facts(text, model=model_decision.model)
    for f in facts:
        f.category = normalize_category(f.category)
        f.confidence = adjust_confidence(f)
    print(f"Extracted {len(facts)} candidate pills from text.\n")

    inserted = []
    skipped_confidence = []
    skipped_duplicate = []
    skipped_short = []

    for fact in facts[:max_pills]:
        if fact.confidence < min_confidence:
            skipped_confidence.append(fact.title)
            print(f"  SKIP (low confidence {fact.confidence:.2f}): {fact.title}")
            continue
        if len(fact.title.strip()) < MIN_PILL_TITLE_LEN or len(fact.content.strip()) < MIN_PILL_CONTENT_LEN:
            skipped_short.append(fact.title)
            print(f"  SKIP (too short): {fact.title}")
            continue

        embedding = await get_embedding(embed_text_for_pill(fact.title, fact.content))
        dupes = await find_near_duplicates(embedding, col)

        if dupes:
            skipped_duplicate.append(
                {
                    "title": fact.title,
                    "similar_to": dupes[0]["title"],
                    "similar_pill_id": dupes[0]["id"],
                }
            )
            print(f"  SKIP (duplicate of {dupes[0]['title']!r}, sim={dupes[0]['similarity']}): {fact.title}")
            continue

        if dry_run:
            print(f"  WOULD INSERT: {fact.title} [{fact.category}] (conf={fact.confidence:.2f})")
            inserted.append(fact.title)
        else:
            pill = KnowledgePill(
                title=fact.title,
                content=fact.content,
                category=fact.category,
                tags=fact.tags,
                source=PillSource(type=SourceType.DOCUMENT, reference=f"extractor:{source_reference}"),
                confidence=fact.confidence,
                embedding=embedding,
            )
            result = await col.insert_one(pill.to_mongo())
            new_id = result.inserted_id
            inserted.append(fact.title)
            print(f"  INSERTED: {fact.title} (id: {new_id})")
            if _link_on_insert() and _get_related_max_links() > 0:
                dup_threshold = _get_duplicate_threshold(for_conversation=False)
                low = _get_related_threshold()
                if low < dup_threshold:
                    candidates = await find_related_candidates(
                        embedding,
                        col,
                        category=fact.category,
                        low=low,
                        high=dup_threshold,
                        exclude_id=new_id,
                        max_links=_get_related_max_links(),
                    )
                    for c in candidates:
                        await add_bidirectional_relation(
                            col, new_id, c["id"], PillRelationKind.RELATED
                        )
                        print(f"    LINK related → {c['title']!r} (sim={c['similarity']})")

    print(f"\n{'=' * 60}")
    print("  Summary")
    print(f"  {'Would insert' if dry_run else 'Inserted'}: {len(inserted)}")
    print(f"  Skipped (low confidence): {len(skipped_confidence)}")
    print(f"  Skipped (too short):     {len(skipped_short)}")
    print(f"  Skipped (duplicate):     {len(skipped_duplicate)}")
    if dry_run and inserted:
        print("  Re-run without --dry-run to insert.")
    print(f"{'=' * 60}\n")

    return {
        "inserted": inserted,
        "skipped_confidence": skipped_confidence,
        "skipped_duplicate": skipped_duplicate,
        "skipped_short": skipped_short,
        "stats": {"text_length": len(text), "candidates": len(facts)},
        "model": {
            "selected": model_decision.model,
            "policy": model_decision.policy,
            "escalated": model_decision.escalated,
            "reason": model_decision.reason,
        },
    }


async def run_conversation_extraction(
    transcript: str,
    source_reference: str,
    dry_run: bool = True,
    min_confidence: float = 0.5,
    max_pills: int = MAX_PILLS_PER_RUN,
) -> dict:
    """Summarize a conversation and extract pills, deduplicate, and optionally insert."""
    col = await get_collection()

    print(f"\n{'=' * 60}")
    summary_model = _resolve_model_for_task(task_name="summary", input_len=len(transcript))
    extraction_model = _resolve_model_for_task(
        task_name="conversation_extraction", input_len=len(transcript)
    )
    print(
        "  Conversation Extractor  |  "
        f"summary_model: {summary_model.model} | extraction_model: {extraction_model.model}"
    )
    print(f"  source: {source_reference}")
    print(f"  mode: {'DRY RUN' if dry_run else 'INSERT'}")
    print(f"{'=' * 60}\n")

    summary = await summarize_transcript(transcript, model=summary_model.model)
    summary_length = len(summary)
    print("Conversation summary:\n")
    print(summary)
    print("\nExtracting pills from summary...\n")

    extraction_input = summary
    excerpt_used = False
    if len(transcript) > CONVERSATION_EXCERPT_TRANSCRIPT_MIN_LEN:
        excerpt_len = CONVERSATION_EXCERPT_LEN
        excerpt = (
            transcript[:excerpt_len]
            + "\n\n... [middle omitted] ...\n\n"
            + transcript[-excerpt_len:]
        )
        extraction_input = summary + "\n\n[Transcript excerpt for context]\n" + excerpt
        excerpt_used = True

    conv_threshold = _get_duplicate_threshold(for_conversation=True)
    facts = await extract_facts(
        extraction_input,
        system_prompt=CONVERSATION_EXTRACTION_SYSTEM_PROMPT,
        model=extraction_model.model,
    )
    for f in facts:
        f.category = normalize_category(f.category)
        f.confidence = adjust_confidence(f)
    print(f"Extracted {len(facts)} candidate pills from summary.\n")

    inserted = []
    skipped_confidence = []
    skipped_duplicate = []
    skipped_short = []

    for fact in facts[:max_pills]:
        if fact.confidence < min_confidence:
            skipped_confidence.append(fact.title)
            print(f"  SKIP (low confidence {fact.confidence:.2f}): {fact.title}")
            continue
        if len(fact.title.strip()) < MIN_PILL_TITLE_LEN or len(fact.content.strip()) < MIN_PILL_CONTENT_LEN:
            skipped_short.append(fact.title)
            print(f"  SKIP (too short): {fact.title}")
            continue

        embedding = await get_embedding(embed_text_for_pill(fact.title, fact.content))
        dupes = await find_near_duplicates(embedding, col, threshold=conv_threshold)

        if dupes:
            skipped_duplicate.append(
                {
                    "title": fact.title,
                    "similar_to": dupes[0]["title"],
                    "similar_pill_id": dupes[0]["id"],
                }
            )
            print(f"  SKIP (duplicate of {dupes[0]['title']!r}, sim={dupes[0]['similarity']}): {fact.title}")
            continue

        if dry_run:
            print(f"  WOULD INSERT: {fact.title} [{fact.category}] (conf={fact.confidence:.2f})")
            inserted.append(fact.title)
        else:
            pill = KnowledgePill(
                title=fact.title,
                content=fact.content,
                category=fact.category,
                tags=fact.tags,
                source=PillSource(type=SourceType.CHAT, reference=f"conversation:{source_reference}"),
                confidence=fact.confidence,
                embedding=embedding,
            )
            result = await col.insert_one(pill.to_mongo())
            new_id = result.inserted_id
            inserted.append(fact.title)
            print(f"  INSERTED: {fact.title} (id: {new_id})")
            if _link_on_insert() and _get_related_max_links() > 0:
                dup_threshold = conv_threshold
                low = _get_related_threshold()
                if low < dup_threshold:
                    candidates = await find_related_candidates(
                        embedding,
                        col,
                        category=fact.category,
                        low=low,
                        high=dup_threshold,
                        exclude_id=new_id,
                        max_links=_get_related_max_links(),
                    )
                    for c in candidates:
                        await add_bidirectional_relation(
                            col, new_id, c["id"], PillRelationKind.RELATED
                        )
                        print(f"    LINK related → {c['title']!r} (sim={c['similarity']})")

    print(f"\n{'=' * 60}")
    print("  Summary")
    print(f"  {'Would insert' if dry_run else 'Inserted'}: {len(inserted)}")
    print(f"  Skipped (low confidence): {len(skipped_confidence)}")
    print(f"  Skipped (too short):     {len(skipped_short)}")
    print(f"  Skipped (duplicate):     {len(skipped_duplicate)}")
    if dry_run and inserted:
        print("  Re-run without --dry-run to insert.")
    print(f"{'=' * 60}\n")

    return {
        "inserted": inserted,
        "skipped_confidence": skipped_confidence,
        "skipped_duplicate": skipped_duplicate,
        "skipped_short": skipped_short,
        "stats": {
            "transcript_length": len(transcript),
            "summary_length": summary_length,
            "excerpt_used": excerpt_used,
            "candidates": len(facts),
            "summary_model": summary_model.model,
            "extraction_model": extraction_model.model,
            "model_policy": MODEL_POLICY,
            "summary_escalated": summary_model.escalated,
            "extraction_escalated": extraction_model.escalated,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract knowledge pills from raw text"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="Path to text file to extract from")
    source.add_argument("--stdin", action="store_true", help="Read from stdin")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Report only (default)")
    parser.add_argument("--insert", action="store_true", help="Actually insert pills into MongoDB")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="Min confidence threshold (default 0.5)")
    parser.add_argument("--max-pills", type=int, default=MAX_PILLS_PER_RUN, help=f"Max pills per run (default {MAX_PILLS_PER_RUN})")
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
        ref = args.file
    else:
        text = sys.stdin.read()
        ref = "stdin"

    if not text.strip():
        print("No input text provided.")
        sys.exit(1)

    dry_run = not args.insert

    try:
        asyncio.run(run_extraction(
            text=text,
            source_reference=ref,
            dry_run=dry_run,
            min_confidence=args.min_confidence,
            max_pills=args.max_pills,
        ))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    finally:
        asyncio.run(close())
