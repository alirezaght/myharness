"""Detached native-host supervision with durable launch uncertainty and replay."""

import argparse
import fcntl
import os
import queue
import secrets
import subprocess
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path

from harness.controller import HarnessController
from harness.db import connect, transaction, utcnow
from harness.execution import database_path
from harness.execution_authority import require_execution_authority
from harness.execution_bootstrap import execution_environment, module_argv
from harness.execution_contract import MAX_RECORD, canonical, digest, loads
from harness.execution_handoff import read_handoff, same_path
from harness.execution_hosts import (
    al_argv,
    al_source,
    installed_package,
    native_diagnostic,
    native_identity,
    package_fingerprint,
)
from harness.execution_logs import append_log
from harness.execution_models import TERMINAL, ExecutionConflict
from harness.execution_projection import ingest_event
from harness.execution_store import bind_host, get_execution, process_alive, update_owned
from harness.native_diagnostics import NativeStageFailure
from harness.native_diagnostics import message as native_message
from harness.native_diagnostics import preserve as native_failure
from harness.native_diagnostics import startup as native_startup


class WorkerOwnedConflict(ExecutionConflict):
    """The stable execution worker lock is held by another supervisor."""


@contextmanager
def worker_lock(execution, *, wait=False):
    path = (
        Path(execution.get("authority_directory") or Path(execution["handoff_path"]).parent)
        / "worker.lock"
    )
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise WorkerOwnedConflict("execution already has a live worker owner") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def launch_worker(
    conn,
    execution_id,
    *,
    mode="start",
    expected_epoch_id=None,
    expected_authority_generation=None,
    focus=None,
    planning_request_id=None,
):
    if focus not in {None, "tooling", "verification"} or focus and mode != "resume":
        raise ValueError("supported continuation focus required")
    execution = get_execution(conn, execution_id)
    if (expected_epoch_id, expected_authority_generation) == (None, None):
        expected_epoch_id = execution["epoch_id"]
        expected_authority_generation = execution["authority_generation"]
    from harness.execution_epoch_lock import epoch_action_lock

    with (
        worker_lock(execution),
        epoch_action_lock(execution, exclusive=True) if mode == "retry-start" else nullcontext(),
    ):
        if mode == "retry-start":
            from harness.execution_bridge import _upgrade_locked
            from harness.execution_epochs import require_active_epoch

            require_active_epoch(
                conn,
                execution_id,
                mutation=True,
                expected_epoch_id=expected_epoch_id,
                expected_authority_generation=expected_authority_generation,
            )
            _upgrade_locked(
                HarnessController(conn),
                execution_id,
                reason=(
                    "Explicit startup retry: select qualified Harness startup "
                    "and native policy support."
                ),
                startup_retry=True,
            )
        with transaction(conn):
            from harness.execution_epochs import require_active_epoch

            execution = require_active_epoch(
                conn,
                execution_id,
                expected_epoch_id=expected_epoch_id,
                expected_authority_generation=expected_authority_generation,
                mutation=True,
            )
            planning = None
            if planning_request_id is not None:
                if mode != "start" or focus is not None:
                    raise ExecutionConflict("fresh planning requires an unfocused native start")
                row = conn.execute(
                    "SELECT request,preparation,activation,state "
                    "FROM execution_runtime_transitions "
                    "WHERE execution_id=? AND request_id=?",
                    (execution_id, planning_request_id),
                ).fetchone()
                if not row or row["state"] != "ACTIVE" or not row["preparation"]:
                    raise ExecutionConflict(
                        "fresh planning requires active protected epoch selection"
                    )
                request, receipt = loads(row["request"]), loads(row["preparation"])
                planning = loads(row["activation"])
                if (
                    request.get("transition_mode")
                    not in {"fresh-planning-missing-state", "fresh-planning-intact"}
                    or receipt.get("transition_mode") != request["transition_mode"]
                    or receipt["successor_subject"]["session_id"] != execution["al_session_id"]
                    or receipt["successor_run_id"] != execution["al_run_id"]
                ):
                    raise ExecutionConflict(
                        "fresh native dispatch does not match selected recovery"
                    )
                if planning.get("fresh_dispatch"):
                    return {
                        "status": execution["status"],
                        "request_id": planning_request_id,
                        "fresh_dispatch": planning["fresh_dispatch"],
                        "native_resumed": False,
                    }
            if execution["status"] in TERMINAL:
                return {"status": execution["status"]}
            envelope = require_execution_authority(
                HarnessController(conn),
                execution_id,
                operation="resume" if mode == "resume" else "admit",
            )
            if mode == "retry-start":
                from harness.execution_start_retry import require_startup_retry

                require_startup_retry(HarnessController(conn), execution, envelope)
            if mode == "resume":
                if not execution["native_id"]:
                    raise ExecutionConflict(
                        "native identity unknown; reconcile uncertain spawn before retry"
                    )
                rows = conn.execute(
                    "SELECT child_pid,status FROM execution_attempts WHERE execution_id=?",
                    (execution_id,),
                ).fetchall()
                if any(
                    row["child_pid"]
                    and (
                        process_alive(row["child_pid"])
                        or row["status"] not in {"Exited", "Reconciled"}
                    )
                    for row in rows
                ):
                    raise ExecutionConflict(
                        "recorded native process is live or its outcome "
                        "requires explicit reconciliation"
                    )
            if mode == "start" and execution["status"] != "Reserved":
                return {"status": execution["status"]}
            if mode != "start" and execution["status"] not in {
                "Interrupted",
                "Unavailable",
                "RecoveryRequired",
                "Running",
                "WorkerOwned",
                "Launching",
            }:
                raise ExecutionConflict("execution cannot be recovered in this state")
            token, attempt = secrets.token_hex(24), "attempt-" + secrets.token_hex(12)
            now = utcnow()
            conn.execute(
                "UPDATE executions SET status=?,worker_token=?,updated_at=? WHERE id=?",
                ("WorkerOwned", token, now, execution_id),
            )
            conn.execute(
                (
                    "INSERT INTO execution_attempts "
                    "(id,execution_id,token,mode,status,created_at,updated_at,epoch_id) "
                    "VALUES (?,?,?,?,?,?,?,?)"
                ),
                (attempt, execution_id, token, mode, "Reserved", now, now, execution["epoch_id"]),
            )
            if planning is not None:
                from harness.execution_recovery_settlement import observe_recovery_settlement

                # Exclude this transaction's new, unspawned reservation. Historical
                # attempts must all have independently authenticated closure.
                observation = observe_recovery_settlement(HarnessController(conn), execution)
                unresolved = [
                    a
                    for a in observation["attempts"]
                    if a["attempt_id"] != attempt and a["disposition"] in {"unknown", "live"}
                ]
                if unresolved:
                    raise ExecutionConflict("fresh planning historical native settlement changed")
                planning["fresh_dispatch"] = {
                    "state": "reserved",
                    "attempt_id": attempt,
                    "token": token,
                }
                conn.execute(
                    "UPDATE execution_runtime_transitions SET activation=?,updated_at=? "
                    "WHERE execution_id=? AND request_id=?",
                    (canonical(planning), now, execution_id, planning_request_id),
                )
        argv = module_argv("harness.execution_worker") + [
            "--db",
            str(database_path(conn)),
            "--execution",
            execution_id,
            "--token",
            token,
            "--mode",
            mode,
        ]
        if focus:
            argv.extend(["--focus", focus])
        try:
            _spawn_worker(argv, execution, attempt)
            if planning is not None:
                from harness.execution_planning_dispatch import observe_dispatch

                observe_dispatch(conn, execution_id, token, "launching")
        except OSError:
            # Popen failed before creation; no native host was attempted by this worker.
            from harness.execution_recovery_settlement import record_not_started

            record_not_started(execution, token, stage="worker-spawn", conn=conn)
            update_owned(
                conn,
                execution_id,
                token,
                status="Unavailable",
                error=native_message("WORKER_SPAWN_FAILED"),
            )
            conn.execute(
                "UPDATE execution_attempts SET status='PreSpawnFailed',updated_at=? WHERE id=?",
                (utcnow(), attempt),
            )
            append_log(
                conn,
                execution_id,
                "worker",
                native_startup("WORKER_SPAWN_FAILED"),
            )
            return {"status": "Unavailable", "error": native_message("WORKER_SPAWN_FAILED")}
    return {"status": "WorkerOwned", "attempt_id": attempt}


