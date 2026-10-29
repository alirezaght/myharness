"""Fresh kernel census includes managed -c bootstraps and queued lock waiters."""

import os
import subprocess
from pathlib import Path

from .activity_process import identity, process_cwd
from .execution_process import parent_pid, process_arguments


def require_quiescent(repository, *, excluded=()):
    root = Path(repository).resolve(strict=True)
    ignored = {os.getpid(), *excluded}
    cursor = os.getpid()
    while cursor > 1:
        cursor = parent_pid(cursor)
        ignored.add(cursor)
    result = subprocess.run(["/bin/ps", "-axo", "pid=,uid="], capture_output=True, timeout=5)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("successor process census unavailable")
    relevant = []
    for line in result.stdout.splitlines():
        values = line.split()
        if len(values) != 2 or not all(v.isdigit() for v in values):
            raise ValueError("successor process census malformed")
        pid, uid = map(int, values)
        if uid != os.getuid() or pid in ignored:
            continue
        before = identity(pid)
        if before is None or before["zombie"]:
            continue
        try:
            cwd = process_cwd(pid)
            if cwd is None:
                if identity(pid) is not None:
                    raise ValueError("successor process cwd unavailable")
                continue
            if cwd != root and not cwd.is_relative_to(root):
                continue
            argv = process_arguments(pid)
            if identity(pid) != before:
                raise ValueError("successor process identity changed")
        except (FileNotFoundError, ProcessLookupError):
            continue
        # Do not identify a Python -c bootstrap by a literal al_state.py token.
        # Every other live process rooted in this worktree prevents preparation.
        relevant.append(
            {
                "pid": pid,
                "start": before["start"],
                "managed_bootstrap": any("_al_run_pin_context" in a for a in argv),
            }
        )
    if relevant:
        raise ValueError("successor preparation requires settled repository processes")
    return {"kind": "kernel-worktree-census-v1", "repository": str(root), "relevant_processes": []}
