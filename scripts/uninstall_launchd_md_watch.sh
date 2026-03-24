#!/usr/bin/env bash
set -euo pipefail

LABEL="${OPENPILL_LAUNCHD_LABEL:-com.openpill.mdwatch}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "${PLIST_PATH}" ]]; then
  launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
  rm -f "${PLIST_PATH}"
  echo "Uninstalled launchd job: ${LABEL}"
else
  echo "No plist found at ${PLIST_PATH}"
fi
