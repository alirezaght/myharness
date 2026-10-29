"""Supervisor performs guarded AL start before admitting any native process."""

import json
from pathlib import Path

from harness.db import utcnow
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest
from harness.execution_hosts import al_argv, al_source
from harness.execution_models import ExecutionConflict
from harness.owned_subprocess import run_owned


def initialization_identity(execution, envelope):
    source = al_source(execution, envelope)
    state = json.loads(source.read_bytes())
    origin = state.get("runtime_origin", {})
    reference = origin.get("initialization", {})
    initial_path = Path(reference.get("stored_path", "/missing"))
    raw = initial_path.read_bytes()
    import hashlib

    if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
        raise ExecutionConflict("protected AL initialization digest differs")
    initial = json.loads(raw)
    storage = initial_path.parent.parent
    candidates = []
    for path in (storage / "runs").glob("*.json"):
        value = json.loads(path.read_bytes())
        if (
            value.get("session_id") == execution["al_session_id"]
            and (value.get("repository_identity", {}).get("root") == envelope["repository"])
            and value.get("platform") == execution["host"]
        ):
            candidates.append((path, value))
    if len(candidates) != 1:
        raise ExecutionConflict("exact protected AL binding unavailable")
    path, binding = candidates[0]
    if (
        binding.get("status") != "ACTIVE"
        or binding.get("run_id") != state.get("run_id")
        or binding.get("initialization") != reference
        or binding.get("pin") != origin.get("pin")
        or initial.get("run_id") != state.get("run_id")
        or state.get("session_id") != execution["al_session_id"]
        or state.get("integration", {}).get("handoff") != execution["handoff_path"]
    ):
        raise ExecutionConflict("AL initialization/state/protected binding mismatch")
    lock = path.with_suffix(".lock")
    if not lock.is_file() or lock.is_symlink():
        raise ExecutionConflict("existing AL coordination lock unavailable")
    return {
        "binding_path": str(path),
        "binding_digest": digest(path.read_bytes()),
        "initialization_path": str(initial_path),
        "pin_root": str(storage / "pins" / binding["pin"]["pin_identity"]),
        "coordination_lock": str(lock),
        "al_run_id": state["run_id"],
        "source": str(source),
    }


def initialize_execution(controller, execution, package, envelope):
    from harness.execution_bootstrap import authority_snapshot

    authority_snapshot(Path(execution["handoff_path"]).parent)
    eid, conn = execution["id"], controller.conn
    require_execution_authority(controller, eid, operation="launch")
    conn.execute(
        (
            "INSERT INTO "
            "execution_initializations(execution_id,epoch_id,state,identity,updated_at) "
            "VALUES (?, ?,'Intent',NULL,?) ON CONFLICT(execution_id,epoch_id) DO NOTHING"
        ),
        (eid, execution["epoch_id"], utcnow()),
    )
    source = al_source(execution, envelope)
    if source.exists():
        # ACTIVE is reused. RESERVED is repaired only by the same managed start below.
        try:
            identity = initialization_identity(execution, envelope)
        except ExecutionConflict:
            identity = None
    else:
        identity = None
    if identity is None:
        argv = al_argv(
            package["launcher"],
            execution["host"],
            envelope["repository"],
            execution["al_session_id"],
            "start",
            "--package-file",
            str(Path(execution["handoff_path"]).parent / "task-package.json"),
            "--handoff",
            execution["handoff_path"],
            session_directory=envelope.get("runtime_layout") == "harness-session-directory-v1",
        )
        from harness.execution_bootstrap import execution_environment

        result = run_owned(
            argv,
            cwd=envelope["repository"],
            env=execution_environment(),
            timeout=60,
        )
        if result.returncode or len(result.stdout) > 262144:
            from harness.execution_logs import redact

            raise ExecutionConflict(
                "guarded AL initialization refused; exact run requires reconciliation: "
                + redact((result.stderr + result.stdout).decode(errors="replace")[-4096:])
            )
        identity = initialization_identity(execution, envelope)
    identity = validate_existing_origin(execution, envelope, package["launcher"])
    conn.execute(
        "UPDATE execution_initializations SET state='Active',identity=?,updated_at=? "
        "WHERE execution_id=? AND epoch_id=?",
        (canonical(identity), utcnow(), eid, execution["epoch_id"]),
    )
    return identity


def validate_existing_origin(execution, envelope, launcher):
    """Read-only managed validation, also used before bridge maintenance."""
    # Protected managed origin validates state independently; never trust a self-described
    # runtime_origin path from native-writable AL state as the authority root.
    from harness.execution_bootstrap import execution_environment

    verified = run_owned(
        al_argv(
            launcher,
            execution["host"],
            envelope["repository"],
            execution["al_session_id"],
            "show",
            "--line",
            existing_run=True,
        ),
        cwd=envelope["repository"],
        env=execution_environment(),
        timeout=30,
    )
    if verified.returncode:
        raise ExecutionConflict("protected existing AL origin validation refused")
    return initialization_identity(execution, envelope)
