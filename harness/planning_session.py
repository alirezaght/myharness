"""One cooperative planning owner with durable foreground delivery and fenced results."""

import time
import uuid
from contextlib import contextmanager

from harness.authoring import AuthoringService, project_identity
from harness.authoring_contract import exact, request_identity, text
from harness.authoring_transaction import committing
from harness.db import utcnow
from harness.decision_workflow import discard_on_failure
from harness.planning_session_clock import boot_observation, require_boot_identity
from harness.planning_session_contract import (
    BUSY_LEASE_SECONDS,
    SessionError,
    validate_request,
)
from harness.planning_session_delivery import SessionDelivery
from harness.planning_session_events import mutation_scope
from harness.planning_session_identity import read_capability, verifier, verify_capability
from harness.planning_session_store import PlanningSessionStore, clock_state
from harness.registry_mutation import registry_mutation


class PlanningSessionService(SessionDelivery):
    def __init__(self, controller, root, task_id=None, *, clock=None):
        self.controller, self.conn = controller, controller.conn
        self.root = __import__("pathlib").Path(root).resolve()
        self.project, self.task_id = project_identity(self.root), task_id
        self.clock = clock or (lambda: (time.time(), time.monotonic()))

    @property
    def store(self):
        return PlanningSessionStore(self.conn, self.task_id, self.project)

    def base(self):
        return self.store.base()

    def status(self, **kwargs):
        return self.store.snapshot(clock=self.clock, repository=str(self.root), **kwargs)

    @contextmanager
    def _transaction(self, task_id=None, origin="attached_agent"):
        with (
            registry_mutation() as mutations,
            discard_on_failure() as written,
            committing(self.conn),
            mutation_scope(self.conn, task_id or self.task_id, origin) as scope,
        ):
            service = (
                AuthoringService(self.controller, self.root, self.task_id) if self.task_id else None
            )
            if service:
                service._registry_mutations, service._decision_records = mutations, written
            yield service, scope

    def _handle(self, capability, caller):
        from harness.planning_session_identity import resolve_caller

        observed = resolve_caller(
            caller.get("provider"),
            {
                "CODEX_SESSION_ID": caller.get("root_session_id"),
                "CODEX_THREAD_ID": caller.get("current_thread_id"),
            },
            caller.get("root_session_id") if caller.get("provider") == "claude" else None,
        )
        if caller != observed:
            raise SessionError("SESSION_IDENTITY_UNAVAILABLE")
        handle = read_capability(capability["path"], caller)
        if __import__("pathlib").Path(handle["path"]).is_relative_to(self.root):
            raise SessionError("SESSION_CAPABILITY_INVALID")
        if handle["project"] != self.project:
            raise SessionError("SESSION_CAPABILITY_INVALID")
        return handle

    def _authorize(self, request, caller, capability, *, live=True):
        owner = self.store.owner()
        epoch = self.store.epoch()
        handle = self._handle(capability, caller)
        if (
            not owner
            or owner["mode"] != "attached"
            or request["task"] != self.task_id
            or request["project"] != self.project
            or request["epoch"] != owner["epoch"]
            or request["attachment_id"] != owner["attachment_id"]
            or not epoch
        ):
            raise SessionError("SESSION_OWNER_LOST")
        if (
            any(
                epoch[k] != caller.get(k)
                for k in ("provider", "root_session_id", "current_thread_id")
            )
            or handle["attachment_id"] != epoch["attachment_id"]
            or not verify_capability(handle, epoch["capability_verifier"])
        ):
            raise SessionError("SESSION_CAPABILITY_INVALID")
        if live:
            observation = boot_observation()
            state = clock_state(self.store.contact(), *self.clock(), observation)
            if state == "clock_unavailable":
                require_boot_identity(observation)
            if state in {"expired", "clock_uncertain", "disconnected"}:
                raise SessionError(
                    "SESSION_CLOCK_UNCERTAIN"
                    if state == "clock_uncertain"
                    else "SESSION_LEASE_EXPIRED"
                )
        return owner

    def _check_base(self, expected):
        if self.base() != expected:
            raise SessionError("SESSION_BASE_STALE", current=self.base())

    def _scope(self, op, epoch=None):
        return f"session:{self.project}:{self.task_id}:{epoch or self.store.owner()['epoch']}:{op}"

    def _unsettled(self):
        if self.conn.execute(
            (
                "SELECT 1 FROM authoring_jobs WHERE task_id=? AND "
                "kind!='attached_publication' AND (state IN ('queued','running') "
                "OR settlement!='settled') LIMIT 1"
            ),
            (self.task_id,),
        ).fetchone():
            raise SessionError("SESSION_STANDALONE_UNSETTLED")
        if self.conn.execute(
            (
                "SELECT 1 FROM authoring_attempts a JOIN authoring_jobs j ON "
                "j.id=a.job_id WHERE j.task_id=? AND "
                "j.kind!='attached_publication' AND (a.state IN "
                "('queued','running') OR a.settlement!='settled') LIMIT 1"
            ),
            (self.task_id,),
        ).fetchone():
            raise SessionError("SESSION_STANDALONE_UNSETTLED")

    def _select_owner(self, handle, caller):
        old = self.store.owner()
        epoch = old["epoch"] + 1 if old else 1
        if self.conn.execute(
            "SELECT 1 FROM planning_session_epochs WHERE attachment_id=?",
            (handle["attachment_id"],),
        ).fetchone():
            raise SessionError("SESSION_CAPABILITY_INVALID")
        self.conn.execute(
            "INSERT INTO planning_session_epochs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                self.task_id,
                self.project,
                epoch,
                handle["attachment_id"],
                caller["provider"],
                caller["root_session_id"],
                caller["current_thread_id"],
                1,
                verifier(handle),
                utcnow(),
            ),
        )
        self.conn.execute(
            (
                "INSERT INTO "
                "planning_session_owners(task_id,project,mode,epoch,attachment_id)"
                " VALUES(?,?,'attached',?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "mode='attached',epoch=excluded.epoch,attachment_id=excluded.attac"
                "hment_id,status_revision=status_revision+1"
            ),
            (self.task_id, self.project, epoch, handle["attachment_id"]),
        )
        self.store.touch(*self.clock(), "busy", BUSY_LEASE_SECONDS)

    def _receipt(self, handle):
        owner = self.store.owner()
        return {
            "task": self.task_id,
            "project": self.project,
            "attachment_id": owner["attachment_id"],
            "epoch": owner["epoch"],
            "base": self.base(),
            "session": self.status(),
            "capability_path": handle["path"],
            "dashboard_path": "/?task=" + self.task_id,
            "dashboard_command": [
                "harness",
                "dashboard",
                self.task_id,
                "--db",
                self.conn.execute("PRAGMA database_list").fetchone()[2],
                "--project",
                str(self.root),
            ],
        }

    def start(self, request, caller, capability):
        exact(request, {"schema_version", "project", "request_id", "request", "complexity"})
        request_identity(request["request_id"])
        text(request["request"], "request")
        if (
            type(request["schema_version"]) is not int
            or request["schema_version"] != 1
            or request["project"] != self.project
        ):
            raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
        handle = self._handle(capability, caller)
        if handle["request_id"] != request["request_id"]:
            raise SessionError("SESSION_CAPABILITY_INVALID")
        scope = "session-start:" + self.project
        payload = request | {
            "caller": caller,
            "attachment_id": handle["attachment_id"],
            "capability_verifier": verifier(handle),
        }
        old_task = self.task_id
        try:
            with self._transaction(task_id="startup"):
                store = PlanningSessionStore(self.conn, None, self.project)
                prior = store.receipt(scope, request["request_id"], payload)
                if prior:
                    self.task_id = prior["task"]
                    return prior
                require_boot_identity()
                self.task_id = "t-" + uuid.uuid4().hex[:8]
                with mutation_scope(self.conn, self.task_id):
                    self.controller.create_task_in_transaction(
                        request["request"], request["complexity"], task_id=self.task_id
                    )
                    service = AuthoringService(self.controller, self.root, self.task_id)
                    service._initial()
                    from harness.investigation_artifacts import bind

                    bind(service)
                    self._select_owner(handle, caller)
                    self.store.append_event(
                        "session.started", {"request": request["request"]}, origin="system"
                    )
                    result = self._receipt(handle)
                    store.record_receipt(scope, request["request_id"], payload, result)
                    return result
        except BaseException:
            self.task_id = old_task
            raise

    def attach(self, request, caller, capability, *, takeover=False):
        exact(
            request,
            {"schema_version", "project", "task", "request_id", "expected", "previous_epoch"},
        )
        request_identity(request["request_id"])
        if (
            request["schema_version"] != 1
            or request["project"] != self.project
            or request["task"] != self.task_id
        ):
            raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
        handle = self._handle(capability, caller)
        if handle["request_id"] != request["request_id"]:
            raise SessionError("SESSION_CAPABILITY_INVALID")
        payload = request | {
            "takeover": takeover,
            "caller": caller,
            "attachment_id": handle["attachment_id"],
            "capability_verifier": verifier(handle),
        }
        scope = "session-attach:" + self.project + ":" + self.task_id
        with self._transaction() as (service, _):
            prior = self.store.receipt(scope, request["request_id"], payload)
            if prior:
                return prior
            self._check_base(request["expected"])
            owner = self.store.owner()
            if request["previous_epoch"] != (owner["epoch"] if owner else 0):
                raise SessionError("SESSION_OWNER_LOST")
            require_boot_identity()
            if owner and owner["mode"] == "attached" and not takeover:
                state = self.status()["state"]
                epoch = self.store.epoch()
                same_caller = all(
                    epoch[key] == caller.get(key)
                    for key in ("provider", "root_session_id", "current_thread_id")
                )
                if state not in {"expired", "clock_uncertain", "disconnected"} or not same_caller:
                    raise SessionError("SESSION_OWNER_LOST")
            from harness.investigation_eligibility import require_eligible

            require_eligible(service)
            self._unsettled()
            service._initial()
            from harness.investigation_artifacts import bind

            bind(service)
            self._select_owner(handle, caller)
            self.store.append_event(
                "session.attached", {"takeover": takeover}, origin="system", deliverable=False
            )
            result = self._receipt(handle)
            self.store.record_receipt(scope, request["request_id"], payload, result)
            return result

    def observe_repository(self, request, caller, capability):
        from harness.investigation_eligibility import require_eligible
        from harness.investigation_repository import Reader

        validate_request(request)
        scope = self._scope("observe_repository", request["epoch"])
        with self._transaction() as (service, _):
            self._authorize(request, caller, capability)
            prior = self.store.receipt(scope, request["request_id"], request)
            if prior:
                return prior
            self._check_base(request["expected"])
            require_eligible(service)
        from harness.investigation_artifacts import derive

        policy = derive(service)
        observation = Reader(self.root, managed_policy=policy).observe(
            self.controller.get_task(self.task_id).request
        )
        with self._transaction() as (service, _):
            self._authorize(request, caller, capability)
            prior = self.store.receipt(scope, request["request_id"], request)
            if prior:
                return prior
            self._check_base(request["expected"])
            require_eligible(service)
            from harness.investigation_artifacts import require_policy

            require_policy(service, policy)
            result = {"task": self.task_id, "base": self.base(), "repository": observation}
            self.store.record_receipt(scope, request["request_id"], request, result)
            return result

    def prepare_interview(self, request, caller, capability):
        from harness.analyzer import analyze
        from harness.investigation_eligibility import require_eligible
        from harness.investigation_policy import parse_issue
        from harness.phases import Phase

        validate_request(request)
        scope = self._scope("prepare_interview", request["epoch"])

        def checked(service):
            self._authorize(request, caller, capability)
            prior = self.store.receipt(scope, request["request_id"], request)
            if prior:
                return prior
            self._check_base(request["expected"])
            require_eligible(service)
            task = self.controller.get_task(self.task_id)
            if parse_issue(task.request) or task.phase not in {
                Phase.INVESTIGATION,
                Phase.INTERVIEW,
            }:
                raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
            return None

        with self._transaction() as (service, _):
            prior = checked(service)
            if prior:
                return prior
            checkpoint = self.controller.workspace(self.task_id).latest_checkpoint("investigation")
            needs_report = checkpoint is None
        report = (
            analyze(self.root, self.controller.get_task(self.task_id).request)
            if needs_report
            else None
        )
        with self._transaction() as (service, _):
            prior = checked(service)
            if prior:
                return prior
            if report is None and self.controller.get_task(self.task_id).phase is Phase.INTERVIEW:
                result = {"task": self.task_id, "phase": "interview", "base": self.base()}
                self.store.record_receipt(scope, request["request_id"], request, result)
                return result
            if report is not None:
                self.controller.record_investigation_in_transaction(self.task_id, report)
            if self.controller.get_task(self.task_id).phase is Phase.INVESTIGATION:
                service._planning({"op": "advance"})
            service.store.bump()
            self.store.append_event(
                "session.interview_prepared", {"phase": "interview"}, deliverable=False
            )
            result = {"task": self.task_id, "phase": "interview", "base": self.base()}
            self.store.record_receipt(scope, request["request_id"], request, result)
            return result

    def detach(self, request, caller, capability, *, destination):
        validate_request(request)
        if destination not in {"manual", "standalone"}:
            raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
        with self._transaction():
            handle = self._handle(capability, caller)
            epoch = self.store.epoch(request["epoch"])
            if (
                not epoch
                or epoch["attachment_id"] != request["attachment_id"]
                or request["project"] != self.project
                or request["task"] != self.task_id
                or any(
                    epoch[k] != caller[k]
                    for k in ("provider", "root_session_id", "current_thread_id")
                )
                or not verify_capability(handle, epoch["capability_verifier"])
            ):
                raise SessionError("SESSION_CAPABILITY_INVALID")
            scope, payload = (
                self._scope("detach", request["epoch"]),
                request | {"destination": destination},
            )
            prior = self.store.receipt(scope, request["request_id"], payload)
            if prior:
                return prior
            self._authorize(request, caller, capability)
            self._check_base(request["expected"])
            self._unsettled()
            self.conn.execute(
                (
                    "UPDATE planning_session_owners SET "
                    "mode=?,epoch=epoch+1,attachment_id=NULL,status_revision=status_re"
                    "vision+1 WHERE task_id=?"
                ),
                (destination, self.task_id),
            )
            self.store.append_event(
                "session.detached", {"destination": destination}, deliverable=False
            )
            result = {"task": self.task_id, "base": self.base(), "session": self.status()}
            self.store.record_receipt(scope, request["request_id"], payload, result)
            return result
