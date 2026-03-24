#!/usr/bin/env python3
"""Uninstall OS-specific autostart for OpenPill markdown watch ingestion."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

LABEL = os.getenv("OPENPILL_AUTOSTART_LABEL", "com.openpill.mdwatch")


def uninstall_macos() -> None:
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    if plist.exists():
        plist.unlink()
    print(f"Removed macOS launchd agent: {plist}")


def uninstall_linux() -> None:
    unit = Path.home() / ".config" / "systemd" / "user" / "openpill-md-watch.service"
    subprocess.run(["systemctl", "--user", "disable", "--now", "openpill-md-watch.service"], check=False)
    if unit.exists():
        unit.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    print(f"Removed Linux user service: {unit}")


def uninstall_windows() -> None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        print("APPDATA not set; nothing to remove.")
        return
    cmd = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "openpill_md_watch.cmd"
    if cmd.exists():
        cmd.unlink()
    print(f"Removed Windows startup launcher: {cmd}")


def main() -> int:
    sysname = platform.system().lower()
    if sysname == "darwin":
        uninstall_macos()
    elif sysname == "linux":
        uninstall_linux()
    elif sysname == "windows":
        uninstall_windows()
    else:
        raise RuntimeError(f"Unsupported OS: {platform.system()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
