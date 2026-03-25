# Public Release Checklist (Internal)

Use this checklist before changing the GitHub repository visibility from private to public.

For ongoing tracking (without editing this checklist), use:
- [`PUBLIC_READY_STATUS.md`](PUBLIC_READY_STATUS.md)

## Product and naming

- Confirm the product name is finalized as **OpenPill** across README, docs, UI, and config examples.
- Verify all links in `README.md` and `docs/` resolve correctly.
- Ensure examples do not reference local private paths or personal machine details.

## Security and privacy

- Confirm no secrets are committed (`.env`, tokens, credentials, local auth files).
- Verify `.gitignore` excludes local caches and environment files.
- Check that API auth docs match runtime behavior (`OPENPILL_API_KEY` + legacy compatibility).
- Ensure public routes (`/health`, docs/static) are intentional and documented.

## Quickstart and developer experience

- Follow `README.md` Quick Start from a clean environment and verify it works end-to-end.
- Run core tests and smoke checks:
  - `pytest tests/ -v`
  - `bash scripts/integration_mongo_smoke.sh`
  - `bash scripts/openclaw_guardrail_smoke.sh`
- Confirm UI works in same-origin mode and remote mode via API settings.

## Demo readiness

- Verify the single-page app supports:
  - ingest conversation
  - keyword + semantic search
  - pill detail editing and relation updates
- Prepare at least one short demo flow (copy/paste transcript -> retrieve -> edit).
- Optional: add a short GIF/screenshot set to the README.

## Repo hygiene

- Confirm branch is clean (`git status`).
- Review latest commits for clear messages and coherent grouping.
- Tag a release candidate commit before switching visibility.

