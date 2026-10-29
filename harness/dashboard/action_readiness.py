"""Operator capability probes; these never grant native-agent authority."""

import errno
import fcntl
import os
import secrets
import time
from pathlib import Path

from harness.command_discovery import attribution_compatibility
from harness.db import default_db_path
from harness.execution_contract import digest
from harness.execution_handoff import read_handoff

RESTART = (
    "Restart the public harness dashboard command from an ordinary trusted terminal "
    "with access to this repository and its existing private Harness state. "
    "Do not grant privileges inside the native agent."
)
_CACHE = {}


def permission_failure(error):
    return isinstance(error, PermissionError) or error.errno in {
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    }


def _directory(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        # Probe the exact destination, not a parent whose policy may differ.
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = ".harness-readiness-" + secrets.token_hex(12)
    created = False
    try:
        probe = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        created = True
        os.close(probe)
    finally:
        try:
            if created:
                os.unlink(name, dir_fd=fd)
        finally:
            os.close(fd)


def _lock(path):
    try:
        fd = os.open(
            path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "owned"
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return "available"


def probe_readiness(execution):
    checks = []
    try:
        envelope = read_handoff(execution["handoff_path"])
        directory = Path(envelope["repository"]) / "docs" / "decisions"
        key = digest(str(directory.resolve()).encode()).split(":")[1]
        lock = (
            default_db_path(directory.resolve()).parent.parent / "registry-locks" / (key + ".lock")
        )
        parent = Path(execution["handoff_path"]).parent
        authority = Path(execution.get("authority_directory") or parent)
        destinations = [
            ("worker_lock", authority / "worker.lock", True),
            ("registry_lock", lock, True),
            ("registry_destination", directory, False),
            ("decision_inbox", parent / "inbox", False),
        ]
        for capability, path, is_lock in destinations:
            try:
                state = _lock(path) if is_lock else (_directory(path) or "available")
                checks.append({"capability": capability, "state": state})
            except OSError as error:
                state = "permission_denied" if permission_failure(error) else "unavailable"
                checks.append({"capability": capability, "state": state})
    except OSError as error:
        checks.append(
            {
                "capability": "execution_state",
                "state": "permission_denied" if permission_failure(error) else "unavailable",
            }
        )
    except (ValueError, KeyError):
        checks.append({"capability": "execution_state", "state": "unavailable"})

    def reason(names, *, ownership=False):
        relevant = [c for c in checks if c["capability"] in names | {"execution_state"}]
        denied = [c["capability"] for c in relevant if c["state"] == "permission_denied"]
        if denied:
            return {
                "code": "permission_denied",
                "message": "Filesystem permission denied for " + ", ".join(denied) + ". " + RESTART,
            }
        unavailable = [c["capability"] for c in relevant if c["state"] == "unavailable"]
        if unavailable:
            return {
                "code": "unavailable",
                "message": "Filesystem capability unavailable for "
                + ", ".join(unavailable)
                + ". Inspect the recorded diagnostics before retrying.",
            }
        owned = [c["capability"] for c in relevant if c["state"] == "owned"]
        if owned and not ownership:
            return {
                "code": "worker_owned" if "worker_lock" in owned else "operation_owned",
                "message": (
                    "An execution worker already owns this session. Use Refresh recorded status "
                    "to observe its progress; do not start another continuation."
                )
                if "worker_lock" in owned
                else (
                    "Another operation currently owns the Decision Registry. "
                    "Refresh recorded status and retry after it settles."
                ),
            }
        return None

    reasons = {}
    for action, names, ownership in [
        ("reconnect", {"worker_lock"}, True),
        ("resume", {"worker_lock"}, False),
        ("decision", {"registry_lock", "registry_destination", "decision_inbox"}, False),
        ("decision-retry", {"registry_lock", "registry_destination", "decision_inbox"}, False),
    ]:
        failure = reason(names, ownership=ownership)
        if failure:
            reasons[action] = failure
    if "resume" not in reasons and execution.get("status") in {
        "Running",
        "Launching",
        "WorkerOwned",
    }:
        reasons["resume"] = {
            "code": "active_execution",
            "message": (
                "The execution is already active. Use Refresh recorded status "
                "to observe its progress; another continuation is unavailable."
            ),
        }
    if "resume" not in reasons and not execution.get("native_id"):
        reasons["resume"] = {
            "code": "missing_native_identity",
            "message": (
                "The native conversation identity is not recorded. Inspect and reconcile "
                "the recorded execution before continuing."
            ),
        }
    actions = {
        action: action not in reasons
        for action in ("reconnect", "resume", "decision", "decision-retry")
    }
    filesystem_failure = next(
        (r for r in reasons.values() if r["code"] == "permission_denied"), None
    ) or next((r for r in reasons.values() if r["code"] == "unavailable"), None)
    result = {
        "checked_at": time.time(),
        "operator_only": True,
        "checks": checks,
        "actions": actions,
        "action_reasons": reasons,
        "message": filesystem_failure["message"]
        if filesystem_failure
        else (
            "Operator filesystem capabilities checked; native discovery "
            "and implementation readiness remain unverified."
        ),
        "attribution": attribution_compatibility(),
        "native_discovery": "unverified",
    }
    _CACHE[execution["id"]] = result
    return result


def last_readiness(execution_id, pin_identity=None):
    snapshot = _CACHE.get(
        execution_id,
        {
            "checked_at": None,
            "operator_only": True,
            "checks": [],
            "actions": {},
            "message": (
                "Operator permissions have not been checked for this execution. "
                "Actions recheck before use."
            ),
            "attribution": attribution_compatibility(),
            "native_discovery": "unverified",
        },
    )

    return snapshot | {"attribution": attribution_compatibility(pin_identity)}


def require_ready(execution, action):
    result = probe_readiness(execution)
    if not result["actions"].get(action, True):
        raise ValueError(result["action_reasons"][action]["message"])
    return result
