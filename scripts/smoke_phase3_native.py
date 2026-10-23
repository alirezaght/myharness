#!/usr/bin/env python3
"""Run one bounded Phase 3 native-host smoke in a disposable project root."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from harness.approval import ApprovalGate
from harness.controller import HarnessController
from harness.db import connect
from harness.execution import start_execution
from harness.execution_store import get_execution
from harness.spec_store import SpecStore
from tests.complete_task import settle
from tests.sample_repo import make_sample_repo

ROOT = Path(__file__).resolve().parents[1]


def _redact(text: str) -> str:
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]+=*", r"\1 [REDACTED]", text)
    return re.sub(
        r"(?i)\b(token|secret|password|authorization|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )


def _worker_diagnostics(handoff_root: Path) -> list[dict]:
    rows = []
    for log in sorted(handoff_root.glob("worker-*.log")):
        tail = log.read_bytes()[-4096:].decode(errors="replace")
        rows.append({"name": log.name, "tail": _redact(tail)})
    return rows


def _candidate_launcher(root: Path) -> Path:
    """Bootstrap the candidate managed launcher inside the disposable root (never ~/.local/bin)."""
    launcher = root / "candidate-bin" / "alctl"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(ROOT / "al/alctl.py"),
            "bootstrap",
            "--source-root",
            str(ROOT / "al"),
            "--destination",
            str(launcher),
            "--state-root",
            str(root / "candidate-state"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode or not launcher.is_file():
        raise RuntimeError("candidate launcher bootstrap failed: " + _redact(result.stderr[-2000:]))
    return launcher


def _install_candidate(repo: Path, host: str, launcher: Path) -> None:
    if host == "claude":
        skill = repo / ".claude/skills/al"
        role_dir = repo / ".claude/agents/al"
        source = ROOT / "al/claude/skills/al"
        roles = ROOT / "al/claude/agents"
        suffix = ".md"
    else:
        skill = repo / ".agents/skills/al"
        role_dir = repo / ".codex/agents"
        source = ROOT / "al/codex/skills/al"
        roles = ROOT / "al/codex/agents"
        suffix = ".toml"
    skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, skill)
    # The disposable project's skill copy names the candidate launcher instead of the global one.
    instructions = skill / "SKILL.md"
    text = instructions.read_text(encoding="utf-8")
    if "$HOME/.local/bin/alctl" not in text:
        raise RuntimeError("candidate skill does not name the managed launcher")
    instructions.write_text(text.replace("$HOME/.local/bin/alctl", str(launcher)), encoding="utf-8")
    role_dir.mkdir(parents=True, exist_ok=True)
    role_names = (
        "al-architect",
        "al-architect-fallback",
        "al-designer",
        "al-designer-fallback",
        "al-engineer",
        "al-builder",
        "al-scout",
    )
    for role in role_names:
        shutil.copy2(roles / (role + suffix), role_dir / (role + suffix))
    if host == "codex":
        entries = ["[agents]", "max_depth = 1", ""]
        for role in role_names:
            path = (role_dir / (role + suffix)).resolve()
            entries.extend((f"[agents.{role}]", f"config_file = {json.dumps(str(path))}", ""))
        config = repo / ".codex/config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("\n".join(entries), encoding="utf-8")
        parsed = tomllib.loads(config.read_text(encoding="utf-8"))
        if parsed.get("agents", {}).get("max_depth") != 1:
            raise RuntimeError("disposable Codex project config did not set AL's agent depth")
        registered = {
            role
            for role in role_names
            if parsed.get("agents", {}).get(role, {}).get("config_file")
            == str((role_dir / (role + suffix)).resolve())
        }
        if registered != set(role_names):
            raise RuntimeError(
                "disposable Codex project config did not register every candidate role"
            )
    if not (skill / "SKILL.md").is_file() or any(
        not (role_dir / (role + suffix)).is_file() for role in role_names
    ):
        raise RuntimeError(f"candidate {host} skill/role installation is incomplete")


def _process_identity(pid: int) -> dict | None:
    result = subprocess.run(
        ["ps", "-o", "pid=,pgid=,lstart=,command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        return None
    fields = result.stdout.strip().split(maxsplit=7)
    if len(fields) != 8:
        return None
    try:
        return {
            "pid": int(fields[0]),
            "pgid": int(fields[1]),
            "started": " ".join(fields[2:7]),
            "command": fields[7],
        }
    except ValueError:
        return None


def _owned_process_identity(pid: int) -> dict | None:
    if not pid or pid == os.getpid():
        return None
    identity = _process_identity(pid)
    if identity and identity.get("pgid") == pid:
        return identity
    return None


def _remember_owned_process(owned_processes: dict, pid: int | None) -> None:
    if pid and pid not in owned_processes:
        identity = _owned_process_identity(pid)
        if identity:
            owned_processes[pid] = identity


def _safe_error(error: str | None) -> str | None:
    return _redact(error) if error else None


def _stop_process_group(pid: int | None, expected_identity: dict | None = None) -> str | None:
    if not pid:
        return None
    if pid == os.getpid():
        return f"refused to signal runner process {pid}"
    if not expected_identity:
        return f"refused to signal process {pid} without a recorded start identity"
    if _owned_process_identity(pid) != expected_identity:
        return f"refused to signal process {pid}; owned process identity changed or is unavailable"
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return None
    except PermissionError as error:
        return f"could not signal owned process group {pid}: PermissionError: {error}"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        time.sleep(0.1)
    if _owned_process_identity(pid) != expected_identity:
        return (
            f"refused to force-stop process {pid}; owned process identity changed or is unavailable"
        )
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return None
    except PermissionError as error:
        return f"could not force-stop owned process group {pid}: PermissionError: {error}"
    return None


def _run(host: str, timeout: int) -> tuple[int, dict]:
    previous_environment = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix=f"harness-phase3-{host}-") as temporary:
        root = Path(temporary).resolve()
        repo = make_sample_repo(root / "project")
        launcher = _candidate_launcher(root)
        _install_candidate(repo, host, launcher)
        database = root / "harness.db"
        os.environ.update(
            HARNESS_DB=str(database),
            HARNESS_HOME=str(root / "harness-home"),
            HARNESS_AUTHORITY_ROOT=str(ROOT),
            PATH=str(launcher.parent) + os.pathsep + os.environ.get("PATH", ""),
        )
        previous = Path.cwd()
        os.chdir(repo)
        conn = None
        try:
            conn = connect(database)
            controller = HarnessController(conn)
            task = controller.create_task(
                "Make a small, testable documentation-only improvement in this disposable fixture",
                "trivial",
            )
            settle(controller, task.id)
            controller.advance(task.id)
            ApprovalGate(
                controller, task.id, SpecStore(repo / "docs/specs" / task.id), repo
            ).approve()
            launched = start_execution(controller, task.id, host=host)
            execution_id = launched["execution"]["id"]
            deadline = time.monotonic() + timeout
            owned_processes = {}
            while time.monotonic() < deadline:
                execution = get_execution(conn, execution_id)
                attempts = conn.execute(
                    "SELECT worker_pid,child_pid FROM execution_attempts "
                    "WHERE execution_id=? ORDER BY rowid DESC LIMIT 1",
                    (execution_id,),
                ).fetchone()
                if attempts:
                    for process_pid in (attempts["worker_pid"], attempts["child_pid"]):
                        _remember_owned_process(owned_processes, process_pid)
                if execution["status"] in {
                    "Completed",
                    "Failed",
                    "Unavailable",
                    "Interrupted",
                    "RecoveryRequired",
                }:
                    break
                time.sleep(0.5)
            execution = get_execution(conn, execution_id)
            timed_out = execution["status"] not in {
                "Completed",
                "Failed",
                "Unavailable",
                "Interrupted",
                "RecoveryRequired",
            }
            cleanup_errors = []
            if timed_out:
                attempts = conn.execute(
                    "SELECT worker_pid,child_pid FROM execution_attempts "
                    "WHERE execution_id=? ORDER BY rowid DESC LIMIT 1",
                    (execution_id,),
                ).fetchone()
                for pid in (attempts["child_pid"], attempts["worker_pid"]):
                    cleanup_error = _stop_process_group(pid, owned_processes.get(pid))
                    if cleanup_error:
                        cleanup_errors.append(cleanup_error)
                execution = get_execution(conn, execution_id)
            latest = conn.execute(
                "SELECT worker_pid,child_pid,status FROM execution_attempts "
                "WHERE execution_id=? ORDER BY rowid DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
            handoff_root = Path(execution["handoff_path"]).parent
            diagnostics = _worker_diagnostics(handoff_root)
            if timed_out:
                failure_class = "bounded-timeout"
            elif execution["status"] == "Unavailable":
                failure_class = "package-or-launch-preflight"
            elif execution["status"] in {"Interrupted", "RecoveryRequired", "Failed"}:
                failure_class = "native-process-or-AL-lifecycle"
            elif execution["status"] == "Completed":
                failure_class = None
            else:
                failure_class = "nonterminal-status"
            report = {
                "host": host,
                "execution_id": execution_id,
                "status": execution["status"],
                "al_run_id": execution["al_run_id"],
                "native_id_recorded": bool(execution["native_id"]),
                "event_cursor": execution["cursor"],
                "error": _safe_error(execution["error"]),
                "timed_out": timed_out,
                "failure_class": failure_class,
                "attempt_status": latest["status"] if latest else None,
                "worker_pid": latest["worker_pid"] if latest else None,
                "child_pid": latest["child_pid"] if latest else None,
                "worker_diagnostics": diagnostics,
                "cleanup_errors": cleanup_errors,
                "project_is_disposable": True,
                "candidate_installed_only_in_project": True,
                "candidate_launcher": str(launcher),
            }
            print(json.dumps(report, sort_keys=True))
            return (
                0 if execution["status"] == "Completed" and execution["al_run_id"] else 2
            ), report
        finally:
            if conn is not None:
                conn.close()
            os.chdir(previous)
            os.environ.clear()
            os.environ.update(previous_environment)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=("claude", "codex"), required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not 10 <= args.timeout <= 180:
        parser.error("--timeout must be between 10 and 180 seconds")
    status, _ = _run(args.host, args.timeout)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
