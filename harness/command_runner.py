"""Exact-request, one-use approvals and bounded subprocess audit."""

import os
import selectors
import signal
import subprocess
import time
import uuid

from harness.command_safety import classify_command, git_argv, safe_environment
from harness.db import transaction, utcnow
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical
from harness.execution_logs import redact_value
from harness.execution_store import get_execution


def approve_command(controller, request):
    """Developer-facing explicit operation approval; never called by native agents."""
    decision = classify_command(request)
    eid = request["execution_id"]
    require_execution_authority(controller, eid, operation="delivery")
    identity = "approval-" + uuid.uuid4().hex
    controller.conn.execute(
        "INSERT INTO operation_approvals VALUES (?,?,?,?,0)",
        (
            identity,
            eid,
            decision["digest"],
            canonical(get_execution(controller.conn, eid)["binding"]),
        ),
    )
    return identity


def run_command(controller, request, approval_id=None):
    decision = classify_command(request)
    value, eid = decision["request"], request.get("execution_id")
    audit_id = uuid.uuid4().hex
    controller.conn.execute(
        "INSERT INTO operation_audit VALUES (?,?,?,?,?,?)",
        (audit_id, eid, canonical(redact_value(value)), decision["decision"], None, utcnow()),
    )
    with transaction(controller.conn):
        if decision["decision"] != "read_only":
            if not eid:
                raise ValueError("operation requires execution-bound approval")
            require_execution_authority(controller, eid, operation="delivery")
            approval = controller.conn.execute(
                "SELECT * FROM operation_approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if (
                not approval
                or approval["consumed"]
                or approval["execution_id"] != eid
                or approval["request_digest"] != decision["digest"]
                or approval["binding"] != canonical(get_execution(controller.conn, eid)["binding"])
            ):
                raise ValueError("exact current unused operation approval required")
            controller.conn.execute(
                "UPDATE operation_approvals SET consumed=1 WHERE id=?", (approval_id,)
            )
    argv = value["argv"]
    if decision["decision"] == "read_only" and argv[0] == "git":
        args = argv[1:]
        if args[0] in {"diff", "show", "log"}:
            args[1:1] = ["--no-ext-diff", "--no-textconv"]
        argv = git_argv(args)
    environment = safe_environment() | value["environment"]
    proc = subprocess.Popen(
        argv,
        cwd=value["cwd"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector, chunks, size, deadline = selectors.DefaultSelector(), [], 0, time.monotonic() + 15
    for stream in (proc.stdout, proc.stderr):
        selector.register(stream, selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise ValueError("command deadline exceeded")
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                size += len(chunk)
                if size > 1048576:
                    raise ValueError("command output limit exceeded")
                chunks.append(chunk)
        proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        result = {
            "returncode": proc.returncode,
            "output": b"".join(chunks).decode(errors="replace"),
        }
    finally:
        selector.close()
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    result = redact_value(result)
    controller.conn.execute(
        "UPDATE operation_audit SET outcome=? WHERE id=?", (canonical(result), audit_id)
    )
    return result
