#!/usr/bin/env python3
"""AL state store — where the run state lives and how a phase transition is
recorded. Observational only: it reads git, it never changes the tree or index.
"""
import contextlib
import fcntl
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_models  # noqa: E402
from al_phases import MAIN_PHASES, PHASE_AGENT  # noqa: E402

def _resolve_repo_root():
    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        root = result.stdout.strip()
        if result.returncode == 0 and root:
            return Path(root).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd


REPO_ROOT = _resolve_repo_root()
RUNTIME_DIR = Path(os.environ.get("AL_RUNTIME_DIR") or REPO_ROOT / ".claude" / "al-runtime")
CURRENT = RUNTIME_DIR / "current"


def _ensure_runtime_dir():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    ignore = RUNTIME_DIR / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
        return
    content = ignore.read_text()
    if "*" in content.splitlines():
        return
    separator = "" if not content or content.endswith("\n") else "\n"
    ignore.write_text(f"{content}{separator}*\n")


def _sanitise(sid):
    from al_schema import valid_id, refuse
    if sid and not valid_id(sid): refuse("invalid session identity")
    return sid


def new_session_id():
    return f"al-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(3)}"


def session_id(explicit, allow_new=False):
    """--session wins; else the id `start` recorded in `current`; `start` may mint one."""
    if explicit is not None:
        if not _sanitise(explicit):sys.exit('al_state: invalid explicit session identity')
        return explicit
    from al_native_sessions import active,conversation
    current=active(conversation())
    if current and not allow_new:
        return current
    if allow_new:
        return new_session_id()
    sys.exit("al_state: no session id — pass --session ID, or run `start` first "
             "(.claude/al-runtime/current is missing)")


def write_current(sid,context):
    from al_native_sessions import publish
    _ensure_runtime_dir()
    publish(sid,context)


def clear_current(sid):
    from al_native_sessions import clear
    clear(sid)


def state_path(sid):
    return RUNTIME_DIR / f"{sid}.json"


def load(sid):
    p = state_path(sid)
    if not p.exists():
        sys.exit(f"al_state: no AL run for session {sid} (run `start` first)")
    try:
        st = json.loads(p.read_text())
        from al_schema import validate_state
        validate_state(st)
        from al_scope import validate_identity
        validate_identity(st)
        return st
    except (ValueError, OSError, KeyError, TypeError) as exc:
        sys.exit("al_state: corrupt state: " + str(exc))


def save(sid, st):
    from al_schema import validate_state
    validate_state(st)
    _ensure_runtime_dir()
    st["updated_at"] = time.time()
    fd, tmp = tempfile.mkstemp(dir=RUNTIME_DIR, prefix=f".{sid}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(st, indent=2))
    os.replace(tmp, state_path(sid))


@contextlib.contextmanager
def locked(sid):
    """Exclusive lock for a load-modify-save that may race a long-running
    `evidence run` in another process."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUNTIME_DIR / f".{sid}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def git(*args):
    from al_scope import git_bytes
    return git_bytes(*args).decode(errors="replace")


def set_phase(st, phase, agents=None, mode=None):
    if phase=='PLAN_CONFLICT' and st['phase']!=phase:
        from al_conflicts import enter
        enter(st)
    now = time.time()
    prev = st.get("phase")
    if prev and prev in st["phase_timings"] and st["phase_timings"][prev].get("ended") is None:
        st["phase_timings"][prev]["ended"] = now
    skipped = st.get("skipped_phases") or []
    from al_transitions import COMPLETION_EDGES
    occurrences = st.setdefault('phase_occurrences', [])
    complete = bool(occurrences and occurrences[-1]['ended'] is None and phase in COMPLETION_EDGES.get(prev, set()))
    if complete and prev not in st['completed_phases']:
        st['completed_phases'].append(prev)
    if occurrences and occurrences[-1]['ended'] is None:
        occurrences[-1].update(ended=now, completed=complete)
    occurrences.append({'phase': phase, 'started': now, 'ended': None, 'completed': False})
    st["phase"] = phase
    st["phase_timings"].setdefault(phase, {"started": now, "ended": None})
    if st["phase_timings"][phase].get("ended") is not None:  # re-entered phase (loop)
        st["phase_timings"][phase] = {"started": now, "ended": None}
    agents = agents or ([PHASE_AGENT[phase]] if phase in PHASE_AGENT else [])
    st["agents"] = agents
    st["handoffs"] = (st.get("handoffs") or 0) + len([r for r in agents if r in al_models.POLICY])
    ran = st.setdefault("roles_run", [])
    for role in agents:
        if role in al_models.POLICY and role not in ran:
            ran.append(role)
    if mode:
        st.setdefault("exec_mode", {})[phase] = mode
    refresh_active(st)
    st["pending_phases"] = [p for p in MAIN_PHASES
                            if p not in st["completed_phases"] and p not in skipped]


def refresh_active(st):
    """Recompute agent/model/effort for the active role without touching timings."""
    roles = [r for r in st.get("agents", []) if r in al_models.POLICY]
    if not roles:
        st["agent"], st["model"], st["effort"] = "AL", None, None
        return
    slot = al_models.resolve(st, roles[0])
    st["agent"] = al_models.display(roles[0])
    st["model"], st["effort"] = slot["model"], slot["effort"]

