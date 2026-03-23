#!/usr/bin/env bash
set -u

CONTAINER="${OPENCLAW_CONTAINER:-openclaw-sandbox}"
AGENT="${OPENCLAW_AGENT:-main}"
TIMEOUT_SECONDS="${OPENCLAW_TIMEOUT_SECONDS:-120}"

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker command not found"
  exit 1
fi

container_found=0
while IFS= read -r name; do
  if [[ "${name}" == "${CONTAINER}" ]]; then
    container_found=1
    break
  fi
done < <(docker ps --format '{{.Names}}')

if [[ "${container_found}" -ne 1 ]]; then
  echo "FAIL: container '${CONTAINER}' is not running"
  exit 1
fi

run_case() {
  local case_id="$1"
  local message="$2"
  local session_id="guardrail-smoke-${case_id}-$(date +%s)"
  local output

  output="$(docker exec "${CONTAINER}" sh -lc "OLLAMA_API_KEY=ollama-local node openclaw.mjs agent --local --agent ${AGENT} --session-id ${session_id} --verbose on --message \"${message}\" --json --timeout ${TIMEOUT_SECONDS}" 2>&1)"

  if [[ ! "${output}" =~ tool=semantic_search ]]; then
    echo "CASE ${case_id}: FAIL (semantic_search was not called)"
    return 1
  fi

  if [[ "${output}" =~ Connection\ refused|ECONNREFUSED|could\ not\ reach\ any\ servers|fehler\ bei\ der\ verbindung\ zum\ speicher|kann\ ich\ keine\ ergebnisse ]]; then
    echo "CASE ${case_id}: FAIL (memory backend/connectivity error detected)"
    return 1
  fi

  if [[ ! "${output}" =~ \"payloads\" ]]; then
    echo "CASE ${case_id}: FAIL (no JSON payload returned)"
    return 1
  fi

  echo "CASE ${case_id}: PASS"
  return 0
}

fails=0

run_case "1" "Frage zum Memory: Wie ist OpenClaw mit OpenPill integriert? Du MUSST zuerst semantic_search nutzen und danach kurz antworten." || fails=$((fails + 1))
run_case "2" "Welche Memory-Policies gelten fuer OpenClaw? Nutze semantic_search und antworte nur mit 3 Bulletpoints." || fails=$((fails + 1))
run_case "3" "Gib mir zwei OpenPill-Entries zum Thema MCP und Integration, vorher semantic_search nutzen." || fails=$((fails + 1))

if [[ "${fails}" -gt 0 ]]; then
  echo "RESULT: FAIL (${fails} case(s) failed)"
  exit 1
fi

echo "RESULT: PASS (all cases passed)"
exit 0
