"""Recoverable developer-decision intent, Registry materialization and delivery."""

import fcntl
import json
import secrets
from contextlib import contextmanager
from pathlib import Path

from harness.db import default_db_path, transaction, utcnow
from harness.decisions import parse_decision, render_record, slug
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest, identity, integer, text, validate_delivery
from harness.execution_handoff import atomic_file, durable_file
from harness.execution_models import ExecutionConflict
from harness.execution_store import entity, get_execution
from harness.registry import DecisionRegistry


def _intent(conn, delivery_id):
    row = conn.execute("SELECT * FROM execution_decisions WHERE id=?", (delivery_id,)).fetchone()
    if row is None:
        raise LookupError("execution decision not found")
    return dict(row)


@contextmanager
def _registry_lock(directory, execution):
    # All executions targeting this Registry share the same private lock.
    key = digest(str(directory.resolve()).encode()).split(":")[1]
    lockdir = default_db_path(Path(directory).resolve()).parent.parent / "registry-locks"
    lockdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lockdir / (key + ".lock")).open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def record_execution_decision(
    controller,
    execution_id,
    blocker_id,
    *,
    blocker_revision,
    request_id,
    verdict,
    decision_text,
    tasks,
    criteria,
    amendment=False,
):
    identity(request_id)
    identity(blocker_id)
    integer(blocker_revision, "blocker revision", 1)
    text(decision_text, "developer decision")
    if verdict not in {"accept", "reject", "modify", "alternative"} or type(amendment) is not bool:
        raise ValueError("invalid developer verdict/amendment")
    if (
        not isinstance(tasks, list)
        or not tasks
        or not isinstance(criteria, list)
        or len(tasks) != len(set(tasks))
        or len(criteria) != len(set(criteria))
    ):
        raise ValueError("invalid explicit decision scope")
    for task in tasks:
        identity(task)
    if verdict != "reject" and len(decision_text.strip()) < 20:
        raise ValueError("resolution requires substantive explicit developer text")
    conn = controller.conn
    request = {
        "blocker_id": blocker_id,
        "blocker_revision": blocker_revision,
        "verdict": verdict,
        "text": decision_text,
        "tasks": sorted(tasks),
        "criteria": sorted(criteria),
        "amendment": amendment,
    }
    with transaction(conn):
        require_execution_authority(controller, execution_id)
        previous = conn.execute(
            "SELECT * FROM execution_decisions WHERE execution_id=? AND request_id=?",
            (execution_id, request_id),
        ).fetchone()
        if previous:
            if previous["request"] != canonical(request):
                raise ExecutionConflict("decision request ID reused with changed content")
            delivery_id = previous["id"]
        else:
            blocker = entity(conn, execution_id, "blocker", blocker_id)
            if not blocker or blocker["state"] != "Open" or blocker["revision"] != blocker_revision:
                raise ExecutionConflict("blocker no longer matches this decision")
            actual = set(blocker.get("affected", blocker["tasks"]))
            if set(tasks) != actual:
                raise ExecutionConflict(
                    "decision scope must cover affected task dependency closure"
                )
            if conn.execute(
                "SELECT 1 FROM execution_decisions "
                "WHERE execution_id=? AND blocker_id=? AND blocker_revision=? "
                "AND state NOT IN ('Rejected')",
                (execution_id, blocker_id, blocker_revision),
            ).fetchone():
                raise ExecutionConflict(
                    "blocker already has a pending decision; retry its request ID"
                )
            delivery_id = "decision-" + secrets.token_hex(12)
            now = utcnow()
            conn.execute(
                "INSERT INTO execution_decisions "
                "(id,execution_id,blocker_id,blocker_revision,request_id,request,state,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    delivery_id,
                    execution_id,
                    blocker_id,
                    blocker_revision,
                    request_id,
                    canonical(request),
                    "Intent",
                    now,
                    now,
                ),
            )
            controller.workspace(get_execution(conn, execution_id)["task_id"]).record_event(
                "execution.decision.intent",
                {"execution_id": execution_id, "delivery_id": delivery_id},
            )
    return recover_decision_intent(controller, delivery_id)


