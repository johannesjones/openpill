# Public Release Dry-Run Status

Use this file to track progress before making the repository public.

Status values:
- `TODO`
- `IN_PROGRESS`
- `DONE`

## Snapshot

- Target visibility date: `TBD`
- Owner: `jjones (TBD confirm)`
- Current decision: `keep private until checklist is DONE`

## Checklist tracking

| Area | Task | Status | Owner | Notes |
|---|---|---|---|---|
| Product and naming | OpenPill naming consistent across README/docs/UI/config | IN_PROGRESS | jjones | Main user-facing naming migrated; internal legacy aliases still present by design |
| Product and naming | Links in docs and README resolve | IN_PROGRESS | jjones | New docs linked; run final link-check before publish |
| Product and naming | No private local path references in examples | IN_PROGRESS | jjones | One final grep pass pending |
| Security and privacy | No secrets committed | IN_PROGRESS | jjones | Final pre-publish scan pending |
| Security and privacy | `.gitignore` excludes local artifacts and env files | DONE |  | Added in initial repo baseline |
| Security and privacy | Auth docs match runtime (`OPENPILL_API_KEY` + legacy vars) | DONE | jjones | Runtime fallback order documented and implemented |
| Security and privacy | Public routes intentionally documented | DONE | jjones | Health/docs/static exceptions documented |
| Quickstart DX | README quickstart replay from clean shell succeeds | IN_PROGRESS | jjones | Needs one clean-shell rehearsal before GO |
| Quickstart DX | `pytest tests/ -v` succeeds | IN_PROGRESS | jjones | Run targeted + full suite in final validation pass |
| Quickstart DX | `scripts/integration_mongo_smoke.sh` succeeds | IN_PROGRESS | jjones | Script exists; rerun during release-candidate check |
| Quickstart DX | `scripts/openclaw_guardrail_smoke.sh` succeeds | IN_PROGRESS | jjones | Script exists and adapted for pure bash regex |
| Quickstart DX | UI works in same-origin and remote API mode | IN_PROGRESS | jjones | API settings + auth header support implemented |
| Demo readiness | Demo flow documented and rehearsed | IN_PROGRESS | jjones | Rehearsal checklist not finalized |
| Demo readiness | Optional screenshots/GIF prepared | TODO |  |  |
| Repo hygiene | Branch clean (`git status`) | IN_PROGRESS | jjones | Work branch still active |
| Repo hygiene | Commit history grouped and readable | TODO | jjones | To be done at commit grouping step |
| Repo hygiene | Release candidate commit/tag selected | TODO | jjones | Pending final validation |

## Go/No-Go

- Decision: `NO-GO`
- Blocking items:
  - Final validation pass (tests + smokes + docs sanity) still open
  - Release candidate commit/tag not selected
- Next review date: `TBD (after validation pass)`

