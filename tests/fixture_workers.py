"""Own the empty native fixture's workers until verified exit, including failed tests.

Approval snapshots include worker-mutated tables, so Running is not a stable
snapshot boundary. The fixture budget covers guarded start (60s), existing-origin
validation (30s), the policy's SessionStart hook (10s), and cleanup (20s).
This is a fixture ceiling, not a production initialization SLA.
"""

import json
import signal
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

WORK_TIMEOUT = 100
CLEANUP_TIMEOUT = 20
COMPLETION_TIMEOUT = WORK_TIMEOUT + CLEANUP_TIMEOUT
OWNER_SHUTDOWN_TIMEOUT = COMPLETION_TIMEOUT + 5
EXPECTED_INTERRUPTION = "native host exited 0; no guarded AL implementation result"
_owner = None
_STATUSES = {
    "Reserved",
    "WorkerOwned",
    "Launching",
    "Running",
    "Interrupted",
    "Unavailable",
    "RecoveryRequired",
    "Completed",
    "Failed",
    "Exited",
    "Reconciled",
    "PreSpawnFailed",
    "Intent",
    "Active",
}
_PHASES = {
    "SPEC_GATE",
    "PLANNING",
    "PSEUDOCODE",
    "IMPLEMENTATION",
    "VERIFICATION",
    "REVIEW",
    "FINAL_REVIEW",
    "FINALIZATION",
    "DONE",
}


def _enum(value, allowed):
    return value if value in allowed else "Unknown"


def _observe(database, execution_id):
    from harness.execution_settlement import closed_context

    conn = sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True, timeout=0.2)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        execution = dict(
            conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
        )
        attempt = conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=? ORDER BY rowid DESC LIMIT 1",
            (execution_id,),
        ).fetchone()
        attempt = dict(attempt) if attempt else None
        initialization = conn.execute(
            "SELECT state,identity FROM execution_initializations WHERE execution_id=? "
            "ORDER BY rowid DESC LIMIT 1",
            (execution_id,),
        ).fetchone()
    finally:
        conn.close()
    phase = None
    try:
        if initialization and initialization["identity"]:
            source = json.loads(initialization["identity"]).get("source")
            if source:
                with Path(source).open("rb") as stream:
                    raw = stream.read(4 * 1024 * 1024 + 1)
                if len(raw) <= 4 * 1024 * 1024:
                    phase = json.loads(raw).get("phase")
    except (OSError, ValueError, TypeError, KeyError):
        pass  # Optional phase diagnostics cannot hide an observable exit result.
    closed, native_code = False, None
    if attempt and attempt["child_pid"]:
        try:
            closed = closed_context(execution, attempt)
            if closed:
                path = Path(execution["handoff_path"]).parent / "settlements"
                raw = (path / ("native-" + attempt["token"] + ".json")).read_bytes()
                native_code = json.loads(raw)["native_returncode"]
        except (OSError, ValueError, KeyError):
            closed = False
    facts = {
        "status": _enum(execution["status"], _STATUSES),
        "attempt_status": _enum(attempt["status"], _STATUSES) if attempt else None,
        "initialization": _enum(initialization["state"], _STATUSES) if initialization else None,
        "phase": _enum(phase, _PHASES),
        "native_pid": attempt["child_pid"] if attempt else None,
        "native_closed": closed,
        "native_returncode": native_code,
    }
    return execution, attempt, facts


def _safe_observation(database, execution_id):
    try:
        return _observe(database, execution_id)[2]
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return {"observation": "unavailable"}


def _result_code(execution, attempt, facts, returncode):
    if returncode != 0:
        return "worker_exit_nonzero"
    if execution["status"] != "Interrupted":
        return "execution_not_interrupted"
    if execution["result"] is not None:
        return "product_result_present"
    if execution["error"] != EXPECTED_INTERRUPTION:
        return "unexpected_interruption"
    if not attempt or attempt["status"] != "Exited":
        return "attempt_not_exited"
    if not facts["native_closed"]:
        return "native_context_not_closed"
    if facts["native_returncode"] != 0:
        return "native_exit_nonzero"
    return "settled_empty_fixture"


def _receipt(execution):
    return Path(execution["handoff_path"]).parent / "fixture-worker-exit.json"


def _failure(facts):
    # Only closed enums, numeric process facts and booleans enter diagnostics.
    return AssertionError(
        "approval fixture worker did not settle: " + json.dumps(facts, sort_keys=True)
    )


class _SubprocessProxy:
    def __init__(self, original, owner):
        self.original, self.owner = original, owner

    def __getattr__(self, name):
        return getattr(self.original, name)

    def Popen(self, argv, *args, **kwargs):
        process = self.original.Popen(argv, *args, **kwargs)
        if "--execution" in argv and "--token" in argv:
            self.owner._own(
                process, argv[argv.index("--db") + 1], argv[argv.index("--execution") + 1]
            )
        return process