def materialize_decision(controller, delivery_id):
    conn = controller.conn
    intent = _intent(conn, delivery_id)
    execution = get_execution(conn, intent["execution_id"])
    envelope = require_execution_authority(controller, execution["id"])
    directory = Path(envelope["repository"]) / "docs" / "decisions"
    registry = DecisionRegistry(directory)
    with _registry_lock(directory, execution):
        with transaction(conn):
            intent = _intent(conn, delivery_id)
            if intent["registry_bytes"] is None:
                request = json.loads(intent["request"])
                next_num = int(registry.next_id().split("-")[1])
                for r in conn.execute(
                    "SELECT registry_id FROM execution_decisions WHERE registry_path LIKE ?",
                    (str(directory) + "/%",),
                ):
                    if r["registry_id"]:
                        next_num = max(next_num, int(r["registry_id"].split("-")[1]) + 1)
                record_id = f"DEC-{next_num:04d}"
                title = f"Execution {execution['id']} blocker {intent['blocker_id']}"
                content = (
                    "Developer rejected the recommendation. Explicit response: "
                    if request["verdict"] == "reject"
                    else "Explicit developer " + request["verdict"] + ": "
                ) + request["text"]
                provenance = (
                    f"Execution {execution['id']}; approval {execution['approval_id']}; "
                    f"blocker {intent['blocker_id']} revision {intent['blocker_revision']}; "
                    f"delivery {intent['id']}; request {intent['request_id']}."
                )
                raw = render_record(
                    record_id,
                    title,
                    1,
                    intent["created_at"],
                    ", ".join(request["tasks"]),
                    None,
                    content,
                    provenance,
                    "Authorized scope: "
                    + ", ".join(request["criteria"])
                    + (
                        "; amendment requires scoped Architect revalidation."
                        if request["amendment"]
                        else "; original approved specification remains immutable."
                    ),
                )
                path = directory / (record_id + "-" + slug(title) + ".md")
                conn.execute(
                    "UPDATE execution_decisions SET registry_id=?,registry_path=?,registry_bytes=?,"
                    "registry_hash=?,updated_at=? WHERE id=?",
                    (record_id, str(path), raw, digest(raw.encode()), utcnow(), delivery_id),
                )
                intent = _intent(conn, delivery_id)
        # A retry uses exactly the frozen ID/path/timestamp/bytes, including after fsync.
        durable_file(
            Path(intent["registry_path"]), intent["registry_bytes"].encode(), private=False
        )
        parsed = parse_decision(Path(intent["registry_path"]))
        if (
            parsed.id != intent["registry_id"]
            or parsed.version != 1
            or digest(Path(intent["registry_path"]).read_bytes()) != intent["registry_hash"]
        ):
            raise ExecutionConflict("materialized Registry record differs from frozen intent")
        with transaction(conn):
            conn.execute(
                "UPDATE execution_decisions SET state=CASE WHEN state='Intent' "
                "THEN 'Ready' ELSE state END,"
                "error=NULL,updated_at=? WHERE id=?",
                (utcnow(), delivery_id),
            )
    return _intent(conn, delivery_id)


def deliver_decision(controller, delivery_id):
    conn = controller.conn
    intent = _intent(conn, delivery_id)
    execution = get_execution(conn, intent["execution_id"])
    require_execution_authority(controller, execution["id"])
    if intent["state"] not in {"Ready", "Delivered"}:
        return intent
    if not execution["al_run_id"]:
        raise ExecutionConflict("AL run identity is not bound yet")
    request = json.loads(intent["request"])
    # Base revision comes from the bound canonical AL source, never host prose.
    from harness.execution_handoff import read_handoff
    from harness.execution_hosts import al_source

    source = al_source(execution, read_handoff(execution["handoff_path"]))
    state = json.loads(source.read_bytes())
    if (
        state.get("run_id") != execution["al_run_id"]
        or state.get("integration", {}).get("binding", {}).get("execution_id") != execution["id"]
    ):
        raise ExecutionConflict("decision source AL identity mismatch")
    import hashlib

    amendments = [item["artifact"]["sha256"] for item in state["amendments"]]
    base_revision = hashlib.sha256(
        json.dumps(
            {"original": state["task_package"]["sha256"], "amendments": amendments}, sort_keys=True
        ).encode()
    ).hexdigest()
    binding = execution["binding"] | {
        "handoff_hash": execution["handoff_hash"],
        "al_session_id": execution["al_session_id"],
        "al_run_id": execution["al_run_id"],
    }
    delivery = {
        "schema_version": 1,
        "id": delivery_id,
        "binding": binding,
        **{
            k: request[k]
            for k in (
                "blocker_id",
                "blocker_revision",
                "verdict",
                "text",
                "tasks",
                "criteria",
                "amendment",
            )
        },
        "registry": {
            "id": intent["registry_id"],
            "path": intent["registry_path"],
            "sha256": intent["registry_hash"],
        },
        "base_revision": base_revision,
    }
    validate_delivery(delivery)
    with transaction(conn):
        intent = _intent(conn, delivery_id)
        if intent["delivery"] is None:
            conn.execute(
                "UPDATE execution_decisions SET delivery=? WHERE id=?",
                (canonical(delivery), delivery_id),
            )
        else:
            delivery = json.loads(intent["delivery"])
    require_execution_authority(controller, execution["id"])
    path = Path(execution["handoff_path"]).parent / "inbox" / (delivery_id + ".json")
    atomic_file(path, canonical(delivery).encode())
    conn.execute(
        "UPDATE execution_decisions SET state='Delivered',error=NULL,updated_at=? "
        "WHERE id=? AND state IN ('Ready','Delivered')",
        (utcnow(), delivery_id),
    )
    return _intent(conn, delivery_id)


def recover_decision_intent(controller, delivery_id):
    try:
        materialize_decision(controller, delivery_id)
        return deliver_decision(controller, delivery_id)
    except (OSError, ValueError) as error:
        controller.conn.execute(
            "UPDATE execution_decisions SET error=?,updated_at=? WHERE id=?",
            (str(error), utcnow(), delivery_id),
        )
        raise


def deliver_pending(controller, execution_id):
    rows = controller.conn.execute(
        "SELECT id FROM execution_decisions "
        "WHERE execution_id=? AND state IN ('Intent','Ready') AND error IS NULL",
        (execution_id,),
    ).fetchall()
    for row in rows:
        try:
            recover_decision_intent(controller, row["id"])
        except (OSError, ValueError):
            # Persistent retry diagnostics belong to the intent. Never acknowledge here.
            pass
