#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _api_base() -> str:
    return os.getenv("OPENPILL_API_BASE", "http://localhost:8080").rstrip("/")


def _auth_headers() -> dict[str, str]:
    key = os.getenv("OPENPILL_API_KEY", "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _request_json(method: str, path: str, *, query: dict | None = None, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    base = _api_base()
    url = f"{base}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    data = None
    req_headers: dict[str, str] = {"Accept": "application/json"}
    req_headers.update(_auth_headers())
    if headers:
        req_headers.update(headers)

    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        data = raw
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = int(resp.status)
            payload = resp.read().decode("utf-8", errors="replace")
            if not payload.strip():
                return status, {}
            return status, json.loads(payload)
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(payload) if payload.strip() else {"error": payload}
        except ValueError:
            j = {"error": payload}
        return int(e.code), j


def _stable_idempotency_key(source: str, title: str, text: str) -> str:
    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(b"\n")
    h.update(title.encode("utf-8"))
    h.update(b"\n")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def cmd_semantic(args: argparse.Namespace) -> int:
    status, j = _request_json(
        "GET",
        "/pills/semantic",
        query={"q": args.query, "limit": str(args.limit), "hybrid": "true" if args.hybrid else "false"},
    )
    print(json.dumps(j, indent=2, sort_keys=True))
    return 0 if 200 <= status < 300 else 1


def cmd_neighbors(args: argparse.Namespace) -> int:
    status, j = _request_json("GET", f"/pills/{urllib.parse.quote(args.pill_id)}/neighbors")
    print(json.dumps(j, indent=2, sort_keys=True))
    return 0 if 200 <= status < 300 else 1


def cmd_get(args: argparse.Namespace) -> int:
    status, j = _request_json("GET", f"/pills/{urllib.parse.quote(args.pill_id)}")
    print(json.dumps(j, indent=2, sort_keys=True))
    return 0 if 200 <= status < 300 else 1


def cmd_ingest_text(args: argparse.Namespace) -> int:
    source = args.source.strip()
    title = args.title.strip()
    text = args.text
    idem = _stable_idempotency_key(source, title, text)
    status, j = _request_json(
        "POST",
        "/pills/ingest",
        body={
            "title": title,
            "text": text,
            "source_reference": source,
        },
        headers={"Idempotency-Key": idem},
    )
    print(json.dumps({"status": status, "response": j, "idempotency_key": idem}, indent=2, sort_keys=True))
    return 0 if 200 <= status < 300 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="OpenPill REST client for Hermes skill usage.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_sem = sub.add_parser("semantic", help="Semantic search pills.")
    p_sem.add_argument("query")
    p_sem.add_argument("--limit", type=int, default=10)
    p_sem.add_argument("--hybrid", action="store_true", default=False)
    p_sem.set_defaults(func=cmd_semantic)

    p_get = sub.add_parser("get", help="Get pill by ID.")
    p_get.add_argument("pill_id")
    p_get.set_defaults(func=cmd_get)

    p_nei = sub.add_parser("neighbors", help="Get pill neighbors by ID.")
    p_nei.add_argument("pill_id")
    p_nei.set_defaults(func=cmd_neighbors)

    p_ing = sub.add_parser("ingest-text", help="Ingest durable text as a pill (idempotent).")
    p_ing.add_argument("--title", required=True)
    p_ing.add_argument("--source", required=True, help="Provenance reference, e.g. hermes:session:<id>.")
    p_ing.add_argument("--text", required=True)
    p_ing.set_defaults(func=cmd_ingest_text)

    args = p.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, urllib.error.URLError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

