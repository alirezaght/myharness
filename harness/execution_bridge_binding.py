"""Immutable first native identity assignment for a pending startup bridge."""

import sqlite3
from pathlib import Path

from harness.execution_contract import canonical, digest, loads
from harness.execution_handoff import atomic_file
from harness.execution_models import ExecutionConflict

FIXED = (
    "id",
    "binding",
    "handoff_hash",
    "al_run_id",
    "al_session_id",
    "epoch_id",
    "authority_generation",
)


def validate_settlement_ref(directory, upgrade):
    from harness.execution_bridge import _read

    ref = upgrade.get("settlement", {})
    path = Path(ref.get("stored_path", "/missing"))
    if path.parent != directory / "settlements" or digest(_read(path)).removeprefix(
        "sha256:"
    ) != ref.get("sha256"):
        raise ExecutionConflict("pending bridge settlement evidence differs")
    value = loads(_read(path))
    if (
        any(value.get(key) != upgrade[key] for key in ("epoch_id", "authority_generation"))
        or value.get("execution_id") != upgrade["id"]
    ):
        raise ExecutionConflict("pending bridge settlement identity differs")


def binding_path(directory, parent, native_id):
    return (
        directory
        / "bridge-upgrades"
        / "first-bindings"
        / (digest({"parent": parent, "native_id": native_id}).removeprefix("sha256:") + ".json")
    )


def validate_first_binding(directory, upgrade, parent, execution, *, conn=None):
    from harness.execution_bridge import _read

    value = loads(_read(binding_path(directory, parent, execution["native_id"]), 65536))
    fixed = {key: execution[key] for key in FIXED}
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "kind",
            "parent",
            *FIXED,
            "attempt_id",
            "worker_token",
            "native_id",
            "meaning",
        }
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("kind") != "first-native-binding-v1"
        or value.get("parent") != parent
        or value.get("native_id") != execution["native_id"]
        or value.get("meaning") != "identity-assigned-not-spawn-proof"
        or any(value.get(key) != fixed[key] for key in FIXED)
    ):
        raise ExecutionConflict("first native binding receipt differs")
    owned = conn is None
    if owned:
        raw = _read(Path(execution["handoff_path"]))
        if digest(raw) != execution["handoff_hash"]:
            raise ExecutionConflict("first binding handoff differs")
        conn = sqlite3.connect(Path(loads(raw)["database"]).as_uri() + "?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id,token,epoch_id,native_id,mode FROM execution_attempts "
            "WHERE execution_id=? AND id=?",
            (execution["id"], value["attempt_id"]),
        ).fetchone()
        if row is None or tuple(row) != (
            value["attempt_id"],
            value["worker_token"],
            execution["epoch_id"],
            execution["native_id"],
            "retry-start",
        ):
            raise ExecutionConflict("first native binding lacks its admitted epoch attempt")
    finally:
        if owned:
            conn.close()
    return value


def record_first_binding(conn, execution, native_id, token):
    from harness.execution_bridge import _read, selected_bridge

    directory = Path(execution["handoff_path"]).parent
    pointer = directory / "bridge-selection.json"
    if not pointer.exists():
        return
    selected_bridge(directory, execution, conn=conn)
    selection = loads(_read(pointer))
    upgrade = loads(_read(directory / selection["receipt"]))
    if upgrade["schema_version"] != 2 or execution["native_id"] is not None:
        return
    if not isinstance(native_id, str) or not native_id or len(native_id) > 256:
        raise ExecutionConflict("invalid first native identity")
    attempt = conn.execute(
        "SELECT id,epoch_id,mode,status,native_id FROM execution_attempts "
        "WHERE execution_id=? AND token=?",
        (execution["id"], token),
    ).fetchone()
    if (
        attempt is None
        or attempt["epoch_id"] != execution["epoch_id"]
        or attempt["mode"] != "retry-start"
        or attempt["status"] not in {"Launching", "Running"}
        or attempt["native_id"] not in (None, native_id)
    ):
        raise ExecutionConflict("first native identity requires current admitted epoch attempt")
    value = {
        "schema_version": 1,
        "kind": "first-native-binding-v1",
        "parent": selection["sha256"],
        **{key: execution[key] for key in FIXED},
        "attempt_id": attempt["id"],
        "worker_token": token,
        "native_id": native_id,
        "meaning": "identity-assigned-not-spawn-proof",
    }
    atomic_file(binding_path(directory, selection["sha256"], native_id), canonical(value).encode())
