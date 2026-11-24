"""Install a systemd user timer for Pillars history sync."""

import os
from pathlib import Path
import subprocess
import sys

from pillars_history import HistoryError


SERVICE = "pillars-sync.service"
TIMER = "pillars-sync.timer"
TIMER_CONTENT = """\
[Unit]
Description=Sync Pillars history every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
"""


def systemctl(*args):
    try:
        result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    except OSError as exc:
        raise HistoryError(f"Could not run systemctl. {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HistoryError(f"Could not {args[0]} automatic sync through systemd. {detail}".rstrip())


def systemd_argument(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def enable_schedule(script, action):
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    units = config / "systemd" / "user"
    python = systemd_argument(sys.executable)
    entrypoint = systemd_argument(script)
    service = f"""\
[Unit]
Description=Sync Pillars history

[Service]
Type=oneshot
ExecStart=:{python} {entrypoint} sync {action}
TimeoutStartSec=5min
"""
    try:
        units.mkdir(parents=True, exist_ok=True)
        (units / SERVICE).write_text(service, encoding="utf-8")
        (units / TIMER).write_text(TIMER_CONTENT, encoding="utf-8")
    except OSError as exc:
        raise HistoryError(f"Could not install the systemd user units. {exc}") from exc

    systemctl("daemon-reload")
    systemctl("enable", "--now", TIMER)
    systemctl("start", "--no-block", SERVICE)


def disable_schedule():
    systemctl("disable", "--now", TIMER)