class FixtureWorkers:
    """Retain actual Popen handles; never signal a PID obtained from SQLite."""

    def __init__(self, *, work_timeout=WORK_TIMEOUT, cleanup_timeout=CLEANUP_TIMEOUT):
        self.work_timeout, self.cleanup_timeout = work_timeout, cleanup_timeout
        self.records = []
        self.lock = threading.Lock()

    def __enter__(self):
        global _owner
        from harness import execution_worker

        if _owner is not None:
            raise AssertionError("fixture worker ownership cannot be nested")
        self.original = execution_worker.subprocess
        execution_worker.subprocess = _SubprocessProxy(self.original, self)
        _owner = self
        return self

    def _own(self, process, database, execution_id):
        record = {
            "process": process,
            "database": database,
            "execution_id": execution_id,
            "started": time.monotonic(),
            "done": threading.Event(),
        }
        with self.lock:
            self.records.append(record)
        record["thread"] = threading.Thread(target=self._finish, args=(record,), daemon=False)
        record["thread"].start()

    def _publish(self, record, *, timed_out, before, retained=False):
        process = record["process"]
        execution, attempt, facts = _observe(record["database"], record["execution_id"])
        code = (
            "worker_unresolved"
            if retained
            else "worker_timeout"
            if timed_out
            else _result_code(execution, attempt, facts, process.returncode)
        )
        facts.update(
            code=code,
            worker_pid=process.pid,
            worker_returncode=process.returncode,
            worker_reaped=not retained,
            ownership_retained=retained,
            descendants_settled=not retained and facts["native_closed"],
            elapsed=round(time.monotonic() - record["started"], 3),
        )
        if before is not None:
            facts["at_timeout"] = before
        record["facts"] = facts
        receipt = {
            "execution_id": record["execution_id"],
            "attempt_id": attempt["id"] if attempt else None,
            "facts": facts,
        }
        destination = _receipt(execution)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt), encoding="utf-8")
        temporary.replace(destination)

    def _finish(self, record):
        process = record["process"]
        timed_out, before = False, None
        try:
            try:
                process.wait(timeout=self.work_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                before = _safe_observation(record["database"], record["execution_id"])
                # Interrupt the exact Python owner so its finally blocks can
                # reap separately sessioned children and close transports.
                try:
                    process.send_signal(signal.SIGINT)
                except OSError:
                    pass
                try:
                    process.wait(timeout=self.cleanup_timeout)
                except subprocess.TimeoutExpired:
                    # Killing this supervisor could orphan its native session.
                    # Retain its actual handle and non-daemon monitor instead.
                    self._publish(record, timed_out=True, before=before, retained=True)
                    record["done"].set()  # Bounded failure decision, not settled ownership.
                    while process.poll() is None:
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            pass
            self._publish(record, timed_out=timed_out, before=before)
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
            record["facts"] = {
                "code": "observation_unavailable",
                "worker_pid": process.pid,
                "worker_returncode": process.poll(),
                "worker_reaped": process.returncode is not None,
                "ownership_retained": process.returncode is None,
                "error_kind": type(error).__name__,
                "elapsed": round(time.monotonic() - record["started"], 3),
            }
            if process.returncode is None:
                # Observation failure cannot release a still-live owned handle.
                # Keep the monitor and report uncertainty until actual exit.
                notice = Path(record["database"]).parent / f"fixture-owner-{process.pid}.json"
                try:
                    notice.write_text(json.dumps(record["facts"]), encoding="utf-8")
                except OSError:
                    pass
                record["done"].set()
                while process.poll() is None:
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                record["facts"].update(
                    worker_returncode=process.returncode,
                    worker_reaped=True,
                    ownership_retained=False,
                )
        finally:
            record["done"].set()

    def __exit__(self, exc_type, exc, traceback):
        global _owner
        from harness import execution_worker

        failures = []
        try:
            for record in self.records:
                remaining = (
                    record["started"] + self.work_timeout + self.cleanup_timeout - time.monotonic()
                )
                record["done"].wait(max(0, remaining) + 1)
                if not record.get("facts", {}).get("ownership_retained"):
                    record["thread"].join(timeout=1)
                facts = record.get("facts", {"code": "settlement_unavailable"})
                if facts.get("code") != "settled_empty_fixture":
                    failures.append(facts)
        finally:
            execution_worker.subprocess = self.original
            _owner = None
        if failures:
            raise _failure({"workers": failures})


def wait_for_fixture_execution(database, task_id, *, timeout=COMPLETION_TIMEOUT):
    """Return only after exact empty-fixture exit, context closure and owner reaping."""
    started = time.monotonic()
    facts = {"code": "execution_absent"}
    while time.monotonic() - started < timeout:
        conn = sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True, timeout=0.2)
        try:
            row = conn.execute(
                "SELECT id FROM executions WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task_id,)
            ).fetchone()
        finally:
            conn.close()
        if row:
            execution, attempt, facts = _observe(database, row[0])
            path = _receipt(execution)
            if path.is_file():
                receipt = json.loads(path.read_text(encoding="utf-8"))
                if (
                    receipt["execution_id"] != execution["id"]
                    or not attempt
                    or receipt["attempt_id"] != attempt["id"]
                ):
                    raise _failure({"code": "fixture_receipt_identity_mismatch"})
                saved = receipt["facts"]
                code = _result_code(execution, attempt, facts, saved["worker_returncode"])
                if (
                    saved["code"] != "settled_empty_fixture"
                    or code != "settled_empty_fixture"
                    or not saved["worker_reaped"]
                ):
                    raise _failure(saved | {"observed_code": code})
                if saved["worker_pid"] != attempt["worker_pid"]:
                    raise _failure({"code": "fixture_worker_identity_mismatch"})
                return execution
        time.sleep(0.05)
    raise _failure(
        facts | {"code": "completion_wait_timeout", "elapsed": round(time.monotonic() - started, 3)}
    )


def dashboard_main():
    """Run the real demo dashboard with a test-only owner around worker spawning."""
    from harness.dashboard.launch import main

    with FixtureWorkers():
        return main()


if __name__ == "__main__":
    raise SystemExit(dashboard_main())
