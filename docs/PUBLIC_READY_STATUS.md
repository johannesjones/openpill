# Public Release Dry-Run Status

Use this file to track progress before making the repository public.

Status values:
- `TODO`
- `IN_PROGRESS`
- `DONE`

## Snapshot

- Target visibility date: `TBD`
- Owner: `TBD`
- Current decision: `keep private until checklist is DONE`

## Checklist tracking

| Area | Task | Status | Owner | Notes |
|---|---|---|---|---|
| Product and naming | OpenPill naming consistent across README/docs/UI/config | TODO |  |  |
| Product and naming | Links in docs and README resolve | TODO |  |  |
| Product and naming | No private local path references in examples | TODO |  |  |
| Security and privacy | No secrets committed | TODO |  |  |
| Security and privacy | `.gitignore` excludes local artifacts and env files | DONE |  | Added in initial repo baseline |
| Security and privacy | Auth docs match runtime (`OPENPILL_API_KEY` + legacy vars) | TODO |  |  |
| Security and privacy | Public routes intentionally documented | TODO |  |  |
| Quickstart DX | README quickstart replay from clean shell succeeds | TODO |  |  |
| Quickstart DX | `pytest tests/ -v` succeeds | TODO |  |  |
| Quickstart DX | `scripts/integration_mongo_smoke.sh` succeeds | TODO |  |  |
| Quickstart DX | `scripts/openclaw_guardrail_smoke.sh` succeeds | TODO |  |  |
| Quickstart DX | UI works in same-origin and remote API mode | TODO |  |  |
| Demo readiness | Demo flow documented and rehearsed | TODO |  |  |
| Demo readiness | Optional screenshots/GIF prepared | TODO |  |  |
| Repo hygiene | Branch clean (`git status`) | TODO |  |  |
| Repo hygiene | Commit history grouped and readable | TODO |  |  |
| Repo hygiene | Release candidate commit/tag selected | TODO |  |  |

## Go/No-Go

- Decision: `NO-GO`
- Blocking items:
  - TBD
- Next review date: `TBD`

