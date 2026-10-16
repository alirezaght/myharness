#!/usr/bin/env python3
"""AL status line — observational only.

Reads the Claude Code status-line JSON on stdin. Prints the AL segment for this
session when an AL run exists, then delegates to any status-line command the
user configured in ~/.claude/settings.json or .claude/settings.local.json (so a
project-level statusLine never hides a personal one). Prints nothing otherwise.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import al_render  # noqa: E402
import al_store  # noqa: E402


def al_segment(sid):
    from al_native_sessions import active
    try:run=active(sid)
    except (OSError,SystemExit):return 'AL │ state unreadable'
    if not run:return ''
    p = al_store.state_path(run)
    if not p.exists():
        return ""
    try:
        return al_render.render_line(json.loads(p.read_text()))
    except Exception:
        return "AL │ state unreadable"


def delegate_command(project_dir):
    candidates = [Path(project_dir or ".") / ".claude" / "settings.local.json", Path.home() / ".claude" / "settings.json"]
    for f in candidates:
        try:
            cmd = json.loads(f.read_text()).get("statusLine", {}).get("command", "")
        except Exception:
            continue
        if cmd and "al_statusline" not in cmd:
            return cmd
    return ""


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}
    sid = data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    project_dir = (data.get("workspace") or {}).get("project_dir") or os.environ.get("CLAUDE_PROJECT_DIR")
    parts = [s for s in [al_segment(sid)] if s]
    cmd = delegate_command(project_dir)
    if cmd:
        try:
            out = subprocess.run(cmd, shell=True, input=raw, capture_output=True, text=True, timeout=5).stdout.strip()
            if out:
                parts.append(out)
        except Exception:
            pass
    if parts:
        print(" ┃ ".join(parts))


if __name__ == "__main__":
    main()
