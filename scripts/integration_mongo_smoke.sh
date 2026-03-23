#!/usr/bin/env bash
# Optional local smoke: start Mongo (e.g. docker compose up -d), then ping API + DB.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${MONGO_URI:-}" ]]; then
  export MONGO_URI="mongodb://127.0.0.1:27017"
fi

echo "== Mongo ping via motor =="
python3 - <<'PY'
import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    c = AsyncIOMotorClient(uri)
    try:
        r = await c.admin.command("ping")
        assert r.get("ok") == 1.0
        print("ok", r)
    finally:
        c.close()
asyncio.run(main())
PY

echo "== API health (start api.py in another terminal: python api.py) =="
if curl -sf "http://127.0.0.1:8080/health" >/dev/null; then
  curl -s "http://127.0.0.1:8080/health"
  echo
else
  echo "(skip) API not listening on :8080"
fi
