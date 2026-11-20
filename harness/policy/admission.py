"Short epoch/SQLite transactions linearize authority, budgets and irreversible starts."

import json
import secrets
from contextlib import closing, contextmanager

from harness.controller import HarnessController
from harness.db import connect, transaction, utcnow
from harness.execution_contract import canonical
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_epochs import require_active_epoch
from harness.execution_store import get_execution
from harness.policy import capabilities, claims, domains
from harness.policy.composition import (
    effective_pool,
    evaluate,
    inherited_roles,
    membership,
    select,
    selected_ceilings,
)
from harness.policy.contracts import PolicyError, require
from harness.policy.deadlines import deadline
from harness.reliability.timeline_store import link


def audit(conn, execution, intent, verdict, code, digest):
    did = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO policy_decisions VALUES (?,?,?,?,?,?,?,?)",
        (
            did,
            execution["id"],
            execution["epoch_id"],
            intent.id if verdict == "allow" else None,
            verdict,
            code,
            digest,
            utcnow(),
        ),
    )
    link(
        conn,
        execution,
        source_kind="policy",
        source_id=did,
        event_kind="policy.allowed" if verdict == "allow" else "policy.denied",
        provenance="policy_authority",
        principal_id=intent.principal_id,
        parent_observation_id=intent.observation_id,
        reference_id=did,
    )
    if verdict == "deny" and code == "BUDGET_EXHAUSTED":
        from harness.reliability.catalog import record_budget_decision

        record_budget_decision(conn, did)
    return did


