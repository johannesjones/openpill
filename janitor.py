"""
Memory Janitor – Durable Memory maintenance for Knowledge Pills.

Scans all active pills grouped by category, uses an LLM (via LiteLLM)
to detect contradictions and redundancies, then either reports findings
(dry-run, the default) or consolidates them into new pills while
archiving the originals.

Follows Peter Steinberger's "Durable Memory" approach: autonomous
self-correction of the agent's long-term knowledge base.

Usage:
    python janitor.py                          # dry-run – report only
    python janitor.py --apply                  # apply consolidations
    python janitor.py --apply --confirm        # ask Y/n before each merge
    python janitor.py --apply --max-ops 5      # cap at 5 consolidations
    python janitor.py --daemon                 # deep scan every 60 min
    python janitor.py --daemon --interval 360  # deep scan every 6 hours

Environment:
    JANITOR_MODEL   LiteLLM model string (default: gpt-4o-mini)
                    Examples: gpt-4o, claude-3-5-sonnet-20241022, ollama/llama3
    JANITOR_MODEL_POLICY       local_only|local_first|external_first (default local_first)
    JANITOR_EXTERNAL_MODEL     Optional external model for policy/escalation
    JANITOR_ESCALATION_ENABLED Enable local_first escalation gate (default false)
    JANITOR_ESCALATION_MIN_GROUP_SIZE  Min group size for consolidation escalation (default 6)
    MONGO_URI       MongoDB connection string
    MONGO_DB        Database name
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from bson import ObjectId
from pydantic import BaseModel, Field

from db import close, get_collection
from models import KnowledgePill, PillRelationKind, PillSource, PillStatus, SourceType
from pill_relations import add_bidirectional_relation, rewire_relations_on_merge

MODEL = os.getenv("JANITOR_MODEL", "gpt-4o-mini")
MODEL_POLICY = os.getenv("JANITOR_MODEL_POLICY", "local_first").strip().lower()
EXTERNAL_MODEL = os.getenv("JANITOR_EXTERNAL_MODEL")
ESCALATION_ENABLED = os.getenv("JANITOR_ESCALATION_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
ESCALATION_MIN_GROUP_SIZE = int(os.getenv("JANITOR_ESCALATION_MIN_GROUP_SIZE", "6"))
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
BATCH_SIZE = 40

# ---------------------------------------------------------------------------
# Pydantic models for structured LLM responses
# ---------------------------------------------------------------------------


class ContradictionPair(BaseModel):
    pill_id_a: str
    pill_id_b: str
    explanation: str


class RedundancyGroup(BaseModel):
    pill_ids: list[str]
    explanation: str


class JanitorAnalysis(BaseModel):
    contradictions: list[ContradictionPair] = Field(default_factory=list)
    redundancies: list[RedundancyGroup] = Field(default_factory=list)


class ConsolidatedPill(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0


@dataclass(frozen=True)
class ModelDecision:
    model: str
    policy: str
    escalated: bool
    reason: str


# ---------------------------------------------------------------------------
# LLM interaction
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = """\
You are a Memory Janitor for an AI agent's long-term knowledge base.
You will receive a JSON array of knowledge pills (each with an _id, title, content, and tags).

Your task:
1. Identify CONTRADICTIONS – pairs of pills whose facts directly conflict.
2. Identify REDUNDANCIES – groups of pills that express essentially the same knowledge.

Return ONLY valid JSON matching this schema (no markdown, no explanation outside the JSON):
{
  "contradictions": [
    {"pill_id_a": "<id>", "pill_id_b": "<id>", "explanation": "<why they conflict>"}
  ],
  "redundancies": [
    {"pill_ids": ["<id>", "<id>", ...], "explanation": "<why they are redundant>"}
  ]
}

If there are no issues, return: {"contradictions": [], "redundancies": []}
Be conservative – only flag genuine conflicts and clear duplicates.\
"""

CONSOLIDATION_SYSTEM_PROMPT = """\
You are a Memory Janitor. You will receive a group of knowledge pills that are \
either contradictory or redundant, along with the reason.

Your task: produce a single consolidated knowledge pill that:
- For CONTRADICTIONS: resolves the conflict by stating the most accurate/current fact, \
noting the nuance if both sides have merit.
- For REDUNDANCIES: merges all information into one concise pill without losing detail.

