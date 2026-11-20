"Durable launch identity and owned closure, independent from best-effort telemetry."

import secrets

from harness.policy.contracts import require


class ProcessHooks:
    def __init__(self, attempt):
        self.attempt = attempt
        self.id = secrets.token_hex(16)
        self.launched = False
        self.instance_ref = secrets.token_hex(16)
        self.process = None

    def prepare(self, handle, observer):
        a = self.attempt
        with a.authority() as (conn, execution):
            from harness.policy.capabilities import validate
            from harness.policy.deadlines import deadline

            approvals, pools = a.current(conn, execution)
            validate(conn, a.intent, a.digest, approvals)
            bound, _ = deadline(conn, pools, a.requested)
            if a.deadline is not None:
                a.deadline = min(a.deadline, bound)
            span = getattr(observer, "span_id", None)
            observed = getattr(observer, "attempt_id", None)
            if span:
                row = conn.execute(
                    "SELECT * FROM reliability_process_spans WHERE span_id=?", (span,)
                ).fetchone()
                require(
                    row is not None
                    and row["execution_id"] == a.intent.execution_id
                    and row["epoch_id"] == a.intent.epoch_id
                    and row["authority_generation"] == a.intent.generation
                    and row["principal_id"] == a.intent.principal_id
                    and row["parent_observation_id"] == a.intent.observation_id,
                    "AUTHORITY_STALE",
                )
            conn.execute(
                "INSERT INTO policy_processes(id,attempt_id,span_id"
                ",observed_attempt_id,handle,instance_ref,launch_st"
                "ate) VALUES (?,?,?,?,?,?,'pending')",
                (self.id, a.intent.id, span, observed, handle, self.instance_ref),
            )
        a.process_count += 1
        if a.intent.retry:
            a.start_launcher()
        elif not a.started:
            a.start()
        return a.deadline

    def spawned(self, process):
        self.launched = True
        self.process = process
        with self.attempt.authority() as (conn, execution):
            conn.execute(
                "UPDATE policy_processes SET pid=?,launch_state='launched' WHERE id=?",
                (process.pid, self.id),
            )

    def finish(self, *, closed, outcome="returned"):
        a = self.attempt
        parent_reaped = self.process is not None and self.process.poll() is not None
        group_absent = False
        if parent_reaped:
            import os
            import time

            for _ in range(20):
                try:
                    os.killpg(self.process.pid, 0)
                except ProcessLookupError:
                    group_absent = True
                    break
                except OSError:
                    break
                time.sleep(0.01)
        qualified = (
            a.intent.boundary in {"sealed_al_retry_v1", "sealed_al_writer_finish_v1"}
            and a.control is not None
            and self.process is not None
            and a.control.qualifies(self.process)
        )
        closed = parent_reaped and (qualified or closed and group_absent)
        try:
            with a.authority(settlement=True) as (conn, execution):
                require(
                    conn.execute(
                        "SELECT 1 FROM policy_processes p JOIN policy_attem"
                        "pts a ON a.id=p.attempt_id WHERE p.id=? AND p.inst"
                        "ance_ref=? AND a.id=? AND a.supervisor_id=?",
                        (self.id, self.instance_ref, a.intent.id, a.supervisor_id),
                    ).fetchone(),
                    "AUTHORITY_STALE",
                )
                conn.execute(
                    "UPDATE policy_processes SET closure=?,parent_reape"
                    "d=?,owned_group_absent=?,boundary_closed=?,launch_state=CASE WHEN la"
                    "unch_state='pending' THEN 'unknown' ELSE launch_st"
                    "ate END WHERE id=? AND instance_ref=?",
                    (
                        "owned_group_closed" if closed else "unconfirmed",
                        int(parent_reaped),
                        int(group_absent),
                        int(qualified),
                        self.id,
                        self.instance_ref,
                    ),
                )
                if qualified:
                    conn.execute(
                        "UPDATE policy_control_receipts SET state='closed' "
                        "WHERE id=? AND attempt_id=?",
                        (a.control.receipt, a.intent.id),
                    )
            if closed:
                a.closed_count += 1
            if not closed:
                if a.started:
                    a.finish(closed=False, outcome=outcome)
            else:
                if outcome != "returned":
                    a.owner_outcome = outcome
                a.finish_processes()
        except Exception:
            # Durable started state is deliberately retained on any persistence/epoch failure.
            pass
