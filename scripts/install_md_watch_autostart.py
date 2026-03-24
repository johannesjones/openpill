#!/usr/bin/env python3
"""
Install OS-specific autostart for OpenPill markdown watch ingestion.

Supported:
- macOS: launchd user agent
- Linux: systemd --user service
- Windows: Startup folder .cmd launcher
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

LABEL = os.getenv("OPENPILL_AUTOSTART_LABEL", "com.openpill.mdwatch")
INTERVAL = os.getenv("OPENPILL_MD_WATCH_INTERVAL", "60")
REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(os.getenv("OPENPILL_MD_WATCH_ROOT", str(REPO_ROOT))).resolve()
STATE_FILE = Path(os.getenv("OPENPILL_MD_WATCH_STATE", str(REPO_ROOT / ".openpill_md_ingest_state.json"))).resolve()
SCRIPT = REPO_ROOT / "scripts" / "ingest_markdown_memory.py"


def install_macos() -> None:
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    log_dir = Path.home() / "Library" / "Logs" / "openpill"
    log_dir.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    out_log = log_dir / "md-watch.out.log"
    err_log = log_dir / "md-watch.err.log"
    plist.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/env</string>
      <string>python3</string>
      <string>{SCRIPT}</string>
      <string>--root</string><string>{ROOT_DIR}</string>
      <string>--state-file</string><string>{STATE_FILE}</string>
      <string>--interval</string><string>{INTERVAL}</string>
    </array>
    <key>WorkingDirectory</key><string>{REPO_ROOT}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{out_log}</string>
    <key>StandardErrorPath</key><string>{err_log}</string>
  </dict>
</plist>
""",
        encoding="utf-8",
    )
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    subprocess.run(["launchctl", "load", str(plist)], check=True)
    print(f"Installed macOS launchd agent: {plist}")


def install_linux() -> None:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "openpill-md-watch.service"
    unit.write_text(
        f"""[Unit]
Description=OpenPill markdown watch ingestion
After=network-online.target

[Service]
Type=simple
WorkingDirectory={REPO_ROOT}
ExecStart=/usr/bin/env python3 {SCRIPT} --root {ROOT_DIR} --state-file {STATE_FILE} --interval {INTERVAL}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""",
        encoding="utf-8",
    )
    if subprocess.run(["which", "systemctl"], capture_output=True, check=False).returncode != 0:
        raise RuntimeError("systemctl not found; cannot install Linux autostart.")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "openpill-md-watch.service"], check=True)
    print(f"Installed Linux user service: {unit}")


def install_windows() -> None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot locate Startup folder.")
    startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    cmd = startup / "openpill_md_watch.cmd"
    cmd.write_text(
        (
            "@echo off\n"
            "start \"OpenPillMDWatch\" /min "
            f"python \"{SCRIPT}\" --root \"{ROOT_DIR}\" --state-file \"{STATE_FILE}\" --interval {INTERVAL}\n"
        ),
        encoding="utf-8",
    )
    print(f"Installed Windows startup launcher: {cmd}")


def main() -> int:
    sysname = platform.system().lower()
    if sysname == "darwin":
        install_macos()
    elif sysname == "linux":
        install_linux()
    elif sysname == "windows":
        install_windows()
    else:
        raise RuntimeError(f"Unsupported OS: {platform.system()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
