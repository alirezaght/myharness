"""One server-owned, database-locked authoring worker; unknown attempts are never replayed."""

import fcntl
import json
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path

from harness.authoring import AuthoringService, TransactionInterview, project_identity
from harness.authoring_jobs import (
    apply_result,
    claim_next,
    interrupt_owned,
    job_row,
    matches,
    queue_job,
    settle,
)
from harness.authoring_native import NativeAuthor
from harness.controller import HarnessController
from harness.db import connect, transaction
from harness.phases import Phase


class AuthoringWorker:
    def __init__(self, db_path, root, *, specs=None, provider_factory=NativeAuthor):
        self.db_path, self.root, self.specs = Path(db_path), Path(root), specs
        self.provider_factory = provider_factory
        self.owner = str(uuid.uuid4())
        self.stop = threading.Event()
        self.thread = None
        self.lock = None
        self.status = "stopped"
        self.lifecycle = threading.RLock()

    def start(self):
        with self.lifecycle:
            self._start_locked()

    def _start_locked(self):
        if self.stop.is_set():
            return
        if self.thread and self.thread.is_alive():
            return
        if not self.db_path.is_file() or not self._eligible():
            return
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None
        self.lock = self.db_path.with_suffix(self.db_path.suffix + ".authoring.lock").open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            self.status = "another_worker_owns_database"
            return
        self.status = "running"
        self.thread = threading.Thread(target=self._run, name="harness-authoring", daemon=False)
        self.thread.start()

    def _eligible(self):
        # Read-only admission must precede creating the lock file or migration-capable connection.
        with closing(
            sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.execute("BEGIN")
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
            }
            if "schema_migrations" not in tables:
                raise sqlite3.OperationalError("Missing schema migration metadata")
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            required = {
                "authoring_tasks",
                "authoring_graphs",
                "authoring_revisions",
                "authoring_contributions",
                "authoring_jobs",
                "authoring_attempts",
                "authoring_receipts",
                "authoring_projections",
            }
            present = required & tables
            if 18 not in versions:
                if present:
                    raise sqlite3.OperationalError("Partial authoring schema without migration 18")
                if not versions or max(versions) > 17:
                    raise sqlite3.OperationalError("Unsupported schema migration state")
                return False
            if present != required:
                raise sqlite3.OperationalError("Incomplete authoring schema for migration 18")
            project = project_identity(self.root)
            jobs = conn.execute(
                "SELECT 1 FROM authoring_jobs WHERE project=? "
                "AND state IN ('queued','running') LIMIT 1",
                (project,),
            ).fetchone()
            pending = conn.execute(
                "SELECT 1 FROM authoring_projections p JOIN authoring_tasks a "
                "ON a.task_id=p.task_id "
                "WHERE a.project=? AND p.state='pending' LIMIT 1",
                (project,),
            ).fetchone()
            return bool(jobs or pending)

    def _run(self):
        conn = None
        try:
            conn = connect(self.db_path)
            interrupt_owned(conn, project=project_identity(self.root))
            controller = HarnessController(conn)
            while not self.stop.is_set():
                pending = conn.execute(
                    "SELECT p.task_id FROM authoring_projections p JOIN authoring_tasks a "
                    "ON a.task_id=p.task_id WHERE p.state='pending' AND a.project=?",
                    (project_identity(self.root),),
                ).fetchall()
                for projection in pending:
                    try:
                        AuthoringService(
                            controller, self.root, projection["task_id"], specs=self.specs
                        ).project_draft()
                    except (OSError, ValueError):
                        pass  # Persisted pending intent remains available for recovery.
                claim = claim_next(conn, self.owner, project_identity(self.root))
                if not claim:
                    self.stop.wait(0.2)
                    continue
                service = None
                try:
                    service = AuthoringService(
                        controller, self.root, claim["task_id"], specs=self.specs
                    )
                    inputs = json.loads(claim["input_json"])
                    if (
                        service.project != claim["project"]
                        or service.store.base() != inputs["base"]
                    ):
                        with transaction(conn):
                            settle(service, claim, "stale", "Input revision changed before launch")
                        continue
                    if claim["kind"] == "investigate":
                        from harness.knowledge.integration import investigate_with_context

                        if not controller.workspace(service.task_id).latest_checkpoint(
                            "investigation"
                        ):
                            investigate_with_context(controller, service.task_id, self.root)
                        with transaction(conn):
                            if not matches(job_row(service, claim["id"]), claim):
                                continue
                            if controller.get_task(service.task_id).phase is Phase.INVESTIGATION:
                                controller._move(
                                    service.task_id,
                                    Phase.INVESTIGATION,
                                    Phase.INTERVIEW,
                                    "Interview startup investigation recorded",
                                )
                                TransactionInterview(controller, service.task_id).next()
                            previous = service.store.revision(service.store.binding()["working_id"])
                            # Only the untouched initial system revision can be refreshed here.
                            if previous["attribution"] == "system":
                                service._append_working(
                                    service.manager().body(proposed=True), actor="system"
                                )
                            settle(service, claim, "succeeded")
                            service.store.bump()
                            queue_job(service, "generate", claim["host"])
                        continue

                    def cancelled(service=service, claim=claim):
                        row = job_row(service, claim["id"])
                        return self.stop.is_set() or not matches(row, claim)

                    provider = self.provider_factory(claim["host"])
                    output = provider.run(inputs, cancelled=cancelled)
                    apply_result(service, claim, output, getattr(provider, "diagnostics", ()))
                except (OSError, ValueError, LookupError) as error:
                    if service:
                        with transaction(conn):
                            row = job_row(service, claim["id"])
                            if matches(row, claim):
                                # Errors raised by providers must not leak paths/environment/stderr.
                                known = str(error)
                                safe = (
                                    known
                                    if known.startswith(
                                        (
                                            "Native ",
                                            "incomplete output:",
                                            "Authoring cancelled",
                                            "unsupported authoring profile:",
                                        )
                                    )
                                    else "Authoring failed validation or provider availability"
                                )
                                settle(
                                    service,
                                    claim,
                                    "interrupted" if self.stop.is_set() else "failed",
                                    safe,
                                    settlement="settled",
                                )
                            elif row["state"] == "cancelled":
                                settle(service, claim, "cancelled", settlement="settled")
            interrupt_owned(conn, self.owner)
        except Exception:
            self.status = "failed"
            # Retain unknown claims; no automatic provider retry.
            if conn is not None:
                try:
                    interrupt_owned(conn, self.owner)
                except Exception:
                    pass
        finally:
            if conn is not None:
                conn.close()
            if self.status != "failed":
                self.status = "stopped"

    def close(self):
        with self.lifecycle:
            self._close_locked()

    def _close_locked(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=15)
        if self.thread and self.thread.is_alive():
            self.status = "shutdown_pending"
            return
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None
