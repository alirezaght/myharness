"""Current-epoch process publications; observer failures never replay a spawn."""

import secrets

from harness.db import utcnow
from harness.reliability.process_context import CURRENT
from harness.reliability.store import _publication
from harness.reliability.timeline_store import ensure_trace, link


class ProcessObserver:
    def __init__(self, context, task, revision):
        self.context, self.task, self.revision = context, task, revision if task else None
        self.span_id, self.attempt_id = secrets.token_hex(16), secrets.token_hex(16)
        self.available = True
        self.initialized = False

    def begin(self):
        if self.initialized:
            return
        context = self.context
        with _publication(context) as (conn, execution):
            parent = conn.execute(
                "SELECT * FROM reliability_observations WHERE observation_id=? AND execution_id=? "
                "AND epoch_id=? AND authority_generation=? AND principal_id=?",
                (
                    context.observation_id,
                    context.execution_id,
                    context.epoch_id,
                    context.authority_generation,
                    context.principal_id,
                ),
            ).fetchone()
            if parent is None:
                raise ValueError("process parent observation differs")
            trace, now = ensure_trace(conn, execution["id"]), utcnow()
            conn.execute(
                "INSERT INTO reliability_process_spans "
                "(span_id,attempt_id,trace_id,execution_id,epoch_id,authority_generation,"
                "principal_id,role,parent_observation_id,task_id,revision,launch_state,"
                "started_at,cleanup_status) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,'pending',?,'unobserved')",
                (
                    self.span_id,
                    self.attempt_id,
                    trace,
                    context.execution_id,
                    context.epoch_id,
                    context.authority_generation,
                    context.principal_id,
                    parent["role"],
                    context.observation_id,
                    self.task,
                    self.revision,
                    now,
                ),
            )
            self._link(conn, execution, "process.launch_pending", now)
        self.initialized = True

    def _link(self, conn, execution, kind, now):
        link(
            conn,
            execution,
            source_kind="process",
            source_id=self.attempt_id,
            event_kind=kind,
            provenance="runtime_observed",
            at=now,
            principal_id=self.context.principal_id,
            parent_observation_id=self.context.observation_id,
            parent_span_id=self.span_id,
        )

    def publish(
        self,
        kind,
        *,
        returncode=None,
        duration_ms=None,
        stop_reason=None,
        cleanup_status="unobserved",
    ):
        with _publication(self.context) as (conn, execution):
            row = conn.execute(
                "SELECT * FROM reliability_process_spans WHERE span_id=?", (self.span_id,)
            ).fetchone()
            if row is None:
                raise ValueError("process observation unavailable")
            now = utcnow()
            if kind == "process.started":
                if row["launch_state"] != "pending":
                    raise ValueError("conflicting process launch")
                conn.execute(
                    "UPDATE reliability_process_spans SET launch_state='started', "
                    "spawned_at=? WHERE span_id=?",
                    (now, self.span_id),
                )
            elif kind == "process.launch_failed":
                if row["launch_state"] != "pending":
                    raise ValueError("conflicting process launch")
                conn.execute(
                    "UPDATE reliability_process_spans SET launch_state='not_started', "
                    "terminal_state='observation_failed',finished_at=?,duration_ms=?,"
                    "stop_reason=? WHERE span_id=?",
                    (now, duration_ms, stop_reason, self.span_id),
                )
            elif kind in {"process.exited", "process.cleanup_unconfirmed"}:
                terminal = "exited" if returncode is not None else "observation_failed"
                if row["terminal_state"] is not None:
                    if (
                        row["returncode"],
                        row["duration_ms"],
                        row["stop_reason"],
                        row["cleanup_status"],
                    ) != (returncode, duration_ms, stop_reason, cleanup_status):
                        raise ValueError("conflicting process terminal observation")
                else:
                    conn.execute(
                        "UPDATE reliability_process_spans SET terminal_state=?,"
                        "finished_at=?,duration_ms=?,returncode=?,stop_reason=?,cleanup_status=? "
                        "WHERE span_id=?",
                        (
                            terminal,
                            now,
                            duration_ms,
                            returncode,
                            stop_reason,
                            cleanup_status,
                            self.span_id,
                        ),
                    )
            self._link(conn, execution, kind, now)

    def notify(self, kind, **snapshot):
        if not self.available:
            return
        try:
            self.publish(kind, **snapshot)
        except Exception:
            self.available = False


def pending_process(owner, task, revision, *, context=None):
    context = context or CURRENT.get()
    if context is None:
        return None
    if owner != context.principal_id:
        raise ValueError("process owner differs from observed principal")
    observer = ProcessObserver(context, task, revision)
    observer.begin()
    return observer


def annotate(result, observer):
    if observer and not observer.available and isinstance(result, dict):
        return result | {"telemetry_status": "unavailable"}
    return result
