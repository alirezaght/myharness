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


def protected_initialization(execution, envelope, binding):
    """Describe an already initialized protected run, without starting or trusting AL state."""
    from harness.execution_runtime_fences import _reference
    from harness.execution_runtime_layout import durable_runtime, selected_runtime_binding
    from harness.template_classification import _ordinary

    transaction = binding["transaction"]
    if transaction.get("status") != "ACTIVE" or transaction.get("schema_version") != 3:
        raise ExecutionConflict("successor initialization requires an active protected binding")
    selected_runtime_binding(execution, dict(envelope, runtime_binding=binding))
    directory = durable_runtime(
        binding,
        envelope["repository"],
        session_id=execution["al_session_id"],
        run_id=execution["al_run_id"],
    )
    storage = Path(binding["storage"])
    subject = {k: transaction[k] for k in ("repository_identity", "platform", "session_id")}
    path = storage / "runs" / (digest(subject).split(":")[1] + ".json")
    raw, mode = _ordinary(path)
    if mode != 0o600 or json.loads(raw) != transaction:
        raise ExecutionConflict("successor initialization protected transaction changed")
    reference = transaction["initialization"]
    initial_path = storage / "initializations" / reference["sha256"]
    if (
        reference["stored_path"] != str(initial_path)
        or reference["id"] != reference["sha256"]
        or reference["kind"] != "proof-admission-initialization-v3"
    ):
        raise ExecutionConflict("successor initialization reference escaped protected storage")
    initial = _reference(reference)
    expected = {
        k: transaction[k]
        for k in (
            "schema_version",
            "repository_identity",
            "session_id",
            "run_id",
            "runtime_layout",
            "runtime_directory",
            "harness_binding",
        )
    }
    expected.update(kind="proof-admission-initialization-v3", runtime_pin=transaction["pin"])
    if any(initial.get(k) != v for k, v in expected.items()):
        raise ExecutionConflict("successor initialization proof differs from selected binding")
    pin = transaction["pin"]
    pin_root = storage / "pins" / pin["pin_identity"]
    manifest = _reference(
        {"stored_path": str(pin_root / "manifest.json"), "sha256": pin["pin_manifest_digest"]}
    )
    if (
        manifest.get("pin_identity") != pin["pin_identity"]
        or manifest.get("platform") != execution["host"]
    ):
        raise ExecutionConflict("successor initialization pin identity differs")
    lock = path.with_suffix(".lock")
    if _ordinary(lock)[1] != 0o600:
        raise ExecutionConflict("successor initialization coordination lock changed")
    return {
        "binding_path": str(path),
        "binding_digest": digest(raw),
        "initialization_path": str(initial_path),
        "pin_root": str(pin_root),
        "coordination_lock": str(lock),
        "al_run_id": transaction["run_id"],
        "source": str(directory / (transaction["session_id"] + ".json")),
    }


def enroll_initialization(conn, execution, identity):
    """Caller owns the epoch-selection transaction; enrollment is not execution authority."""
    if not conn.in_transaction:
        raise ExecutionConflict(
            "successor initialization enrollment requires selection transaction"
        )
    row = conn.execute(
        "SELECT state,identity FROM execution_initializations WHERE execution_id=? AND epoch_id=?",
        (execution["id"], execution["epoch_id"]),
    ).fetchone()
    if row and (
        row["state"] not in {"Intent", "Active"}
        or row["identity"] is not None
        and json.loads(row["identity"]) != identity
    ):
        raise ExecutionConflict("conflicting successor initialization enrollment")
    if row and row["state"] == "Active" and row["identity"] is not None:
        return
    conn.execute(
        "INSERT INTO execution_initializations(execution_id,epoch_id,state,identity,updated_at) "
        "VALUES (?,?,'Active',?,?) ON CONFLICT(execution_id,epoch_id) DO UPDATE SET "
        "state='Active',identity=excluded.identity,updated_at=excluded.updated_at",
        (execution["id"], execution["epoch_id"], canonical(identity), utcnow()),
    )


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
    try:
        source = al_source(execution, envelope)
    except FileNotFoundError:
        if envelope.get("runtime_layout") == "harness-durable-epoch-v1" and (
            "runtime_binding" in envelope
            or (Path(execution["handoff_path"]).parent / "runtime-binding.json").exists()
        ):
            raise ExecutionConflict(
                "selected durable runtime is unavailable; reconcile its protected origin"
            ) from None
        source = None
    identity = None
    if (
        envelope.get("runtime_layout") == "harness-durable-epoch-v1"
        and "runtime_binding" not in envelope
        and source is None
    ):
        from harness.execution_runtime_recovery import _launcher_call

        try:
            origin = _launcher_call(
                package["launcher"],
                envelope["repository"],
                "origin",
                "--repository",
                envelope["repository"],
                "--platform",
                execution["host"],
                "--session",
                execution["al_session_id"],
                "--allow-absent",
            )
        except ExecutionConflict as error:
            raise ExecutionConflict(
                "Runtime origin observation refused; ensure the AL coordinator supports "
                "--allow-absent (upgrade AL if needed), then reconcile its protected origin. "
                "No initialization was started."
            ) from error
        absent = {
            "schema_version": 1,
            "kind": "runtime-origin-absence-v1",
            "state": "ABSENT",
            "repository": envelope["repository"],
            "platform": execution["host"],
            "session_id": execution["al_session_id"],
        }
        if isinstance(origin, dict) and origin.get("state") == "ABSENT":
            if origin != absent or type(origin.get("schema_version")) is not int:
                raise ExecutionConflict(
                    "runtime origin absence observation differs from exact subject"
                )
        elif not isinstance(origin, dict) or set(origin) != {"storage", "transaction", "source"}:
            raise ExecutionConflict("unsupported runtime origin observation")
        else:
            identity = _publish_initial_binding(execution, envelope, package["launcher"], origin)
    if identity is None and source is not None and source.exists():
        # Never initialize over an existing source, including a corrupt source.
        identity = initialization_identity(execution, envelope)
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
            session_directory=envelope.get("runtime_layout")
            in {"harness-session-directory-v1", "harness-durable-epoch-v1"},
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
        if (
            envelope.get("runtime_layout") == "harness-durable-epoch-v1"
            and "runtime_binding" not in envelope
        ):
            from harness.execution_runtime_recovery import _launcher_call

            origin = _launcher_call(
                package["launcher"],
                envelope["repository"],
                "origin",
                "--repository",
                envelope["repository"],
                "--platform",
                execution["host"],
                "--session",
                execution["al_session_id"],
            )
            _publish_initial_binding(execution, envelope, package["launcher"], origin)
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


def _publish_initial_binding(execution, envelope, launcher, origin):
    from harness.execution_handoff import atomic_file
    from harness.execution_runtime_layout import durable_runtime, selected_runtime_binding

    binding = {key: origin[key] for key in ("storage", "transaction")}
    if binding["transaction"].get("status") != "ACTIVE":
        raise ExecutionConflict("runtime binding requires an initialized protected origin")
    observed = dict(envelope, runtime_binding=binding)
    selected_runtime_binding(execution, observed)
    directory = durable_runtime(
        binding,
        envelope["repository"],
        session_id=execution["al_session_id"],
        run_id=execution["al_run_id"],
    )
    if origin["source"] != str(directory / (execution["al_session_id"] + ".json")):
        raise ExecutionConflict("initialized runtime source differs from protected origin")
    identity = validate_existing_origin(execution, observed, launcher)
    atomic_file(
        Path(execution["handoff_path"]).parent / "runtime-binding.json", canonical(binding).encode()
    )
    return identity
