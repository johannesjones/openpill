#!/usr/bin/env python3
"""
Ingest changed markdown files into OpenPill via REST API.

Designed for autonomous memory sync loops (OpenClaw or local cron/watchers).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_API_BASE = os.getenv("OPENPILL_API_BASE", "http://localhost:8080")
DEFAULT_STATE_FILE = ".openpill_md_ingest_state.json"
EXCLUDE_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".cursor"}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, state: dict[str, str]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def iter_markdown_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*.md"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        files.append(p)
    return sorted(files)


def post_ingest(
    *,
    api_base: str,
    api_key: str | None,
    idempotency_key: str,
    source_reference: str,
    text: str,
    min_confidence: float,
) -> dict:
    url = api_base.rstrip("/") + "/pills/ingest"
    payload = json.dumps(
        {
            "text": text,
            "source_reference": source_reference,
            "min_confidence": min_confidence,
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} for {source_reference}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error for {source_reference}: {e}") from e


def run_once(
    *,
    root: Path,
    state_file: Path,
    api_base: str,
    api_key: str | None,
    min_confidence: float,
    dry_run: bool,
) -> int:
    state = load_state(state_file)
    current: dict[str, str] = {}
    changed: list[Path] = []

    for p in iter_markdown_files(root):
        rel = str(p.relative_to(root))
        digest = file_hash(p)
        current[rel] = digest
        if state.get(rel) != digest:
            changed.append(p)

    if not changed:
        print("No changed markdown files.")
        save_state(state_file, current)
        return 0

    print(f"Changed markdown files: {len(changed)}")
    ingested = 0
    for p in changed:
        rel = str(p.relative_to(root))
        content = p.read_text(encoding="utf-8", errors="replace")
        source_ref = f"md:{rel}"
        idem = hashlib.sha256((rel + "\n" + current[rel]).encode("utf-8")).hexdigest()
        if dry_run:
            print(f"[DRY RUN] Would ingest: {source_ref}")
            ingested += 1
            continue

        text = f"[Source file: {rel}]\n\n{content}"
        result = post_ingest(
            api_base=api_base,
            api_key=api_key,
            idempotency_key=idem,
            source_reference=source_ref,
            text=text,
            min_confidence=min_confidence,
        )
        print(
            f"Ingested {source_ref}: "
            f"inserted={len(result.get('inserted', []))} "
            f"dupes={len(result.get('skipped_duplicate', []))}"
        )
        ingested += 1

    save_state(state_file, current)
    print(f"Done. Processed changed files: {ingested}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest changed markdown files into OpenPill.")
    parser.add_argument("--root", default=".", help="Root directory to scan for .md files.")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="State file path.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="OpenPill API base URL.")
    parser.add_argument("--api-key", default=os.getenv("OPENPILL_API_KEY", ""), help="Optional API key.")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="Ingest min_confidence.")
    parser.add_argument("--interval", type=int, default=0, help="Loop interval seconds (0 = run once).")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be ingested.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    state_file = Path(args.state_file).resolve()
    api_key = args.api_key.strip() or None

    if args.interval <= 0:
        return run_once(
            root=root,
            state_file=state_file,
            api_base=args.api_base,
            api_key=api_key,
            min_confidence=args.min_confidence,
            dry_run=args.dry_run,
        )

    print(f"Watching markdown changes every {args.interval}s under {root}")
    while True:
        try:
            run_once(
                root=root,
                state_file=state_file,
                api_base=args.api_base,
                api_key=api_key,
                min_confidence=args.min_confidence,
                dry_run=args.dry_run,
            )
        except (RuntimeError, OSError, ValueError, urllib.error.URLError) as exc:
            print(f"[WARN] cycle failed: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