Return ONLY valid JSON matching this schema (no markdown, no explanation outside the JSON):
{
  "title": "<concise title>",
  "content": "<the consolidated fact>",
  "tags": ["<merged tags>"],
  "confidence": <0.0-1.0, lower if the resolution is uncertain>
}\
"""


def _resolve_model_for_task(*, task_name: str, group_size: int) -> ModelDecision:
    global _ab_external_calls_used
    local_model = MODEL
    external_model = (EXTERNAL_MODEL or "").strip()
    escalation_allowed = (
        ESCALATION_ENABLED
        and bool(external_model)
        and group_size >= ESCALATION_MIN_GROUP_SIZE
        and task_name == "consolidation"
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


async def _llm_call(system: str, user: str, *, model: str | None = None) -> str:
    from litellm import acompletion

    response = await acompletion(
        model=model or MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


async def analyze_batch(pills: list[dict]) -> JanitorAnalysis:
    """Send a batch of pills to the LLM and parse the analysis."""
    payload = json.dumps(pills, ensure_ascii=False, default=str)
    decision = _resolve_model_for_task(task_name="analysis", group_size=len(pills))
    raw = await _llm_call(ANALYSIS_SYSTEM_PROMPT, payload, model=decision.model)
    return JanitorAnalysis.model_validate_json(raw)


async def consolidate_pills(
    pills: list[dict], reason: str
) -> ConsolidatedPill:
    """Ask the LLM to merge a group of pills into one."""
    payload = json.dumps(
        {"reason": reason, "pills": pills}, ensure_ascii=False, default=str
    )
    decision = _resolve_model_for_task(
        task_name="consolidation", group_size=len(pills)
    )
    raw = await _llm_call(CONSOLIDATION_SYSTEM_PROMPT, payload, model=decision.model)
    return ConsolidatedPill.model_validate_json(raw)


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------


async def fetch_pills_by_category(col) -> dict[str, list[dict]]:
    """Load all active pills, grouped by category."""
    groups: dict[str, list[dict]] = defaultdict(list)
    cursor = col.find({"status": "active"}, {"embedding": 0})
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        groups[doc["category"]].append(doc)
    return groups


def chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _pills_by_ids(pills: list[dict], ids: list[str]) -> list[dict]:
    id_set = set(ids)
    return [p for p in pills if p["_id"] in id_set]


async def write_audit_log(
    col, action: str, original_ids: list[str], new_id: str, reason: str
) -> None:
    """Write an entry to the janitor_audit_log collection."""
    db = col.database
    log_col = db["janitor_audit_log"]
    await log_col.insert_one({
        "action": action,
        "original_ids": original_ids,
        "new_pill_id": new_id,
        "reason": reason,
        "model": MODEL,
        "timestamp": datetime.now(timezone.utc),
    })


def prompt_confirm(message: str) -> bool:
    """Ask the user for Y/n confirmation. Returns True for yes."""
    answer = input(f"  {message} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


async def apply_consolidation(
    col,
    consolidated: ConsolidatedPill,
    original_ids: list[str],
    category: str,
    reason: str = "",
) -> str:
    """Insert the merged pill, archive the originals, and write an audit log."""
    pill = KnowledgePill(
        title=consolidated.title,
        content=consolidated.content,
        category=category,
        tags=consolidated.tags,
        source=PillSource(
            type=SourceType.DOCUMENT,
            reference=f"janitor:merged:{','.join(original_ids)}",
        ),
        confidence=consolidated.confidence,
    )
    result = await col.insert_one(pill.to_mongo())
    new_id = str(result.inserted_id)

    await col.update_many(
        {"_id": {"$in": [ObjectId(oid) for oid in original_ids]}},
        {"$set": {"status": PillStatus.ARCHIVED.value}},
    )

    await rewire_relations_on_merge(col, original_ids, new_id)

    await write_audit_log(col, "consolidation", original_ids, new_id, reason)
    return new_id


# ---------------------------------------------------------------------------
# Main janitor loop
# ---------------------------------------------------------------------------


async def run_janitor(
    dry_run: bool = True,
    confirm: bool = False,
    max_ops: int | None = None,
) -> None:
    col = await get_collection()
    categories = await fetch_pills_by_category(col)

    total_pills = sum(len(v) for v in categories.values())
    print(f"\n{'=' * 60}")
    print(f"  Memory Janitor  |  model: {MODEL}")
    print(f"  model_policy: {MODEL_POLICY}")
    print(f"  {total_pills} active pills across {len(categories)} categories")
    mode_str = "DRY RUN (no changes)" if dry_run else "APPLY"
    if not dry_run and confirm:
        mode_str += " (interactive confirm)"
    if max_ops is not None:
        mode_str += f" (max {max_ops} ops)"
    print(f"  mode: {mode_str}")
    print(f"{'=' * 60}\n")

    if total_pills < 2:
        print("Not enough pills to compare. Exiting.")
        return

    total_contradictions = 0
    total_redundancies = 0
    total_consolidated = 0
    ops_remaining = max_ops

    for category, pills in sorted(categories.items()):
        if len(pills) < 2:
            continue
        if ops_remaining is not None and ops_remaining <= 0:
            print("Max operations reached. Stopping.")
            break

        print(f"--- Category: {category} ({len(pills)} pills) ---")

        for batch in chunk(pills, BATCH_SIZE):
            analysis = await analyze_batch(batch)

            for c in analysis.contradictions:
                total_contradictions += 1
                pair = _pills_by_ids(batch, [c.pill_id_a, c.pill_id_b])
                titles = [p["title"] for p in pair]
                print(f"  CONTRADICTION: {titles[0]!r} vs {titles[1]!r}")
                print(f"    Reason: {c.explanation}")

                if not dry_run and len(pair) == 2:
                    # Persist contradiction edges so retrieval can surface consistency warnings.
                    await add_bidirectional_relation(
                        col,
                        c.pill_id_a,
                        c.pill_id_b,
                        kind=PillRelationKind.CONFLICTS_WITH,
                    )
                    if ops_remaining is not None and ops_remaining <= 0:
                        print("    -> Skipped (max operations reached)")
                        continue
                    if confirm and not prompt_confirm("Consolidate this contradiction?"):
                        print("    -> Skipped by user.")
                        continue
                    reason = f"contradiction: {c.explanation}"
                    merged = await consolidate_pills(pair, reason)
                    new_id = await apply_consolidation(
                        col, merged, [c.pill_id_a, c.pill_id_b], category, reason
                    )
                    total_consolidated += 1
                    if ops_remaining is not None:
                        ops_remaining -= 1
                    print(f"    -> Consolidated into: {merged.title!r} (id: {new_id})")
                    print(f"    -> Archived originals: {c.pill_id_a}, {c.pill_id_b}")

            for r in analysis.redundancies:
                total_redundancies += 1
                group = _pills_by_ids(batch, r.pill_ids)
                titles = [p["title"] for p in group]
                print(f"  REDUNDANCY: {titles}")
                print(f"    Reason: {r.explanation}")

                if not dry_run and len(group) >= 2:
                    if ops_remaining is not None and ops_remaining <= 0:
                        print("    -> Skipped (max operations reached)")
                        continue
                    if confirm and not prompt_confirm("Consolidate this redundancy?"):
                        print("    -> Skipped by user.")
                        continue
                    reason = f"redundancy: {r.explanation}"
                    merged = await consolidate_pills(group, reason)
                    new_id = await apply_consolidation(
                        col, merged, r.pill_ids, category, reason
                    )
                    total_consolidated += 1
                    if ops_remaining is not None:
                        ops_remaining -= 1
                    print(f"    -> Consolidated into: {merged.title!r} (id: {new_id})")
                    print(f"    -> Archived originals: {', '.join(r.pill_ids)}")

        if not analysis.contradictions and not analysis.redundancies:
            print("  No issues found.")
        print()

    print(f"{'=' * 60}")
    print("  Summary")
    print(f"  Contradictions found: {total_contradictions}")
    print(f"  Redundancies found:   {total_redundancies}")
    if not dry_run:
        print(f"  Pills consolidated:   {total_consolidated}")
        print("  Audit log written to: janitor_audit_log collection")
    else:
        print("  Re-run with --apply to consolidate.")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Daemon mode – periodic deep scans
# ---------------------------------------------------------------------------

DEFAULT_INTERVAL_MIN = 60


async def run_daemon(
    interval_min: int = DEFAULT_INTERVAL_MIN,
    max_ops: int | None = None,
) -> None:
    """Run the janitor in a loop, sleeping between full deep scans."""
    cycle = 0
    print(f"\n{'=' * 60}")
    print(f"  Memory Janitor DAEMON  |  model: {MODEL}")
    print(f"  interval: {interval_min} min  |  max ops/cycle: {max_ops or 'unlimited'}")
    print(f"{'=' * 60}\n")

    while True:
        cycle += 1
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"--- Daemon cycle {cycle} at {now} ---")

        try:
            await run_janitor(dry_run=False, confirm=False, max_ops=max_ops)
        except Exception as exc:
            print(f"  [DAEMON] Error in cycle {cycle}: {exc}")

        print(f"  Sleeping {interval_min} min until next cycle...\n")
        await asyncio.sleep(interval_min * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Memory Janitor – scan knowledge pills for contradictions and redundancies"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply consolidations (default is dry-run / report only)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ask for Y/n confirmation before each consolidation (requires --apply)",
    )
    parser.add_argument(
        "--max-ops",
        type=int,
        default=None,
        help="Maximum number of consolidation operations per run",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as a daemon with periodic deep scans (implies --apply)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_MIN,
        help=f"Minutes between daemon cycles (default: {DEFAULT_INTERVAL_MIN})",
    )
    args = parser.parse_args()

    try:
        if args.daemon:
            asyncio.run(run_daemon(
                interval_min=args.interval,
                max_ops=args.max_ops,
            ))
        else:
            asyncio.run(run_janitor(
                dry_run=not args.apply,
                confirm=args.confirm,
                max_ops=args.max_ops,
            ))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    finally:
        asyncio.run(close())
