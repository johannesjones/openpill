#!/usr/bin/env bash
set -euo pipefail

API_BASE="${OPENPILL_API_BASE:-http://localhost:8080}"
API_KEY="${OPENPILL_API_KEY:-}"

auth_args=()
if [[ -n "${API_KEY}" ]]; then
  auth_args=(-H "Authorization: Bearer ${API_KEY}")
fi

usage() {
  cat <<'EOF'
OpenPill tool shortcuts (REST-backed)

Usage:
  bash scripts/openpill_tools.sh <command> [args...]

Commands:
  search_pills <query> [limit]
  semantic_search <query> [limit] [hybrid=true|false]
  get_pill <pill_id>
  get_pill_neighbors <pill_id>
  list_categories
  topics_snapshot [top_terms] [per_category] [min_doc_freq] [min_token_len]

Environment:
  OPENPILL_API_BASE   (default: http://localhost:8080)
  OPENPILL_API_KEY    (optional bearer token)
EOF
}

urlenc() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

cmd="${1:-}"
shift || true

case "${cmd}" in
  search_pills)
    q="${1:-}"
    limit="${2:-20}"
    if [[ -z "${q}" ]]; then
      echo "search_pills requires: <query> [limit]" >&2
      exit 1
    fi
    curl -sS "${auth_args[@]}" \
      "${API_BASE}/pills/search?q=$(urlenc "${q}")&limit=${limit}"
    ;;
  semantic_search)
    q="${1:-}"
    limit="${2:-10}"
    hybrid="${3:-false}"
    if [[ -z "${q}" ]]; then
      echo "semantic_search requires: <query> [limit] [hybrid]" >&2
      exit 1
    fi
    curl -sS "${auth_args[@]}" \
      "${API_BASE}/pills/semantic?q=$(urlenc "${q}")&limit=${limit}&hybrid=${hybrid}"
    ;;
  get_pill)
    id="${1:-}"
    if [[ -z "${id}" ]]; then
      echo "get_pill requires: <pill_id>" >&2
      exit 1
    fi
    curl -sS "${auth_args[@]}" "${API_BASE}/pills/${id}"
    ;;
  get_pill_neighbors)
    id="${1:-}"
    if [[ -z "${id}" ]]; then
      echo "get_pill_neighbors requires: <pill_id>" >&2
      exit 1
    fi
    curl -sS "${auth_args[@]}" "${API_BASE}/pills/${id}/neighbors"
    ;;
  list_categories)
    curl -sS "${auth_args[@]}" "${API_BASE}/categories"
    ;;
  topics_snapshot)
    top_terms="${1:-20}"
    per_category="${2:-10}"
    min_doc_freq="${3:-2}"
    min_token_len="${4:-3}"
    curl -sS "${auth_args[@]}" \
      "${API_BASE}/topics/snapshot?top_terms=${top_terms}&per_category=${per_category}&min_doc_freq=${min_doc_freq}&min_token_len=${min_token_len}"
    ;;
  ""|-h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    usage
    exit 1
    ;;
esac
