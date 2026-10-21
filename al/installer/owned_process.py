"""Private launch receipts and bounded cleanup of identity-checked owned groups."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .owned_artifacts import read_json, write_json
from .activity_process import BSDInfo as BSDInfo, identity

SIGNALS = {signal.SIGINT, signal.SIGTERM}


def group_members(group):
    result = subprocess.run(["/bin/ps", "-axo", "pid=,pgid="], capture_output=True, timeout=2)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise OSError("owned process-group enumeration unavailable")
    rows = result.stdout.splitlines()
    if len(rows) > 100000:
        raise OSError("process enumeration limit")
    members = []
    for row in rows:
        values = row.split()
        if len(values) != 2 or not all(v.isdigit() for v in values):
            raise OSError("invalid process-group observation")
        if int(values[1]) == group:
            observed = identity(int(values[0]))
            if observed is not None:
                members.append(observed)
    return members


@contextmanager
def deferred_signals():
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


@contextmanager
def cleanup_barrier(secondary):
    previous = {number: signal.getsignal(number) for number in SIGNALS}

    def deferred(number, _frame):
        secondary.append(signal.Signals(number).name)

    for number in SIGNALS:
        signal.signal(number, deferred)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        yield
    finally:
        for number in SIGNALS & signal.sigpending():
            signal.sigwait({number})
            secondary.append(signal.Signals(number).name)
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        for number, handler in previous.items():
            signal.signal(number, handler)


class OwnedProcess:
    def __init__(self, directory, assignment):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
            raise OSError("owned launch directory is not private")
        self.token = uuid.uuid4().hex
        self.assignment = assignment
        self.receipt = self.directory / ("ownership-" + self.token + ".json")
        self.process = None
        self.launch_identity = None

    def spawn(self, argv, *, cwd, env, output):
        child_env = dict(
            env, AL_VERIFICATION_LAUNCH_TOKEN=self.token, AL_VERIFICATION_ASSIGNMENT=self.assignment
        )
        from .owned_boot import command_for

        command = command_for(self.receipt, argv)
        with deferred_signals():
            self.process = subprocess.Popen(
                command,
                cwd=cwd,
                env=child_env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.capture_identity()
        return self.process

    def capture_identity(self, timeout=2):
        if self.launch_identity:
            return self.launch_identity
        if self.process is None:
            raise OSError("owned child not spawned")
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if self.receipt.exists():
                receipt = read_json(self.receipt, limit=4096)
                if (
                    receipt.get("schema_version") != 1
                    or receipt.get("token") != self.token
                    or receipt.get("assignment") != self.assignment
                ):
                    raise OSError("foreign owned launch receipt")
                launch = receipt.get("identity", {})
                if (
                    launch.get("pid") != self.process.pid
                    or launch.get("pgid") != self.process.pid
                    or launch.get("sid") != self.process.pid
                ):
                    raise OSError("owned launch group identity mismatch")
                observed = identity(self.process.pid)
                if observed is not None and observed != launch:
                    # A live child may become a zombie between the two reads.
                    if {k: v for k, v in observed.items() if k != "zombie"} != {
                        k: v for k, v in launch.items() if k != "zombie"
                    }:
                        raise OSError("owned launch process identity changed")
                self.launch_identity = launch
                return launch
            if self.process.poll() is not None:
                break
            time.sleep(0.01)
        raise OSError("missing owned launch identity receipt")

    def record(self):
        return {
            "pid": self.process.pid if self.process else None,
            "token": self.token,
            "assignment": self.assignment,
            "identity": self.launch_identity,
            "receipt": str(self.receipt),
        }

    def members(self):
        if self.process is None:
            return []
        members = group_members(self.process.pid)
        if not members:
            return []
        launch = self.capture_identity(timeout=0.2)
        for member in members:
            if (
                member["pgid"] != launch["pgid"]
                or member["sid"] != launch["sid"]
                or member["start"] < launch["start"]
            ):
                raise OSError("foreign/reused owned process-group identity")
            if member["pid"] == launch["pid"] and member["start"] != launch["start"]:
                raise OSError("owned leader PID reused")
        return [m for m in members if not m["zombie"]]

    def cleanup(self):
        errors, unresolved, secondary = [], [], []
        if self.process is None:
            return {"complete": True, "errors": [], "unresolved": [], "secondary_signals": []}
        with cleanup_barrier(secondary):
            for number, grace in ((signal.SIGTERM, 1), (signal.SIGKILL, 2)):
                try:
                    members = self.members()
                    if not members:
                        break
                    os.killpg(self.process.pid, number)
                except ProcessLookupError:
                    continue
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    subprocess.SubprocessError,
                ) as exc:
                    errors.append(str(exc))
                    break
                until = time.monotonic() + grace
                while time.monotonic() < until:
                    self.process.poll()
                    try:
                        if not self.members():
                            break
                    except (
                        OSError,
                        ValueError,
                        KeyError,
                        TypeError,
                        subprocess.SubprocessError,
                    ) as exc:
                        errors.append(str(exc))
                        break
                    time.sleep(0.02)
            try:
                unresolved = self.members()
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
                unresolved = [self.record()]
            if not unresolved:
                try:
                    self.process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    errors.append("owned leader not reaped")
                    unresolved = [self.record()]
            for number in SIGNALS & signal.sigpending():
                signal.sigwait({number})
                secondary.append(signal.Signals(number).name)
        return {
            "complete": not errors and not unresolved,
            "errors": errors,
            "unresolved": unresolved,
            "secondary_signals": secondary,
        }


def execute(argv, *, cwd, env, output, directory, assignment, timeout):
    """Run one owned collection/tool process, retaining even failed publication."""
    owner = OwnedProcess(directory, assignment)
    path = Path(directory) / "process-journal.json"
    journal = {
        "schema_version": 1,
        "status": "RUNNING",
        "owner_pid": os.getpid(),
        "assignment": assignment,
        "ownership": None,
    }
    write_json(path, journal)
    failure = None
    try:
        owner.spawn(argv, cwd=cwd, env=env, output=output)
        journal["ownership"] = owner.record()
        write_json(path, journal)
        owner.process.wait(timeout=timeout)
    except BaseException as exc:
        failure = exc
    cleanup = owner.cleanup()
    if failure is None and cleanup["secondary_signals"]:
        failure = KeyboardInterrupt(cleanup["secondary_signals"][0])
    journal.update(
        status="FINISHED" if cleanup["complete"] else "PENDING",
        cleanup=cleanup,
        cause=None
        if failure is None
        else {"kind": type(failure).__name__, "message": str(failure)},
        ownership=owner.record(),
    )
    try:
        write_json(path, journal)
    except BaseException as exc:
        if failure is not None:
            failure.add_note("owned journal finalization failed: " + str(exc))
        else:
            failure = exc
    if failure is not None:
        raise failure
    if not cleanup["complete"]:
        raise OSError("owned collection cleanup uncertain: " + str(path))
    return owner
