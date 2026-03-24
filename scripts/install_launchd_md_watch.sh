#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${OPENPILL_LAUNCHD_LABEL:-com.openpill.mdwatch}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
INTERVAL="${OPENPILL_MD_WATCH_INTERVAL:-60}"
ROOT_DIR="${OPENPILL_MD_WATCH_ROOT:-${REPO_ROOT}}"
LOG_DIR="${OPENPILL_MD_WATCH_LOG_DIR:-${HOME}/Library/Logs/openpill}"
OUT_LOG="${LOG_DIR}/md-watch.out.log"
ERR_LOG="${LOG_DIR}/md-watch.err.log"

mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${LOG_DIR}"

cat > "${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/env</string>
      <string>python3</string>
      <string>${REPO_ROOT}/scripts/ingest_markdown_memory.py</string>
      <string>--root</string>
      <string>${ROOT_DIR}</string>
      <string>--state-file</string>
      <string>${REPO_ROOT}/.openpill_md_ingest_state.json</string>
      <string>--interval</string>
      <string>${INTERVAL}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_ROOT}</string>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${OUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>${ERR_LOG}</string>
  </dict>
</plist>
EOF

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl load "${PLIST_PATH}"

echo "Installed launchd job: ${LABEL}"
echo "Plist: ${PLIST_PATH}"
echo "Logs:  ${OUT_LOG} / ${ERR_LOG}"
echo "Check: launchctl list | rg ${LABEL}"
