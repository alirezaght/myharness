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
from contextlib import ExitStack, contextmanager
from pathlib import Path

from harness.controller import HarnessController
from harness.db import connect, transaction, utcnow
from harness.execution import database_path
from harness.execution_authority import require_execution_authority
from harness.execution_bootstrap import execution_environment, module_argv
from harness.execution_contract import MAX_RECORD, canonical, loads
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
    conn, execution_id, *, mode="start", expected_epoch_id=None, expected_authority_generation=None
):
    execution = get_execution(conn, execution_id)
    if (expected_epoch_id, expected_authority_generation) == (None, None):
        expected_epoch_id = execution["epoch_id"]
        expected_authority_generation = execution["authority_generation"]
    with worker_lock(execution):
        with transaction(conn):
            from harness.execution_epochs import require_active_epoch

            execution = require_active_epoch(
                conn,
                execution_id,
                expected_epoch_id=expected_epoch_id,
                expected_authority_generation=expected_authority_generation,
                mutation=True,
            )
            if execution["status"] in TERMINAL:
                return {"status": execution["status"]}
            require_execution_authority(
                HarnessController(conn),
                execution_id,
                operation="resume" if mode == "resume" else "admit",
            )
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
        try:
            diagnostics = Path(execution["handoff_path"]).parent / ("worker-" + attempt + ".log")
            descriptor = os.open(diagnostics, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with open(os.devnull, "rb") as stdin, os.fdopen(descriptor, "ab") as output:
                subprocess.Popen(
                    argv,
                    stdin=stdin,
                    stdout=output,
                    stderr=output,
                    close_fds=True,
                    start_new_session=True,
                    env=dict(
                        execution_environment(),
                        HARNESS_AUTHORITY_ROOT=str(
                            __import__(
                                "harness.execution_bridge", fromlist=["bridge_root"]
                            ).bridge_root(execution)
                        ),
                    ),
                )
        except OSError as error:
            # Popen failed before creation; no native host was attempted by this worker.
            update_owned(
                conn,
                execution_id,
                token,
                status="Unavailable",
                error="worker creation failed: " + str(error),
            )
            conn.execute(
                "UPDATE execution_attempts SET status='PreSpawnFailed',updated_at=? WHERE id=?",
                (utcnow(), attempt),
            )
            return {"status": "Unavailable", "error": str(error)}
    return {"status": "WorkerOwned", "attempt_id": attempt}


def _drain(stream, channel, output):
    try:
        while True:
            raw = stream.readline(MAX_RECORD + 1)
            if not raw:
                break
            if len(raw) > MAX_RECORD or not raw.endswith(b"\n"):
                output.put(("error", "oversized or partial native stream record"))
                break
            output.put((channel, raw))
    finally:
        stream.close()
        output.put(("closed", channel))


def replay_source(conn, execution_id, token):
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
    return execution["cursor"] + 200 >= len(outbox)


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


def _prompt(execution, package, envelope, mode):
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
    return (
        "Use Harness MCP tools for all file, command, and AL state operations. "
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
        "al_command accepts structured AL arguments. Use write_artifact for JSON/Markdown drafts. "
        "Set al_command background=true for long checks "
        "and poll the returned owned process handle. "
        "pass inputs as artifact:<artifact_id>:<sha256> and outputs as output:<name.json>. "
        "Register actual native child IDs with integration agent-register; never invent IDs. "
        "For explicit task capability replacement, agent-register may name replaces and a "
        "substantive release_reason. Harness verifies settlement and irrevocably revokes the "
        "old tool capability; this does not prove the old host thread terminated. "
        "Terminal child registrations cannot be reopened. New work requires a fresh actual "
        "native invocation, its observed child ID, and a valid task binding. Preserve the "
        "existing AL run and parent native conversation identity. "
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
        "verbatim. Do not bypass "
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
                error="replayed committed events; explicit recorded-session resume or "
                "reconciliation required",
            )
    except (OSError, ValueError, LookupError, subprocess.SubprocessError) as error:
        current = get_execution(conn, execution_id)
        if current["status"] not in TERMINAL and current["worker_token"] == token:
            update_owned(conn, execution_id, token, status="Unavailable", error=str(error))
        append_log(conn, execution_id, "worker", str(error))
    finally:
        conn.execute(
            "UPDATE execution_attempts SET status='Exited',updated_at=? "
            "WHERE execution_id=? AND token=? AND mode='reconnect' "
            "AND status IN ('Reserved','Launching','Running') AND child_pid IS NULL "
            "AND EXISTS (SELECT 1 FROM executions WHERE id=? AND worker_token=?)",
            (utcnow(), execution_id, token, execution_id, token),
        )


def supervise(conn, execution_id, token, mode="start"):
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
            if (
                mode == "resume"
                and (Path(execution["handoff_path"]).parent / "bridge-selection.json").exists()
            ):
                from harness.execution_bridge import bridge_root
                from harness.execution_initialization import initialization_identity

                bridge_root(execution)
                existing = initialization_identity(execution, envelope)
                pinned = Path(existing["pin_root"]) / "payload" / execution["host"] / "skills/al"
                package = dict(
                    package,
                    package=str(pinned),
                    script=str(pinned / "scripts/al_state.py"),
                    sha256=package_fingerprint(pinned),
                )
            previous_package = conn.execute(
                "SELECT package FROM execution_attempts "
                "WHERE execution_id=? AND package IS NOT NULL ORDER BY rowid DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
            if (
                previous_package
                and loads(previous_package["package"])["sha256"] != package["sha256"]
            ):
                raise ExecutionConflict(
                    "installed AL package changed since launch; explicit reconciliation required"
                )
        except (OSError, ValueError, LookupError, subprocess.SubprocessError) as error:
            update_owned(conn, execution_id, token, status="Unavailable", error=str(error))
            conn.execute(
                "UPDATE execution_attempts SET status='PreSpawnFailed',updated_at=? WHERE token=?",
                (utcnow(), token),
            )
            append_log(conn, execution_id, "worker", str(error))
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
            replay_source(conn, execution_id, token)
            runtime = NativeRuntime(
                database_path(conn), execution, envelope, package, initialization, native_id
            )
            isolation_stack.callback(runtime.close)
            env = native_auth_environment()
            argv = runtime.argv(envelope["repository"], resume=mode == "resume")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            isolation_stack.close()
            update_owned(conn, execution_id, token, status="Unavailable", error=str(error))
            return
        with transaction(conn):
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
        try:
            require_execution_authority(
                controller, execution_id, operation="resume" if mode == "resume" else "launch"
            )
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
        except (OSError, ValueError) as error:
            update_owned(conn, execution_id, token, status="Unavailable", error=str(error))
            conn.execute(
                "UPDATE execution_attempts SET status='PreSpawnFailed',updated_at=? WHERE token=?",
                (utcnow(), token),
            )
            isolation_stack.close()
            return
        conn.execute(
            "UPDATE execution_attempts SET status='Running',child_pid=?,updated_at=? WHERE token=?",
            (proc.pid, utcnow(), token),
        )
        update_owned(conn, execution_id, token, status="Running")
        output = queue.Queue(maxsize=200)
        for channel, stream in (("host", proc.stdout), ("stderr", proc.stderr)):
            threading.Thread(target=_drain, args=(stream, channel, output), daemon=True).start()
        closed, last_poll = 0, 0
        failure = None
        try:
            try:
                proc.stdin.write(_prompt(execution, package, envelope, mode).encode())
            except BrokenPipeError:
                pass  # Drain the child's actual authentication/process failure next.
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            while closed < 2 or proc.poll() is None:
                try:
                    channel, raw = output.get(timeout=0.2)
                except queue.Empty:
                    channel, raw = None, None
                if channel == "closed":
                    closed += 1
                elif channel == "error":
                    raise ExecutionConflict(raw)
                elif channel == "host":
                    diagnostic = native_diagnostic(execution["host"], raw)
                    if diagnostic:
                        raise ExecutionConflict(diagnostic)
                    found = native_identity(execution["host"], raw)
                    if found:
                        bind_host(conn, execution_id, found, token)
                        runtime.observe(found)
                    # Native content may contain reasoning; retain only process/session facts.
                elif channel == "stderr":
                    append_log(conn, execution_id, "stderr", raw.decode(errors="replace"))
                if time.monotonic() - last_poll > 1:
                    runtime.tick()
                    current = get_execution(conn, execution_id)
                    if current["status"] not in TERMINAL:
                        require_execution_authority(controller, execution_id, operation="launch")
                    replay_source(conn, execution_id, token)
                    update_owned(conn, execution_id, token, heartbeat=time.time())
                    from harness.execution_decisions import deliver_pending

                    deliver_pending(controller, execution_id)
                    dispatch_pending(controller, execution_id, package)
                    replay_source(conn, execution_id, token)
                    last_poll = time.monotonic()
        except (ValueError, OSError, LookupError) as error:
            failure = str(error)
            try:
                os.killpg(proc.pid, __import__("signal").SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, __import__("signal").SIGKILL)
                proc.wait(timeout=5)
            append_log(conn, execution_id, "worker", failure)
        finally:
            isolation_stack.close()
            try:
                for _ in range(100):
                    if replay_source(conn, execution_id, token):
                        break
            except (OSError, ValueError) as error:
                failure = failure or str(error)
                append_log(conn, execution_id, "protocol", str(error))
            current = get_execution(conn, execution_id)
            if current["status"] not in TERMINAL:
                recorded_work = None
                if proc.returncode == 0 and not failure:
                    from harness.execution_work_notice import authored_work_notice

                    try:
                        recorded_work = authored_work_notice(conn, execution_id)
                    except ValueError:
                        failure = (
                            "authored output provenance is unavailable; inspect recorded source"
                        )
                update_owned(
                    conn,
                    execution_id,
                    token,
                    status="Interrupted",
                    error=(
                        None
                        if recorded_work
                        else failure
                        or (
                            f"native host exited {proc.returncode}; "
                            "no guarded AL implementation result"
                        )
                    ),
                )
            conn.execute(
                "UPDATE execution_attempts SET status='Exited',updated_at=? WHERE token=?",
                (utcnow(), token),
            )
    # Release worker ownership before admitting a recorded-session correction.
    # A new generation's worker owns its own publication and monitoring loop.
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
    p.add_argument("--mode", choices=["start", "resume", "reconnect"], default="start")
    a = p.parse_args()
    conn = connect(Path(a.db))
    try:
        supervise(conn, a.execution, a.token, a.mode)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
