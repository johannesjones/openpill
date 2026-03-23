"""Seed the database with example knowledge pills for testing."""

import asyncio

from db import get_collection
from models import KnowledgePill, PillSource, SourceType


SEEDS = [
    KnowledgePill(
        title="Python GIL and Concurrency",
        content=(
            "Python's Global Interpreter Lock (GIL) prevents true parallel "
            "thread execution for CPU-bound code. For I/O-bound tasks, "
            "threads and asyncio are effective; for CPU-bound tasks use "
            "multiprocessing."
        ),
        category="python",
        tags=["concurrency", "gil", "performance"],
        source=PillSource(type=SourceType.CHAT, reference="wave3-session-001"),
        confidence=0.95,
    ),
    KnowledgePill(
        title="MCP – Model Context Protocol Basics",
        content=(
            "MCP is an open standard by Anthropic that acts as a universal "
            "interface ('API for AIs'). MCP servers expose tools and resources "
            "that any MCP-compatible client (Cursor, Claude Desktop, etc.) "
            "can consume."
        ),
        category="architecture",
        tags=["mcp", "protocol", "ai-tooling"],
        source=PillSource(type=SourceType.DOCUMENT, reference="ai-agent-architecture-wave3.md"),
        confidence=1.0,
    ),
    KnowledgePill(
        title="MongoDB TTL Indexes",
        content=(
            "A TTL index on a datetime field automatically deletes documents "
            "after the specified expireAfterSeconds period. Useful for session "
            "data, logs, or temporary knowledge pills."
        ),
        category="devops",
        tags=["mongodb", "ttl", "indexing"],
        source=PillSource(type=SourceType.MANUAL, reference=""),
        confidence=0.9,
    ),
    KnowledgePill(
        title="Docker Layer Caching",
        content=(
            "Docker builds images layer by layer. If one layer changes, all "
            "subsequent layers are rebuilt. Therefore: copy infrequently "
            "changed dependencies (requirements.txt) BEFORE frequently "
            "changed application code."
        ),
        category="devops",
        tags=["docker", "performance", "best-practices"],
        source=PillSource(type=SourceType.CHAT, reference="wave3-session-003"),
        confidence=1.0,
    ),
    KnowledgePill(
        title="Plan-Execute-Observe Loop",
        content=(
            "Autonomous AI agents operate in a cycle: 1) create a plan, "
            "2) execute the action, 3) observe the result and automatically "
            "correct on failure (self-correction). Reference: Peter "
            "Steinberger / OpenClaw."
        ),
        category="architecture",
        tags=["agents", "autonomy", "patterns"],
        source=PillSource(type=SourceType.DOCUMENT, reference="ai-agent-architecture-wave3.md"),
        confidence=1.0,
    ),
]


async def main() -> None:
    col = await get_collection()
    for pill in SEEDS:
        await col.insert_one(pill.to_mongo())
        print(f"  + {pill.title}")
    print(f"\n{len(SEEDS)} pills seeded.")


if __name__ == "__main__":
    asyncio.run(main())