def _spawn_worker(argv, execution, attempt):
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=dict(
            execution_environment(),
            HARNESS_AUTHORITY_ROOT=str(
                __import__("harness.execution_bridge", fromlist=["bridge_root"]).bridge_root(
                    execution
                )
            ),
        ),
    )


def _drain(stream, channel, output):
    control = getattr(output, "control_put", output.put)
    try:
        while True:
            raw = stream.readline(MAX_RECORD + 1)
            if not raw:
                break
            if len(raw) > MAX_RECORD or not raw.endswith(b"\n"):
                control(("error", native_message("NATIVE_STREAM_INVALID")))
                break
            output.put((channel, raw))
    except Exception:
        control(("error", native_message("NATIVE_SUPERVISION_FAILED")))
    finally:
        try:
            stream.close()
        except Exception:
            control(("error", native_message("NATIVE_SUPERVISION_FAILED")))
        finally:
            control(("closed", channel))


def replay_source(conn, execution_id, token, transport=None):
    """Ingest the AL run's outbox; with `transport` ("service" or "polling") also publish the
    step 8 live view of the same state (#31)."""
    execution = get_execution(conn, execution_id)
    if execution["worker_token"] != token:
        raise ExecutionConflict("stale_epoch: worker ownership changed before source replay")
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    # The runtime directory is fixed by the repository and host, never an event path.
    source = al_source(execution, envelope)
    if not source.is_file():
        return False
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ExecutionConflict("AL source exceeds bounded replay size")
    import json

    state = json.loads(source.read_bytes())
    i = state.get("integration")
    if (
        not i
        or not same_path(i.get("handoff"), execution["handoff_path"])
        or not same_path(state.get("identity", {}).get("root"), envelope["repository"])
    ):
        raise ExecutionConflict("AL source does not match launched handoff/repository")
    if (
        i["binding"]["al_session_id"] != execution["al_session_id"]
        or i["binding"]["al_run_id"] != state["run_id"]
    ):
        raise ExecutionConflict("foreign AL source identity")
    outbox = i.get("outbox")
    if (
        not isinstance(outbox, list)
        or type(i.get("sequence")) is not int
        or i["sequence"] != len(outbox)
    ):
        raise ExecutionConflict("invalid canonical AL outbox")
    if execution["cursor"] > len(outbox):
        raise ExecutionConflict("AL outbox regressed")
    for event in outbox[execution["cursor"] : execution["cursor"] + 200]:
        ingest_event(conn, execution_id, event)
    update_owned(conn, execution_id, token, source_contact=time.time())
    if transport is not None:
        from harness.execution_al_live import publish

        try:
            publish(execution, state, transport)
        except (OSError, ValueError, TypeError, KeyError):
            pass  # the live view is presentation only; the outbox stays authoritative
    return execution["cursor"] + 200 >= len(outbox)


