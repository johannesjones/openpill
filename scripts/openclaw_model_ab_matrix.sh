#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARDRAIL_SCRIPT="${ROOT_DIR}/scripts/openclaw_guardrail_smoke.sh"

CONTAINER="${OPENCLAW_CONTAINER:-openclaw-sandbox}"
BASE_MODEL="${BASE_MODEL:-ollama/qwen2.5:7b}"
CHALLENGER_MODEL="${CHALLENGER_MODEL:-gemini/gemini-2.0-flash}"

if [[ ! -x "${GUARDRAIL_SCRIPT}" ]]; then
  echo "FAIL: missing executable guardrail script at ${GUARDRAIL_SCRIPT}"
  exit 1
fi

run_for_model() {
  local model="$1"
  local out_file
  local elapsed

  echo "=== MODEL: ${model} ==="

  if ! docker exec "${CONTAINER}" sh -lc "node openclaw.mjs models set '${model}'" >/dev/null 2>&1; then
    echo "SKIP: could not set model '${model}' (not configured or auth missing)"
    return 2
  fi

  out_file="$(mktemp)"
  elapsed="$(/usr/bin/time -p bash -lc "'${GUARDRAIL_SCRIPT}'" >"${out_file}" 2>&1; awk '/^real /{print $2}')"
  if [[ -z "${elapsed}" ]]; then
    elapsed="n/a"
  fi

  if grep -q "RESULT: PASS" "${out_file}"; then
    echo "RESULT: PASS (elapsed_s=${elapsed})"
    rm -f "${out_file}"
    return 0
  fi

  echo "RESULT: FAIL (elapsed_s=${elapsed})"
  sed -n '1,80p' "${out_file}"
  rm -f "${out_file}"
  return 1
}

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker command not found"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo "FAIL: container '${CONTAINER}' is not running"
  exit 1
fi

echo "Running A/B matrix with:"
echo "  BASE_MODEL=${BASE_MODEL}"
echo "  CHALLENGER_MODEL=${CHALLENGER_MODEL}"
echo

run_for_model "${BASE_MODEL}"
base_rc=$?
echo
run_for_model "${CHALLENGER_MODEL}"
challenger_rc=$?
echo

echo "=== SUMMARY ==="
echo "base(${BASE_MODEL}) rc=${base_rc}  challenger(${CHALLENGER_MODEL}) rc=${challenger_rc}"

if [[ "${base_rc}" -eq 0 && "${challenger_rc}" -eq 0 ]]; then
  echo "OVERALL: PASS"
  exit 0
fi

if [[ "${base_rc}" -eq 2 || "${challenger_rc}" -eq 2 ]]; then
  echo "OVERALL: PARTIAL (at least one model was not configured)"
  exit 2
fi

echo "OVERALL: FAIL"
exit 1
