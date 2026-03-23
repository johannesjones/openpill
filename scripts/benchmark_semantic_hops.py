#!/usr/bin/env python3
"""Compare semantic retrieval quality/shape for 1-hop vs 2-hop expansion.

Usage:
  python scripts/benchmark_semantic_hops.py
  python scripts/benchmark_semantic_hops.py --base-url http://127.0.0.1:8080 --limit 8
  python scripts/benchmark_semantic_hops.py --queries-file ./queries.txt --out ./benchmark.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_QUERIES = [
    "OpenClaw memory weaknesses",
    "consistency contradictions stale information",
    "semantic retrieval long term memory",
    "idempotency ingest retries",
    "MCP tools for knowledge pills",
    "graph neighbors and relations",
    "janitor consolidation memory",
    "API key and request logging",
    "conflicting memory claims",
    "long-term facts extraction",
]


def _http_get_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _run_mode(
    base_url: str,
    query: str,
    *,
    limit: int,
    hops: int,
    neighbor_limit: int,
    max_nodes: int,
) -> dict:
    params = {
        "q": query,
        "limit": str(limit),
        "expand_neighbors": "true",
        "neighbor_limit": str(neighbor_limit),
        "max_hops": str(hops),
        "max_nodes": str(max_nodes),
    }
    url = f"{base_url.rstrip('/')}/pills/semantic?{urllib.parse.urlencode(params)}"
    t0 = time.perf_counter()
    data = _http_get_json(url)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    pills = data.get("pills", [])
    rows = []
    for p in pills:
        rows.append(
            {
                "id": p.get("_id"),
                "title": p.get("title"),
                "retrieval_score": p.get("retrieval_score"),
                "similarity": p.get("similarity"),
                "hop": p.get("hop", 0),
                "conflict_count": p.get("conflict_count", 0),
            }
        )
    return {
        "elapsed_ms": elapsed_ms,
        "count": data.get("count", len(rows)),
        "hops_found": sorted({r.get("hop", 0) for r in rows}),
        "rows": rows,
    }


def _load_queries(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_QUERIES
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Queries file not found: {path}")
    out: list[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out or DEFAULT_QUERIES


def _summarize(results: list[dict]) -> dict:
    one_ms = [r["one_hop"]["elapsed_ms"] for r in results]
    two_ms = [r["two_hop"]["elapsed_ms"] for r in results]
    one_cnt = [r["one_hop"]["count"] for r in results]
    two_cnt = [r["two_hop"]["count"] for r in results]
    queries_with_extra_hop = sum(
        1 for r in results if any((row.get("hop", 0) >= 2) for row in r["two_hop"]["rows"])
    )
    queries_with_additional_nodes = 0
    additional_nodes_total = 0
    for r in results:
        ids_1 = {row.get("id") for row in r["one_hop"]["rows"] if row.get("id")}
        ids_2 = {row.get("id") for row in r["two_hop"]["rows"] if row.get("id")}
        added = ids_2 - ids_1
        if added:
            queries_with_additional_nodes += 1
            additional_nodes_total += len(added)
    return {
        "queries": len(results),
        "avg_ms_1hop": round(statistics.mean(one_ms), 2) if one_ms else 0.0,
        "avg_ms_2hop": round(statistics.mean(two_ms), 2) if two_ms else 0.0,
        "avg_count_1hop": round(statistics.mean(one_cnt), 2) if one_cnt else 0.0,
        "avg_count_2hop": round(statistics.mean(two_cnt), 2) if two_cnt else 0.0,
        "queries_with_2hop_hits": queries_with_extra_hop,
        "queries_with_additional_nodes_2hop_vs_1hop": queries_with_additional_nodes,
        "additional_nodes_total_2hop_vs_1hop": additional_nodes_total,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark 1-hop vs 2-hop semantic retrieval.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="REST API base URL")
    parser.add_argument("--queries-file", default=None, help="Text file with one query per line")
    parser.add_argument("--limit", type=int, default=6, help="Top-k semantic results per query")
    parser.add_argument("--neighbor-limit", type=int, default=10, help="Max expanded neighbors")
    parser.add_argument("--max-nodes", type=int, default=30, help="Hard cap after expansion")
    parser.add_argument("--out", default="benchmark_semantic_hops.json", help="Output JSON path")
    args = parser.parse_args()

    queries = _load_queries(args.queries_file)
    runs = []
    for q in queries:
        one = _run_mode(
            args.base_url,
            q,
            limit=args.limit,
            hops=1,
            neighbor_limit=args.neighbor_limit,
            max_nodes=args.max_nodes,
        )
        two = _run_mode(
            args.base_url,
            q,
            limit=args.limit,
            hops=2,
            neighbor_limit=args.neighbor_limit,
            max_nodes=args.max_nodes,
        )
        runs.append({"query": q, "one_hop": one, "two_hop": two})

    payload = {"summary": _summarize(runs), "results": runs}
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    s = payload["summary"]
    print("Benchmark complete")
    print(f"- queries: {s['queries']}")
    print(f"- avg latency 1-hop: {s['avg_ms_1hop']} ms")
    print(f"- avg latency 2-hop: {s['avg_ms_2hop']} ms")
    print(f"- avg result count 1-hop: {s['avg_count_1hop']}")
    print(f"- avg result count 2-hop: {s['avg_count_2hop']}")
    print(f"- queries with true hop>=2 hits: {s['queries_with_2hop_hits']}")
    print(
        "- queries with additional nodes in 2-hop vs 1-hop: "
        f"{s['queries_with_additional_nodes_2hop_vs_1hop']}"
    )
    print(
        "- total additional nodes in 2-hop vs 1-hop: "
        f"{s['additional_nodes_total_2hop_vs_1hop']}"
    )
    print(f"- output: {args.out}")


if __name__ == "__main__":
    main()