def deliver_answers(controller, execution_id, package):
    """Record developer answers queued from the dashboard with AL's own `ask --answer` (#31).
    AL's `next` then tells the orchestrator to send each answer to the agent that asked."""
    from harness.execution_al_live import answer_pending

    execution = get_execution(controller.conn, execution_id)
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    environment = dict(execution_environment())

    def run_al(arguments):
        require_execution_authority(controller, execution_id, operation="decision")
        command = al_argv(
            package["launcher"],
            execution["host"],
            envelope["repository"],
            execution["al_session_id"],
            *arguments,
        )
        try:
            result = subprocess.run(
                command,
                cwd=envelope["repository"],
                env=environment,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, "", str(error)
        return (
            result.returncode,
            result.stdout.decode(errors="replace"),
            result.stderr.decode(errors="replace"),
        )

    return answer_pending(execution, run_al)


class SourceEvents:
    """Step 8's source of AL state changes: service events when available, else polling."""

    RETRY_SECONDS = 30.0
    SAFETY_SECONDS = 10.0

    def __init__(self, execution, envelope, package, notify):
        from harness.execution_al_live import environment_flag

        self.enabled = environment_flag("HARNESS_AL_SERVICE_EVENTS")
        self.source = al_source(execution, envelope)
        self.notify = notify
        self.subscription = None
        self.attempted = None
        self.locator = None
        if self.enabled:
            from harness.execution_al_service import Locator

            self.locator = Locator(
                package["launcher"],
                envelope["repository"],
                execution["host"],
                execution["al_session_id"],
            )

    @property
    def transport(self):
        return (
            "service" if self.subscription is not None and self.subscription.active else "polling"
        )

    def refresh(self):
        if not self.enabled or self.transport == "service":
            return
        now = time.monotonic()
        if self.attempted is not None and now - self.attempted < self.RETRY_SECONDS:
            return
        self.attempted = now
        from harness.execution_al_service import Subscription

        try:
            directory = self.locator()
        except (OSError, ValueError, subprocess.SubprocessError):
            directory = None
        self.subscription = Subscription.open(directory, [self.source], self.notify)

    def due(self, last_replay):
        """Whether the polling tick must replay: always without a service; with one, only as a
        slow safety net (events carry every change)."""
        interval = self.SAFETY_SECONDS if self.transport == "service" else 1.0
        return time.monotonic() - last_replay > interval

    def close(self):
        if self.subscription is not None:
            self.subscription.close()


def dispatch_pending(controller, execution_id, package):
    """Deliver explicit developer decisions through AL's guarded acknowledgement CLI."""
    from harness.execution_store import pending_deliveries

    execution = get_execution(controller.conn, execution_id)
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    environment = dict(execution_environment())
    environment["PYTHONPATH"] = (
        str(Path(__file__).resolve().parent.parent) + os.pathsep + environment.get("PYTHONPATH", "")
    )
    if package.get("sha256") and package_fingerprint(package["package"]) != package["sha256"]:
        raise ExecutionConflict(
            "installed AL package changed during execution; explicit reconciliation required"
        )
    for row in pending_deliveries(controller.conn, execution_id):
        if row["state"] != "Delivered" or row["error"]:
            continue
        path = Path(execution["handoff_path"]).parent / "inbox" / (row["id"] + ".json")
        try:
            require_execution_authority(
                controller, execution_id, operation="decision", delivery=row["id"]
            )
            command = al_argv(
                package["launcher"],
                execution["host"],
                envelope["repository"],
                execution["al_session_id"],
                "integration",
                "decision",
                "--file",
                str(path),
            )
            result = subprocess.run(
                command,
                cwd=envelope["repository"],
                env=environment,
                capture_output=True,
                timeout=10,
            )
            if result.returncode or len(result.stdout) > 65536 or len(result.stderr) > 65536:
                raise ExecutionConflict(
                    "AL decision delivery refused: " + result.stderr.decode(errors="replace")[:4096]
                )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            controller.conn.execute(
                "UPDATE execution_decisions SET error=?,updated_at=? WHERE id=?",
                (str(error), utcnow(), row["id"]),
            )


def _prompt(execution, package, envelope, mode, focus=None):
    runtime = str(al_source(execution, envelope).parent)
    launcher = " ".join(
        al_argv(
            package["launcher"],
            execution["host"],
            envelope["repository"],
            execution["al_session_id"],
            existing_run=True,
        )
    )
    focus_note = {
        None: "",
        "tooling": (
            "The developer requested tooling recovery and continuation from the dashboard. "
            "Inspect current operational issues and the updated supported Harness capabilities. "
            "Repair prerequisites within existing authority, then continue implementation. "
            "Do not repeat unchanged refusals or ask for product approval to fix tooling. "
            "Never grant yourself privileges, waive evidence, or change approved requirements. "
        ),
        "verification": (
            "The developer requested retrying pending verification from the dashboard. "
            "Use retained code and current check plans. Prepare locked isolated dependencies, "
            "and rerun relevant checks with genuine evidence. Preserve earlier failures. "
            "Do not repeat the specification interview or claim setup failures as product results. "
        ),
    }[focus]
    return focus_note + (
        "Use Harness MCP tools for all file, command, and AL state operations. "
        "Read harness:capabilities and harness:tooling first. Before dispatching a writer, "
        "inspect environment_operation; prepare required locked dependencies for its registered "
        "task and poll the setup handle. Successful preparation reports source-bound default "
        "checks; the Supervisor runs any planned broad recipe area. Pending recipe conflicts "
        "require explicit replanning, not overwriting existing checks. Setup is not test "
        "evidence. Use the supplied tools "
        "and prepared environment, without bypassing containment or repeating a failed "
        "download until capability changes. Environment issues are not product decisions. "
        "Publish a source-bound logical change diagram early through diagram_operation, "
        "showing affected components, flows and existing/changed/new elements. Use inspect "
        "to obtain its exact approval/design binding, graph_revision and complete "
        "publication_request. "
        "Read read_file paths harness:handoff, harness:task-package, harness:skill, "
        "and harness:al-state for the fixed approved documents. "
        "Read harness:skill/index to enumerate the exact protected pin. Resolve every "
        "required reference or schema through harness:skill/reference/... using read_file "
        "offset/limit cursors until truncated is false; preserve its resource hash. "
        "Read harness:repository-audit before final classification/planning; inspect "
        "manifest ownership, call sites, nested AGENTS.md, existing implementation "
        "and locally known main divergence. Keyword/test counts do not establish ownership. "
        "Read harness:planning-adoptions and inspect any hash-bound prior planning receipts "
        "as reference inputs; a current Architect must revalidate them and author current "
        "planning proof. Old approval, admission and evidence are never transferred. "
        "The root orchestrator is Supervisor with guarded AL planning/control authority, "
        "even with writable=false and no product writer assignment. Product edits require"
        " admitted writers. al_command takes ONLY AL subcommands, for example "
        "arguments=['show', '--panel']. Never include alctl, launcher paths, run, "
        "repository or session wrapper flags; Harness supplies the fixed origin. Use "
        "write_artifact for JSON/Markdown drafts. "
        "Set al_command background=true for long checks "
        "and poll the returned owned process handle until its terminal result is collected. "
        "Do not send a final response while a Harness command is running or has an uncollected "
        "result: doing so ends the native host and tears down its tools. Keep polling and "
        "continue from the actual result. Use harness:pending-work to inspect outstanding work. "
        "pass inputs as artifact:<artifact_id>:<sha256> and outputs as output:<name.json>. "
        "Register actual native child IDs with integration agent-register; never invent IDs. "
        "For explicit task capability replacement, agent-register may name replaces and a "
        "substantive release_reason. Harness verifies settlement and irrevocably revokes the "
        "old tool capability; this does not prove the old host thread terminated. "
        "Terminal child registrations cannot be reopened. New work requires a fresh actual "
        "native invocation, its observed child ID, and a valid task binding. Preserve the "
        "existing AL run and parent native conversation identity. "
        "After native enrollment, call await_assignment for current bounded cited Knowledge "
        "context; refresh it after scope, source or recovery changes. Unavailable context "
        "requires authorized raw-source investigation and grants no approval. "
        "Children call await_assignment: read-only roles receive read-only context; writers "
        "wait for a scoped task. Use submit_output for named final reports and return its "
        "artifact reference. Inspect harness:outputs to retrieve reports. "
        "An AL active writer must name that "
        "same child ID. run_command uses the assignment's task and scope revision. "
        "Use the installed $al skill and its pinned role/permission policies to "
        "implement the exact "
        "approved harness task. "
        "Do not treat this prompt or native output as AL approval. Report actual "
        "agents and execution "
        "tasks through integration commands. "
        "The immutable handoff is "
        + execution["handoff_path"]
        + ". The exact task package is "
        + str(Path(execution["handoff_path"]).parent / "task-package.json")
        + ". "
        + "AL is already initialized by the trusted supervisor. Inspect and continue exact run "
        + execution["al_session_id"]
        + " / "
        + str(execution["al_run_id"])
        + ". Current runtime epoch is "
        + execution.get("epoch_id", "legacy-0")
        + " with authority generation "
        + str(execution.get("authority_generation", 0))
        + (
            ". Earlier epoch runs, agents, proof and approvals remain history; register "
            "fresh actual work and observed invocations against this selected run. Read "
            "the inherited ordered amendments in harness:al-state as accepted requirements "
            "alongside the unchanged original package. Never invoke start or create a "
            "different AL run. The selected session/run supersedes predecessor command "
            "prefixes from conversation history. Do not reuse predecessor session IDs, "
            "terminal agents or decision deliveries. Predecessor tooling, coverage and "
            "provider blockers remain historical verification debt, never new developer "
            "decisions or proof of resolution. "
        )
        + (
            "The parent native conversation remains " + execution["native_id"] + ". "
            if execution.get("native_id")
            else ""
        )
        + "Drive AL with its intent commands (al_command arguments): ['verify'] runs the "
        "missing evidence and the base and candidate gates, then enters VERIFICATION and "
        "starts review, or names the exact blocker; ['writer', 'finish', '--id', <id>, "
        "'--outcome', 'success', '--evidence', 'auto'] finishes a writer with the checks its "
        "changes need; ['next'] shows the next step and any answered agent question to "
        "forward, and ['next', '--do'] runs it when it is a safe deterministic operation. "
        "Use the fine-grained commands only for exceptions and recovery. Checks on unchanged "
        "inputs are reused from AL's action cache and reported as reused; do not rerun them "
        "to make them look fresh. Agents record milestones with ['progress', ...] and raise "
        "questions with ['ask', ...]; when next names an answer to forward, send it to that "
        "same warm agent with the call it names, then run ['ask', '--forwarded', <QID>]. "
        + "Run every AL state command through the protected launcher as `"
        + launcher
        + " <command>`; never invoke al_state.py directly. Its run state is in "
        + runtime
        + ". "
        + (
            "Resume the existing AL run and replay its committed state; do not start another run. "
            if mode == "resume"
            else ""
        )
        + "Requirements and approved test plan are in the handoff; retain them "
        "verbatim. "
        + (
            __import__("harness.testplan_checks", fromlist=["render"]).render(
                envelope["check_recipes"]
            )
            + " "
            if envelope.get("check_recipes")
            else ""
        )
        + "Do not bypass "
        "approval, evidence or review guards. Only genuine specification ambiguities or developer "
        "engineering decisions pause affected authored work and require task-scoped developer "
        "acknowledgement. Tooling, provider, coverage and verification gaps are operational "
        "issues and proof obligations: report them with issue-update, repair what is capable, "
        "and continue authorized scoped implementation. Never represent unknown checks as PASS."
    )


def _reconnect(controller, execution, token):
    """Settle only the replay worker that actually processed under this lock/token."""
    from harness.execution_decisions import deliver_pending
    from harness.execution_store import pending_deliveries

    conn, execution_id = controller.conn, execution["id"]
    try:
        envelope = require_execution_authority(controller, execution_id, operation="launch")
        replay_source(conn, execution_id, token)
        deliver_pending(controller, execution_id)
        if pending_deliveries(conn, execution_id):
            package = installed_package(execution["host"], envelope["repository"])
            dispatch_pending(controller, execution_id, package)
            replay_source(conn, execution_id, token)
        if get_execution(conn, execution_id)["status"] not in TERMINAL:
            update_owned(
                conn,
                execution_id,
                token,
                status="RecoveryRequired",
                error=execution.get("error")
                or "replayed committed events; inspect startup evidence "
                "and current readiness before choosing an available recovery action",
            )
    except (OSError, ValueError, LookupError, subprocess.SubprocessError):
        current = get_execution(conn, execution_id)
        if current["status"] not in TERMINAL and current["worker_token"] == token:
            update_owned(
                conn,
                execution_id,
                token,
                status="Unavailable",
                error=(
                    native_failure(execution.get("error"), "NATIVE_REPLAY_FAILED")
                    if not execution["native_id"] and execution.get("error")
                    else native_message("NATIVE_REPLAY_FAILED")
                ),
            )
        append_log(conn, execution_id, "worker", native_message("NATIVE_REPLAY_FAILED"))
    finally:
        from harness.execution_recovery_settlement import record_not_started

        record_not_started(execution, token, stage="reconnect", conn=conn)
        conn.execute(
            "UPDATE execution_attempts SET status='Exited',updated_at=? "
            "WHERE execution_id=? AND token=? AND mode='reconnect' "
            "AND status IN ('Reserved','Launching','Running') AND child_pid IS NULL "
            "AND EXISTS (SELECT 1 FROM executions WHERE id=? AND worker_token=?)",
            (utcnow(), execution_id, token, execution_id, token),
        )


def package_for_native(execution, envelope, package, *, initialization=None):
    """Use the verified run pin whenever initialization has selected the runtime."""
    marker = Path(execution["handoff_path"]).parent / "bridge-selection.json"
    if initialization is None:
        if not execution["al_run_id"] and not marker.exists():
            return package
        if marker.exists():
            from harness.execution_bridge import bridge_root

            bridge_root(execution)
        from harness.execution_initialization import initialization_identity

        initialization = initialization_identity(execution, envelope)
    pinned = Path(initialization["pin_root"]) / "payload" / execution["host"] / "skills/al"
    return dict(
        package,
        package=str(pinned),
        script=str(pinned / "scripts/al_state.py"),
        sha256=package_fingerprint(pinned),
    )


def supervise(conn, execution_id, token, mode="start", focus=None):
    execution = get_execution(conn, execution_id)
    with worker_lock(execution, wait=True):
        execution = get_execution(conn, execution_id)
        if execution["worker_token"] != token:
            raise ExecutionConflict("superseded worker token")
        if execution["status"] in TERMINAL:
            return
        controller = HarnessController(conn)
        if mode == "reconnect":
            _reconnect(controller, execution, token)
            return
        try:
            envelope = require_execution_authority(
                controller, execution_id, operation="resume" if mode == "resume" else "launch"
            )
            package = installed_package(execution["host"], envelope["repository"])
            package = package_for_native(execution, envelope, package)
            previous_package = conn.execute(
                "SELECT package FROM execution_attempts "
                "WHERE execution_id=? AND epoch_id=? AND package IS NOT NULL "
                "ORDER BY rowid DESC LIMIT 1",
                (execution_id, execution["epoch_id"]),
            ).fetchone()
            if (
                previous_package
                and loads(previous_package["package"])["sha256"] != package["sha256"]
            ):
                raise ExecutionConflict(native_message("NATIVE_PACKAGE_DRIFT"))
        except (OSError, ValueError, LookupError, subprocess.SubprocessError) as exc:
            from harness.execution_recovery_settlement import record_not_started

            failure = native_failure(exc, "NATIVE_SETUP_FAILED")
            record_not_started(execution, token, stage="native-setup", conn=conn)
            update_owned(
                conn,
                execution_id,
                token,
                status="Unavailable",
                error=failure,
            )
            conn.execute(
                "UPDATE execution_attempts SET status='PreSpawnFailed',updated_at=? WHERE token=?",
                (utcnow(), token),
            )
            append_log(
                conn, execution_id, "worker", "Startup failed before native spawn: " + failure
            )
            return
        if mode == "resume":
            from harness.execution_store import process_alive

            prior = conn.execute(
                "SELECT child_pid,status FROM execution_attempts WHERE execution_id=? AND token!=?",
                (execution_id, token),
            ).fetchall()
            if any(
                row["child_pid"]
                and (
                    process_alive(row["child_pid"]) or row["status"] not in {"Exited", "Reconciled"}
                )
                for row in prior
            ):
                update_owned(
                    conn,
                    execution_id,
                    token,
                    status="RecoveryRequired",
                    error="recorded native child is live or unreconciled; "
                    "refusing competing resume",
                )
                return
        isolation_stack = ExitStack()
        native_id = (
            execution["native_id"]
            if mode == "resume"
            else (str(uuid.uuid4()) if execution["host"] == "claude" else None)
        )
        try:
            from harness.execution_initialization import initialize_execution
            from harness.native_runtime import NativeRuntime, native_auth_environment

            initialization = initialize_execution(controller, execution, package, envelope)
            package = package_for_native(
                execution, envelope, package, initialization=initialization
            )
            replay_source(conn, execution_id, token)
            runtime = NativeRuntime(
                database_path(conn),
                execution
                | {"setup_retry": token if focus in {"tooling", "verification"} else None},
                envelope,
                package,
                initialization,
                native_id,
            )
            isolation_stack.callback(runtime.close)
            env = native_auth_environment(execution["host"])
            argv = runtime.argv(envelope["repository"], resume=mode == "resume")
        except (OSError, ValueError, subprocess.SubprocessError):
            isolation_stack.close()
            from harness.execution_recovery_settlement import record_not_started

            record_not_started(execution, token, stage="native-setup", conn=conn)
            conn.execute(
                "UPDATE execution_attempts SET status='PreSpawnFailed',updated_at=? WHERE token=?",
                (utcnow(), token),
            )
            update_owned(
                conn,
                execution_id,
                token,
                status="Unavailable",
                error=native_message("NATIVE_SETUP_FAILED"),
            )
            append_log(conn, execution_id, "worker", native_startup("NATIVE_SETUP_FAILED"))
            return
        from harness.execution_epoch_lock import epoch_action_lock
        from harness.policy.native_presence import permit
        from harness.policy.source_dependencies import local_identity

        launch_manifest = digest(local_identity())
        try:
            with epoch_action_lock(execution), transaction(conn):
                launch_permit = permit(conn, execution, token, launch_manifest)
                update_owned(
                    conn, execution_id, token, status="Launching", heartbeat=time.time(), error=None
                )
                conn.execute(
                    "UPDATE execution_attempts SET status='Launching',worker_pid=?,native_id=?,"
                    "package=?,"
                    "updated_at=? WHERE token=?",
                    (os.getpid(), native_id, canonical(package), utcnow(), token),
                )
                if native_id:
                    bind_host(conn, execution_id, native_id, token)
        except ValueError:
            from harness.execution_recovery_settlement import record_not_started

            isolation_stack.close()
            record_not_started(execution, token, stage="native-setup", conn=conn)
            update_owned(
                conn,
                execution_id,
                token,
                status="Unavailable",
                error=native_message("NATIVE_SETUP_FAILED"),
            )
            return
        stage_failures = []
        closure_validated = False
        status_published = False
        readers = []
        attempt_row = conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=? AND token=?",
            (execution_id, token),
        ).fetchone()

        def signal_group(sig):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass

        def record_failure(stage, error):
            category = (
                "timeout"
                if isinstance(error, subprocess.TimeoutExpired)
                else "os-error"
                if isinstance(error, OSError)
                else "closure-refused"
                if stage == "closure-record" and isinstance(error, ValueError)
                else "unexpected-error"
            )
            stage_failures.append(
                {
                    "stage": stage,
                    "category": category,
                    "diagnostic": "NATIVE_REPLAY_FAILED"
                    if stage == "source-replay"
                    else "NATIVE_SETTLEMENT_UNAVAILABLE",
                }
            )

        def cleanup_call(stage, action):
            try:
                return True, action()
            except Exception as error:
                record_failure(stage, error)
                return False, None

        def drain_readers():
            deadline = time.monotonic() + 5
            while any(reader.is_alive() for reader in readers):
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired("native-reader", 5)
                try:
                    output.get(timeout=0.05)
                except queue.Empty:
                    pass
                for reader in readers:
                    if reader.ident is not None:
                        reader.join(timeout=0.01)

        try:
            require_execution_authority(
                controller, execution_id, operation="resume" if mode == "resume" else "launch"
            )
            from harness.policy.reconciliation import native_admission

            native_admission(conn, get_execution(conn, execution_id))
            launch_permit.begin_spawn()
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=envelope["repository"],
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError:
                launch_permit.spawn_failed()
                raise
            else:
                launch_permit.spawned()
        except (OSError, ValueError):
            from harness.execution_recovery_settlement import record_not_started

            record_not_started(
                execution, token, stage="native-spawn", conn=conn, launch_permit=launch_permit
            )
            update_owned(
                conn,
                execution_id,
                token,
                status="Unavailable",
                error=native_message("NATIVE_SPAWN_FAILED"),
            )
            conn.execute(
                "UPDATE execution_attempts SET status='PreSpawnFailed',updated_at=? WHERE token=?",
                (utcnow(), token),
            )
            isolation_stack.close()
            append_log(conn, execution_id, "worker", native_startup("NATIVE_SPAWN_FAILED"))
            return
        closed, last_poll, last_replay = 0, 0, 0
        failure = None
        stderr_seen = False
        events = None
        try:
            conn.execute(
                "UPDATE execution_attempts SET status='Running',child_pid=?,updated_at=? "
                "WHERE token=?",
                (proc.pid, utcnow(), token),
            )
            update_owned(conn, execution_id, token, status="Running")
            output = queue.Queue(maxsize=200)
            for channel, stream in (("host", proc.stdout), ("stderr", proc.stderr)):
                reader = threading.Thread(
                    target=_drain, args=(stream, channel, output), daemon=True
                )
                readers.append(reader)
                reader.start()
            try:
                proc.stdin.write(_prompt(execution, package, envelope, mode, focus).encode())
            except BrokenPipeError:
                pass  # Drain the child's actual authentication/process failure next.
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass

            def source_changed(_path):
                try:
                    output.put_nowait(("al-event", None))
                except queue.Full:
                    pass  # the next tick replays anyway

            try:
                events = SourceEvents(execution, envelope, package, source_changed)
            except (OSError, ValueError, KeyError):
                events = None
            while closed < 2 or proc.poll() is None:
                from harness.policy.reconciliation import native_deadline

                native_deadline(conn, get_execution(conn, execution_id))
                try:
                    channel, raw = output.get(timeout=0.2)
                except queue.Empty:
                    channel, raw = None, None
                if channel == "al-event":
                    replay_source(conn, execution_id, token, transport="service")
                    last_replay = time.monotonic()
                elif channel == "closed":
                    closed += 1
                elif channel == "error":
                    raise ExecutionConflict(native_failure(raw, "NATIVE_STREAM_INVALID"))
                elif channel == "host":
                    diagnostic = native_diagnostic(execution["host"], raw)
                    if diagnostic:
                        raise ExecutionConflict(diagnostic)
                    found = native_identity(execution["host"], raw)
                    if found:
                        bind_host(conn, execution_id, found, token)
                        runtime.observe(found)
                        from harness.execution_planning_dispatch import observe_dispatch

                        observe_dispatch(conn, execution_id, token, "observed", native_id=found)
                    # Native content may contain reasoning; retain only process/session facts.
                elif channel == "stderr" and not stderr_seen:
                    stderr_seen = True
                    append_log(
                        conn, execution_id, "stderr", native_message("NATIVE_STDERR_SUPPRESSED")
                    )
                if time.monotonic() - last_poll > 1:
                    runtime.tick()
                    current = get_execution(conn, execution_id)
                    if current["status"] not in TERMINAL:
                        require_execution_authority(controller, execution_id, operation="launch")
                    if events is not None:
                        events.refresh()
                    transport = events.transport if events is not None else "polling"
                    if events is None or events.due(last_replay):
                        replay_source(conn, execution_id, token, transport=transport)
                        last_replay = time.monotonic()
                    update_owned(conn, execution_id, token, heartbeat=time.time())
                    from harness.execution_decisions import deliver_pending

                    deliver_pending(controller, execution_id)
                    dispatch_pending(controller, execution_id, package)
                    if deliver_answers(controller, execution_id, package):
                        replay_source(conn, execution_id, token, transport=transport)
                    else:
                        replay_source(conn, execution_id, token)
                    last_poll = time.monotonic()
        except Exception as error:
            failure = native_failure(error)
            cleanup_call("failure-signal", lambda: signal_group(__import__("signal").SIGTERM))
            cleanup_call("failure-wait", lambda: proc.wait(timeout=5))
        finally:
            if events is not None:
                cleanup_call("runtime-close", events.close)
            # Independent disposal retains errors without skipping later owned cleanup.
            group_disposed, _ = cleanup_call(
                "final-signal", lambda: signal_group(__import__("signal").SIGKILL)
            )
            cleanup_call("final-wait", lambda: proc.wait(timeout=5))
            readers_closed, _ = cleanup_call("runtime-close", drain_readers)
            streams_closed = readers_closed
            if readers_closed:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None and not stream.closed:
                        stream_closed, _ = cleanup_call("runtime-close", stream.close)
                        streams_closed = streams_closed and stream_closed
            runtime_closed, _ = cleanup_call("runtime-close", isolation_stack.close)
            from harness.execution_settlement import closed_context, record_closed_context

            def publish_closure():
                current = get_execution(conn, execution_id)
                if (
                    current["worker_token"] != token
                    or current["epoch_id"] != execution["epoch_id"]
                    or current["authority_generation"] != execution["authority_generation"]
                ):
                    raise ExecutionConflict("worker closure ownership was lost")
                record_closed_context(current, token, proc, runtime)

            if group_disposed and streams_closed and runtime_closed:
                cleanup_call("closure-record", publish_closure)
                if attempt_row:
                    current_attempt = conn.execute(
                        "SELECT * FROM execution_attempts WHERE execution_id=? AND token=?",
                        (execution_id, token),
                    ).fetchone()
                    valid, observation = cleanup_call(
                        "closure-record",
                        lambda: closed_context(get_execution(conn, execution_id), current_attempt),
                    )
                    closure_validated = valid and observation is True
                    if not closure_validated:
                        record_failure(
                            "closure-record", ValueError("closure validation unavailable")
                        )
            else:
                record_failure("closure-record", ValueError("closure prerequisites unavailable"))
            for _ in range(100):
                replayed, outcome = cleanup_call(
                    "source-replay", lambda: replay_source(conn, execution_id, token)
                )
                if not replayed or outcome:
                    break

            def publish_status():
                current = get_execution(conn, execution_id)
                if current["worker_token"] != token:
                    raise ExecutionConflict("worker ownership was lost")
                recovery = (
                    bool(stage_failures)
                    or proc.returncode is None
                    or (attempt_row is not None and not closure_validated)
                )
                if (
                    current["status"] not in TERMINAL
                    or recovery
                    and current["status"] == "Completed"
                ):
                    recorded_work = None
                    if proc.returncode == 0 and not failure and not recovery:
                        from harness.execution_work_notice import authored_work_notice

                        try:
                            recorded_work = authored_work_notice(conn, execution_id)
                        except ValueError:
                            recorded_work = None
                    update_owned(
                        conn,
                        execution_id,
                        token,
                        status="RecoveryRequired" if recovery else "Interrupted",
                        error=(failure or native_message("NATIVE_SETTLEMENT_UNAVAILABLE"))
                        if recovery
                        else None
                        if recorded_work
                        else failure
                        or (
                            f"native host exited {proc.returncode}; "
                            "no guarded AL implementation result"
                        ),
                    )
                if proc.returncode is not None:
                    conn.execute(
                        "UPDATE execution_attempts SET status='Exited',updated_at=? "
                        "WHERE execution_id=? AND token=? AND EXISTS "
                        "(SELECT 1 FROM executions WHERE id=? AND worker_token=?)",
                        (utcnow(), execution_id, token, execution_id, token),
                    )

            status_published, _ = cleanup_call("status-publication", publish_status)
            if failure:
                cleanup_call(
                    "status-publication", lambda: append_log(conn, execution_id, "worker", failure)
                )
            for item in list(stage_failures):
                cleanup_call(
                    "status-publication",
                    lambda item=item: append_log(
                        conn, execution_id, "worker", native_message(item["diagnostic"])
                    ),
                )
            if stage_failures:
                raise NativeStageFailure(
                    {
                        "kind": "native-failure-stage-v1",
                        "execution_id": execution_id,
                        "attempt_id": attempt_row["id"] if attempt_row else None,
                        "failures": stage_failures,
                        "returncode_known": proc.returncode is not None,
                        "closure_validated": closure_validated,
                        "status_publication_reached": status_published,
                    }
                ) from None
    # Release worker ownership before admitting a recorded-session correction.
    # A new generation's worker owns its own publication and monitoring loop.
    try:
        from harness.execution_settlement import settle_exited_work

        settle_exited_work(controller, execution_id)
    except (OSError, ValueError, LookupError):
        append_log(conn, execution_id, "worker", native_message("NATIVE_SETTLEMENT_UNAVAILABLE"))
    monitor_completed_delivery(controller, execution_id, token)


