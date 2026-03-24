# Claude Code Instructions for OpenPill

This file defines Claude-Code-specific project guidance.

## Source of Truth

- Normative product intent: `SOUL.md`
- Cursor-specific execution rules: `.cursor/rules/*.mdc`
- This file mirrors those principles for Claude Code to avoid behavior drift.
- If guidance conflicts, prioritize:
  1. `SOUL.md`
  2. Safety and backward compatibility
  3. Tool-specific instruction files

## General Behaviour

- Be constructive and intellectually honest:
  - Give respectful pushback when trade-offs or risks matter.
  - Do not optimize for agreement over correctness.
- Maintain conversation continuity:
  - If interrupted by side questions, answer and then resume the previously agreed thread when still relevant.
  - Do not drop high-value prior proposals silently.
- Suggest next steps only when they add real value.

## Engineering Defaults

- Local-first by default; external model usage is explicit, guarded, and cost-aware.
- Preserve backward compatibility unless a breaking change is explicitly requested.
- Prefer safe incremental patches over broad refactors.
- Avoid destructive actions and never revert unrelated user changes.

## Python Backend Standards (`**/*.py`)

- Keep API/MCP response compatibility by default.
- Preserve existing env-var fallback chains unless intentionally redesigned.
- For retrieval/scoring logic:
  - Keep ranking deterministic and bounded.
  - Add observability metrics when introducing new retrieval paths.
- For policy/guard logic:
  - Fail safely to local behavior rather than hard-failing.
  - Include machine-readable reason fields where decisions change.
- Run focused tests after behavior changes.
- If OpenAPI shape changes intentionally, update contract snapshot tests in the same change.

## Static UI Standards (`static/index.html`)

- Keep UX progressive and conservative:
  - New controls are optional and default-off when experimental.
  - UI should degrade gracefully if optional backend features are disabled.
- Keep API behavior explicit:
  - Validate/clamp inputs before requests.
  - Surface user-facing errors; avoid silent failures.
  - Keep auth assumptions explicit.
- Keep implementation simple:
  - Avoid duplicated request logic.
  - Do not add external build tooling for this static page.

## Docs Standards (`README.md`, `docs/**/*.md`)

- Keep docs aligned with runtime behavior in the same change.
- Document new env vars with safe defaults and clear opt-in semantics.
- Use "OpenPill" consistently in user-facing docs; mention legacy names only for compatibility context.
- Keep release-readiness docs concrete and status-based (done/in-progress/blockers).
- Keep docs concise and scannable.
