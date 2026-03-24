# External Model A/B Protocol (Cost-Capped)

This protocol defines when OpenPill may evaluate an external model (for example `gemini/gemini-2.0-flash`) against the local baseline.

## Trigger Conditions

Run an external A/B only if all are true:

- Local-first baseline is already passing core smokes.
- A clear quality gap is observed on a real use case (for example missed extraction detail or unstable consolidation quality).
- A hard budget is pre-set and accepted before starting.

## Fixed Test Cases

Use a fixed, repeatable set:

1. Guardrail flow (`scripts/openclaw_guardrail_smoke.sh`)
2. A/B matrix wrapper (`scripts/openclaw_model_ab_matrix.sh`)
3. At least 3 representative local transcripts (short/medium/long) for extraction comparison

Keep these cases unchanged between baseline and challenger runs.

## Metrics

Track per model:

- Guardrail pass/fail
- Runtime (seconds)
- Extraction yield: inserted count, duplicate-skip count
- Janitor quality notes: contradiction/redundancy consolidation quality (manual spot-check)
- Estimated token spend (if external model used)

## Hard Cost Cap

Before run:

- Set `AB_MAX_COST_USD` (team convention) and stop immediately once reached.
- If exact provider usage is unavailable in logs, use conservative estimate and stop early.
- Script guardrails:
  - `scripts/openclaw_model_ab_matrix.sh` requires `AB_ALLOW_EXTERNAL_AB=true` for non-`ollama/*` challenger models.
  - For external challengers it also requires `AB_MAX_COST_USD` to be set.

Runtime guard knobs for extractor/janitor policy routing:

- `AB_GUARDS_ENABLED=true` enables runtime guard enforcement.
- `AB_ALLOW_EXTERNAL=true` is required before external model selection is allowed.
- `AB_MAX_EXTERNAL_CALLS=<N>` caps external model calls per process (`0` = uncapped).

Recommended starter cap for first trial: `<= 2 USD`.

## Decision Rule

Adopt external escalation only if:

- Guardrail reliability is not worse than local baseline, and
- Quality is materially better on at least one critical case, and
- Cost per successful run remains within budget.

Otherwise keep `local_first` with escalation disabled.