class Attempt:
    def __init__(self, operation, principal, intent, digest, requested=300, *, legacy=False):
        self.legacy = legacy
        self.control = None
        self.operation, self.principal, self.intent, self.digest = (
            operation,
            dict(principal),
            intent,
            digest,
        )
        self.owner_key = operation._policy_owner
        self.supervisor_id = operation._policy_supervisor
        self.root_id = operation.registry.root
        with closing(connect(operation.database)) as owner_conn:
            self.worker_token = get_execution(owner_conn, intent.execution_id)["worker_token"]
        self.requested = requested
        self.deadline = None
        self.started = False
        self.process_count = 0
        self.closed_count = 0
        self.versions = ()
        self.retry_fenced = False
        self.owner_done = False
        self.owner_outcome = "returned"

    @contextmanager
    def authority(self, *, settlement=False):
        operation = self.operation
        with closing(connect(operation.database)) as conn:
            execution = get_execution(conn, self.intent.execution_id)
            with epoch_action_lock(execution), transaction(conn):
                current = require_active_epoch(
                    conn,
                    self.intent.execution_id,
                    expected_epoch_id=self.intent.epoch_id,
                    expected_authority_generation=self.intent.generation,
                    mutation=True,
                )
                if settlement:
                    from harness.execution_authority import require_execution_authority

                    require(
                        getattr(operation, "_policy_owner", None) is self.owner_key
                        and getattr(operation, "_policy_supervisor", None) == self.supervisor_id
                        and operation.registry.root == self.root_id
                        and current["worker_token"] == self.worker_token,
                        "AUTHORITY_STALE",
                    )
                    root = json.loads((operation.registry.directory / "root.json").read_bytes())
                    require(
                        root
                        == {
                            "thread_id": self.root_id,
                            "binding": operation.registry.binding,
                            "policy": operation.registry.policy,
                        },
                        "AUTHORITY_STALE",
                    )
                    require_execution_authority(
                        HarnessController(conn), self.intent.execution_id, operation="read"
                    )
                else:
                    _, self._authority_state = operation.authority(
                        HarnessController(conn), self.principal
                    )
                    if self.intent.task_id and self.principal["role"] != "orchestrator":
                        from harness.policy.context import scope

                        with scope(self):
                            operation.admitted(
                                HarnessController(conn),
                                self.principal,
                                self.intent.task_id,
                                self.intent.revision,
                                verify_outputs=False,
                            )
                require(canonical(current["binding"]) == self.intent.binding, "AUTHORITY_STALE")
                yield conn, current

    def current(self, conn, execution):
        from harness.policy import source_dependencies as source
        from harness.policy.resource_bindings import dependency_claims

        strict = source.selected(self.intent.claims)
        if self.intent.task_id:
            from harness.task_environment import context_revision

            require(
                context_revision(self._authority_state, self.intent.task_id)
                == self.intent.revision,
                "AUTHORITY_STALE",
            )
        if strict:
            state = self._authority_state
            expected = dependency_claims(
                self.operation._policy_repository,
                self.intent.execution_id,
                state,
                self.intent.task_id,
            )
            require(
                {canonical(source.base_claim(v)) for v in strict}
                == {canonical(v) for v in expected},
                "AUTHORITY_STALE",
            )
            require(
                all(
                    v.get("source_protocol") == source.binding_refs(self.source_support)
                    for v in strict
                ),
                "AUTHORITY_STALE",
            )
            source.validate_support(conn, self.source_support)
            source.require_predecessors_clear(conn, strict, own_attempt_id=self.intent.id)
        docs, digest = select(
            conn, execution, self.principal, roles=inherited_roles(self.operation, self.principal)
        )
        require(digest == self.digest, "AUTHORITY_STALE")
        release = self.control is not None and self.control.method == "sealed-al-writer-finish-v1"
        approvals, pool_ids = ((), ()) if release else evaluate(docs, self.intent)
        if release:
            from harness.policy.control_qualification import WRITER_FIELDS

            writer = self.operation.state(execution).get("writers", {}).get(self.control.args[3])
            require(
                writer is not None
                and {key: writer.get(key) for key in WRITER_FIELDS} == self.control.writer_binding,
                "AUTHORITY_STALE",
            )
        if self.intent.retry_reference is not None:
            run, session, role, checkpoint = self.intent.retry_reference
            prior = conn.execute(
                "SELECT attempt_id FROM policy_retry_claims WHERE e"
                "xecution_id=? AND al_run_id=? AND target_role=? AN"
                "D checkpoint_ref=?",
                (self.intent.execution_id, run, role, checkpoint),
            ).fetchone()
            require(
                prior is None or prior["attempt_id"] == self.intent.id, "ATTEMPT_NOT_EXECUTABLE"
            )
        if self.intent.task_id:
            from harness.policy.resource_bindings import derive

            current_claims = derive(
                self.operation,
                self.principal,
                self.operation.state(execution),
                task_id=self.intent.task_id,
                resources=self.intent.resources,
                mutating=self.intent.mutating,
                boundary=self.intent.boundary,
            )
            current_claims = (
                source.bind_protocol(current_claims, self.source_support)
                if strict
                else current_claims
            )
            require(canonical(current_claims) == canonical(self.intent.claims), "AUTHORITY_STALE")
        member = membership(conn, execution["id"])
        ceilings = selected_ceilings(docs)
        pools = []
        for identifier in pool_ids:
            pool = conn.execute("SELECT * FROM policy_pools WHERE id=?", (identifier,)).fetchone()
            require(pool is not None)
            require(
                pool["domain"] == "named"
                or pool["owner_ref"]
                == (
                    member["root_id"]
                    if pool["domain"] == "execution_tree"
                    else member["workflow_id"]
                ),
                "UNSUPPORTED_ACCOUNTING_DOMAIN",
            )
            pool = effective_pool(pool, ceilings)
            require(
                pool["consumed"] + pool["reserved"] <= pool["maximum"],
                "BUDGET_EXHAUSTED",
            )
            pools.append(pool)
        if any(
            pool["dimension"] in {"operation_deadline_ms", "execution_elapsed_ms"} for pool in pools
        ):
            require(
                self.intent.operation in {"apply_patch", "read_file"}
                and bool(self.intent.resources)
                and all(value.startswith("file:") for value in self.intent.resources),
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )
        return approvals, pools

    def admit(self):
        error = None
        with self.authority() as (conn, execution):
            existing = conn.execute(
                "SELECT id FROM policy_attempts WHERE id=?", (self.intent.id,)
            ).fetchone()
            require(existing is None, "ATTEMPT_NOT_EXECUTABLE")
            try:
                approvals, pools = self.current(conn, execution)
                if self.legacy:
                    require(not domains.affected(conn, self.intent.claims), "AUTHORITY_STALE")
                else:
                    require(
                        self.intent.boundary == "closed_handler"
                        or self.intent.boundary
                        in {"sealed_al_retry_v1", "sealed_al_writer_finish_v1"}
                        and self.control is not None
                        and self.control.verified
                        and self.intent.boundary == self.control.method.replace("-", "_"),
                        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
                    )
                deadline(conn, pools, self.requested)
                for pool in pools:
                    units = self.units(pool)
                    require(
                        units <= pool["maximum"] - pool["consumed"] - pool["reserved"],
                        "BUDGET_EXHAUSTED",
                    )
                capabilities.validate(conn, self.intent, self.digest, approvals)
                claims.require_clear(
                    conn,
                    self.intent.claims,
                    unqualified_launch=self.intent.boundary == "unqualified_process_tree",
                )
            except PolicyError as failure:
                error = failure
                audit(conn, execution, self.intent, "deny", failure.code, self.digest)
            if error is None:
                i = self.intent
                conn.execute(
                    "INSERT INTO policy_attempts(id,execution_id,epoch_id,generation,supervisor_id,"
                    "principal_id,role,task_id,revision,observation_id,"
                    "operation,binding,policy_digest,"
                    "versions,retry_reference,resources,boundary,remote"
                    "_liability,state,admitted_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        i.id,
                        i.execution_id,
                        i.epoch_id,
                        i.generation,
                        self.supervisor_id,
                        i.principal_id,
                        i.role,
                        i.task_id,
                        i.revision,
                        i.observation_id,
                        i.operation,
                        i.binding,
                        self.digest,
                        canonical(self.versions),
                        canonical(i.retry_reference) if i.retry_reference is not None else None,
                        canonical([]),
                        i.boundary,
                        i.remote_liability,
                        "reserved",
                        utcnow(),
                    ),
                )
                if self.legacy:
                    conn.execute("UPDATE policy_attempts SET legacy=1 WHERE id=?", (i.id,))
                    conn.execute(
                        "INSERT INTO policy_legacy_admissions VALUES (?,?)",
                        (i.id, canonical(i.claims)),
                    )
                else:
                    claims.reserve(conn, i.id, i.claims)
                for pool in pools:
                    units = self.units(pool)
                    if pool["dimension"] in {"mutating_operations", "retry_attempts"}:
                        require(
                            conn.execute(
                                (
                                    "UPDATE policy_pools SET reserved=reserved+?,"
                                    "revision=revision+1 "
                                    "WHERE id"
                                    "=? AND ?<=maximum-consumed-reserved"
                                ),
                                (units, pool["id"], units),
                            ).rowcount
                            == 1,
                            "BUDGET_EXHAUSTED",
                        )
                        conn.execute(
                            "INSERT INTO policy_reservations(attempt_id,pool_id,units) "
                            "VALUES (?,?,?)",
                            (i.id, pool["id"], units),
                        )
                capabilities.validate(conn, i, self.digest, approvals, consume=True)
                audit(conn, execution, i, "allow", "ALLOWED", self.digest)
        if error:
            raise error
        return self

    def units(self, pool):
        return (
            int(self.intent.mutating)
            if pool["dimension"] == "mutating_operations"
            else int(self.intent.retry)
            if pool["dimension"] == "retry_attempts"
            else 0
        )

    def start_launcher(self):
        with self.authority() as (conn, execution):
            approvals, pools = self.current(conn, execution)
            if self.legacy:
                claims.require_clear(
                    conn,
                    self.intent.claims,
                    unqualified_launch=self.intent.boundary == "unqualified_process_tree",
                )
                require(not domains.affected(conn, self.intent.claims), "AUTHORITY_STALE")
            else:
                claims.revalidate(conn, self.intent.id, self.intent.claims)
            capabilities.validate(conn, self.intent, self.digest, approvals)
            mono, wall = deadline(conn, pools, self.requested)
            require(
                conn.execute(
                    (
                        "UPDATE policy_attempts SET launcher_permit=? WHERE id=? AND state='reser"
                        "ved' AND launcher_permit IS NULL"
                    ),
                    (secrets.token_hex(16), self.intent.id),
                ).rowcount
                == 1,
                "ATTEMPT_NOT_EXECUTABLE",
            )
            self.charge(conn, mutation_only=True)
        self.deadline = mono

    def charge(self, conn, mutation_only=False):
        rows = conn.execute(
            (
                "SELECT r.*,p.dimension FROM policy_reservations r JOIN policy_pools p ON"
                " p.id=r.pool_id WHERE r.attempt_id=? AND r.charged=0"
            ),
            (self.intent.id,),
        ).fetchall()
        for r in rows:
            if mutation_only and r["dimension"] != "mutating_operations":
                continue
            conn.execute(
                (
                    "UPDATE policy_pools SET reserved=reserved-?,consumed=consumed+?,revision"
                    "=revision+1 WHERE id=?"
                ),
                (r["units"], r["units"], r["pool_id"]),
            )
            conn.execute(
                "UPDATE policy_reservations SET charged=1 WHERE attempt_id=? AND pool_id=?",
                (self.intent.id, r["pool_id"]),
            )

    def start(self, *, retry_claim=None):
        require(not self.started, "ATTEMPT_NOT_EXECUTABLE")
        with self.authority() as (conn, execution):
            approvals, pools = self.current(conn, execution)
            if self.legacy:
                claims.require_clear(
                    conn,
                    self.intent.claims,
                    unqualified_launch=self.intent.boundary == "unqualified_process_tree",
                )
                require(not domains.affected(conn, self.intent.claims), "AUTHORITY_STALE")
            else:
                claims.revalidate(conn, self.intent.id, self.intent.claims)
            capabilities.validate(conn, self.intent, self.digest, approvals)
            mono, wall = deadline(conn, pools, self.requested)
            if self.deadline is not None:
                import time

                require(self.deadline > time.monotonic(), "BUDGET_EXHAUSTED")
                mono = min(mono, self.deadline)
                wall = min(wall, time.time() + mono - time.monotonic())
            permit = secrets.token_hex(16)
            require(
                conn.execute(
                    (
                        "UPDATE policy_attempts SET state='started',started_at=?,deadline_wall=?,"
                        "permit_id=?,retry_claim=? WHERE id=? AND state='reserved'"
                    ),
                    (utcnow(), wall, permit, retry_claim, self.intent.id),
                ).rowcount
                == 1,
                "ATTEMPT_NOT_EXECUTABLE",
            )
            self.charge(conn)
            claims.transition(conn, self.intent.id, "active")
            if retry_claim is not None:
                require(
                    self.intent.retry and self.intent.retry_reference is not None,
                    "ATTEMPT_NOT_EXECUTABLE",
                )
                run, session, role, checkpoint = self.intent.retry_reference
                conn.execute(
                    "INSERT INTO policy_retry_claims VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        self.intent.execution_id,
                        self.intent.epoch_id,
                        run,
                        session,
                        role,
                        checkpoint,
                        self.intent.id,
                        retry_claim,
                        utcnow(),
                    ),
                )
            link(
                conn,
                execution,
                source_kind="policy",
                source_id=self.intent.id,
                event_kind="policy.started",
                provenance="policy_authority",
                principal_id=self.intent.principal_id,
                parent_observation_id=self.intent.observation_id,
                reference_id=self.intent.id,
            )
        self.deadline = mono
        self.started = True
        return permit

    def owner_finished(self, outcome="returned"):
        self.owner_done = True
        if outcome != "returned" or self.owner_outcome == "returned":
            self.owner_outcome = outcome
        if not self.process_count:
            self.finish(outcome=outcome)
        else:
            self.finish_processes()

    def finish_processes(self):
        if not self.owner_done:
            return
        with closing(connect(self.operation.database)) as conn:
            pending = conn.execute(
                (
                    "SELECT 1 FROM policy_processes WHERE attempt_id=? AND closure!='owned_gr"
                    "oup_closed' LIMIT 1"
                ),
                (self.intent.id,),
            ).fetchone()
        if not pending:
            self.finish(outcome=self.owner_outcome)

    def finish(self, *, closed=True, outcome="returned"):
        with self.authority(settlement=True) as (conn, execution):
            row = conn.execute(
                "SELECT state,launcher_permit FROM policy_attempts "
                "WHERE id=? AND supervisor_id=? AND execution_id=? "
                "AND epoch_id=? AND generation=?",
                (
                    self.intent.id,
                    self.supervisor_id,
                    self.intent.execution_id,
                    self.intent.epoch_id,
                    self.intent.generation,
                ),
            ).fetchone()
            if row["state"] in {"settled", "released_proven_not_started"}:
                return
            if row["state"] == "reserved":
                # No permit exists, and this synchronous owner cancels its sole ability to start.
                require(not self.started, "ATTEMPT_NOT_EXECUTABLE")
                require(
                    row["launcher_permit"] is None or self.retry_fenced, "ATTEMPT_NOT_EXECUTABLE"
                )
                state = "unresolved" if row["launcher_permit"] else "released_proven_not_started"
                conn.execute(
                    (
                        "UPDATE policy_attempts SET state=?,settled_at=?,outcome='not_started' WH"
                        "ERE id=? AND state='reserved'"
                    ),
                    (state, utcnow(), self.intent.id),
                )
                claims.transition(
                    conn, self.intent.id, "unresolved" if row["launcher_permit"] else "closed"
                )
                if not row["launcher_permit"]:
                    conn.execute(
                        "DELETE FROM policy_legacy_admissions WHERE attempt_id=?", (self.intent.id,)
                    )
                    conn.execute(
                        "UPDATE policy_processes SET boundary_closed=1 WHERE attempt_id=? "
                        "AND launch_state='pending' AND pid IS NULL",
                        (self.intent.id,),
                    )
                for r in conn.execute(
                    "SELECT * FROM policy_reservations WHERE attempt_id=? AND charged=0",
                    (self.intent.id,),
                ):
                    conn.execute(
                        "UPDATE policy_pools SET reserved=reserved-?,revision=revision+1 "
                        "WHERE id=?",
                        (r["units"], r["pool_id"]),
                    )
                return
            closed = (
                closed
                and not conn.execute(
                    "SELECT 1 FROM policy_processes WHERE attempt_id=? "
                    "AND boundary_closed=0 LIMIT 1",
                    (self.intent.id,),
                ).fetchone()
            )
            closed = (
                closed
                and self.intent.boundary
                in {"closed_handler", "sealed_al_retry_v1", "sealed_al_writer_finish_v1"}
                and self.intent.remote_liability in {"none", "settled"}
            )
            state = "settled" if closed else "unresolved"
            if closed and self.legacy:
                conn.execute(
                    "DELETE FROM policy_legacy_admissions WHERE attempt_id=?", (self.intent.id,)
                )
            claims.transition(conn, self.intent.id, "closed" if closed else "unresolved")
            conn.execute(
                (
                    "UPDATE policy_attempts SET state=?,settled_at=?,outcome=? WHERE id=? AND"
                    " state IN ('started','unresolved')"
                ),
                (
                    state,
                    utcnow(),
                    outcome
                    if outcome in {"returned", "failed", "cancelled", "deadline", "not_started"}
                    else "failed",
                    self.intent.id,
                ),
            )
            conn.execute(
                "UPDATE policy_reservations SET observed_units=units WHERE attempt_id=?",
                (self.intent.id,),
            )
            link(
                conn,
                execution,
                source_kind="policy",
                source_id=self.intent.id,
                event_kind="policy.settled" if closed else "policy.unresolved",
                provenance="policy_authority",
                principal_id=self.intent.principal_id,
                parent_observation_id=self.intent.observation_id,
                reference_id=self.intent.id,
            )

    def release_unstarted(self):
        """Fence the sole start CAS; a competing committed start makes release refuse."""
        with self.authority(settlement=True) as (conn, execution):
            require(
                conn.execute(
                    "UPDATE policy_attempts SET state='released_proven_"
                    "not_started',settled_at=?,outcome='not_started' WH"
                    "ERE id=? AND supervisor_id=? AND state='reserved' "
                    "AND launcher_permit IS NULL",
                    (utcnow(), self.intent.id, self.supervisor_id),
                ).rowcount
                == 1,
                "ATTEMPT_NOT_EXECUTABLE",
            )
            claims.transition(conn, self.intent.id, "closed")
            conn.execute(
                "DELETE FROM policy_legacy_admissions WHERE attempt_id=?", (self.intent.id,)
            )
            conn.execute(
                "UPDATE policy_processes SET boundary_closed=1 WHERE attempt_id=? "
                "AND launch_state='pending' AND pid IS NULL",
                (self.intent.id,),
            )
            for row in conn.execute(
                "SELECT * FROM policy_reservations WHERE attempt_id=? AND charged=0",
                (self.intent.id,),
            ).fetchall():
                conn.execute(
                    "UPDATE policy_pools SET reserved=reserved-?,revision=revision+1 WHERE id=?",
                    (row["units"], row["pool_id"]),
                )
