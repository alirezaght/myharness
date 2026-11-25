"""Fresh task-attributed kernel census, independent of ambient working directories."""

import os
import subprocess
import sys
from pathlib import Path

from .activity_process import identity, process_cwd
from .execution_process import process_arguments
from .runtime_successor_activity import recorded_processes, require_operations
from .runtime_successor_channel import ProcessObservationFailure


def require_quiescent(repository, *, transaction, state, related=()):
    root = Path(repository).resolve(strict=True)
    if transaction["repository_identity"]["root"] != str(root):
        raise ValueError("successor census task identity differs")
    require_operations(transaction, state)
    subjects = [transaction, *related]
    if any(t["repository_identity"] != transaction["repository_identity"] for t in subjects):
        raise ValueError("successor census related task differs")
    context = sys.modules.get("_al_validator_context")
    if context is None or context.request.get("mode") != "snapshot":
        raise ValueError("successor census requires authenticated coordinator")
    coordinator = identity(os.getpid())
    coordinator_argv = process_arguments(os.getpid())
    result = subprocess.run(["/bin/ps", "-axo", "pid=,uid=,ppid="], capture_output=True, timeout=5)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("successor process census unavailable")
    known = {pid: metadata for pid, metadata in recorded_processes(state)}
    rows = []
    for line in result.stdout.splitlines():
        values = line.split()
        if len(values) != 3 or not all(v.isdigit() for v in values):
            raise ValueError("successor process census malformed")
        rows.append(tuple(map(int, values)))
    requesters = _requesters(rows, os.getpid())
    observed = set()
    for pid, uid, _ in rows:
        if uid != os.getuid():
            continue
        observed.add(pid)
        before = identity(pid)
        if before is None:
            if identity(pid) is not None:
                raise ValueError("successor process absence unstable")
            continue
        if before["zombie"]:
            continue
        try:
            argv = process_arguments(pid)
        except (OSError, ValueError) as error:
            try:
                after = identity(pid)
            except (OSError, ValueError):
                raise ProcessObservationFailure(pid, "argv", error, "unavailable") from None
            if after is None:
                # Preserve the existing disappearance rule. A read failure alone
                # never establishes absence, settlement or permission to proceed.
                continue
            observed_state = (
                "changed" if after.get("start") != before.get("start")
                else "zombie" if after["zombie"] else "live"
            )
            raise ProcessObservationFailure(pid, "argv", error, observed_state) from None
        managed = any(
            "_al_run_pin_context" in a
            or "_al_validator_context" in a
            or Path(a).name in {"al_state.py", "alctl", "alctl.py"}
            for a in argv
        )
        try:
            cwd = process_cwd(pid)
        except OSError:
            raise ValueError("task process cwd unavailable") from None
        if identity(pid) != before:
            raise ValueError("successor process identity changed")
        if cwd is None:
            raise ValueError("task process cwd unavailable")
        if pid in known:
            recorded = known[pid]
            if not recorded.get("start") or recorded.get("pid") != pid:
                raise ValueError("task-owned process identity unresolved")
            if recorded["start"] == before["start"]:
                raise ValueError("task-owned process is live")
        # This is protocol attribution under the already verified snapshot context,
        # not a caller/ancestor PID exclusion or evidence of task settlement.
        if before == coordinator and argv == coordinator_argv:
            continue
        managed = any(
            "_al_run_pin_context" in a
            or "_al_validator_context" in a
            or Path(a).name in {"al_state.py", "alctl", "alctl.py"}
            for a in argv
        )
        text = "\n".join(argv)
        attributed = any(
            t["session_id"] in text
            or t["run_id"] in text
            or t.get("runtime_directory", "\0") in text
            for t in subjects
        )
        within = cwd is not None and (cwd == root or cwd.is_relative_to(root))
        if managed and (attributed or str(root) in text or within):
            raise ValueError("live or queued task-managed operation")
        # The process chain that requested this preparation (the Harness supervisor that
        # launched the coordinator, typically started inside the repository) is the requester,
        # not unattributed repository activity. A managed AL operation in that chain was
        # already refused above.
        if within and pid not in requesters:
            raise ValueError("unproven repository process ownership")
    for pid in set(known) - observed:
        if identity(pid) is not None:
            raise ValueError("recorded task process omitted from census")
    return {"kind": "kernel-task-census-v2", "repository": str(root), "relevant_processes": []}


def _requesters(rows, coordinator):
    """The coordinator's ancestors in one process snapshot (pid, uid, ppid rows)."""
    parents = {pid: ppid for pid, _, ppid in rows}
    chain, current = set(), parents.get(coordinator)
    while current and current > 1 and current not in chain:
        chain.add(current)
        current = parents.get(current)
    return chain