def monitor_completed_delivery(controller, execution_id, token):
    from harness.delivery import publish_delivery, watch_delivery
    from harness.delivery_store import get_delivery, save_snapshot
    from harness.execution_logs import redact

    execution = get_execution(controller.conn, execution_id)
    if execution["status"] != "Completed" or execution["worker_token"] != token:
        return
    try:
        delivery = get_delivery(controller.conn, execution_id)
    except LookupError:
        return
    try:
        if delivery["config"]["publication_authorized"]:
            publish_delivery(controller, execution_id)
        elif not delivery["mr"]:
            return
        watch_delivery(controller, execution_id)
    except (OSError, ValueError, LookupError, subprocess.SubprocessError) as error:
        current = get_delivery(controller.conn, execution_id)
        snapshot = current["snapshot"] | {
            "blockers": [
                *current["snapshot"].get("blockers", []),
                "Delivery requires attention: " + redact(str(error)),
            ]
        }
        save_snapshot(controller, execution_id, snapshot, "Blocked")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--execution", required=True)
    p.add_argument("--token", required=True)
    p.add_argument(
        "--mode", choices=["start", "resume", "reconnect", "retry-start"], default="start"
    )
    p.add_argument("--focus", choices=["tooling", "verification"])
    a = p.parse_args()
    conn = connect(Path(a.db))
    try:
        supervise(conn, a.execution, a.token, a.mode, a.focus)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
