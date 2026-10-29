"""Recoverable developer-decision intent, Registry materialization and delivery."""

import fcntl
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

from harness.db import default_db_path, transaction, utcnow
from harness.decisions import parse_decision, render_record, slug
from harness.execution_authority import require_execution_authority
from harness.execution_contract import (
    bounded,
    canonical,
    digest,
    identity,
    integer,
    string_list,
    text,
    validate_delivery,
)
from harness.execution_handoff import atomic_file, durable_file
from harness.execution_models import ExecutionConflict
from harness.execution_store import entity, get_execution
from harness.registry import DecisionRegistry


def _intent(conn, delivery_id):
    row = conn.execute("SELECT * FROM execution_decisions WHERE id=?", (delivery_id,)).fetchone()
    if row is None:
        raise LookupError("execution decision not found")
    intent = dict(row)
    current = get_execution(conn, intent["execution_id"])
    if intent["epoch_id"] != current["epoch_id"]:
        raise ExecutionConflict("stale_epoch: decision belongs to a historical runtime")
    return intent


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


def validate_decision_request(request):
    """The complete request contract, before any durable intent or Registry allocation."""
    bounded(request)
    if not isinstance(request, dict) or set(request) != {
        "blocker_id",
        "blocker_revision",
        "verdict",
        "text",
        "tasks",
        "criteria",
        "amendment",
    }:
        raise ValueError("invalid persisted decision request")
    identity(request["blocker_id"])
    integer(request["blocker_revision"], "blocker revision", 1)
    text(request["text"], "developer decision")
    if (
        request["verdict"] not in {"accept", "reject", "modify", "alternative"}
        or type(request["amendment"]) is not bool
    ):
        raise ValueError("invalid developer verdict/amendment")
    if request["verdict"] == "reject" and request["amendment"]:
        raise ValueError("rejection cannot authorize an amendment")
    string_list(request["tasks"], "decision tasks", ids=True, nonempty=True)
    string_list(request["criteria"], "decision criteria")
    if request["verdict"] != "reject" and len(request["text"].strip()) < 20:
        raise ValueError("resolution requires substantive explicit developer text")
    return request


def _request(intent):
    request = json.loads(intent["request"])
    validate_decision_request(request)
    if (
        request["blocker_id"] != intent["blocker_id"]
        or request["blocker_revision"] != intent["blocker_revision"]
    ):
        raise ExecutionConflict("persisted decision binding mismatch")
    return request


@contextmanager
def _decision_lock(controller, delivery_id):
    identity(delivery_id)
    intent = _intent(controller.conn, delivery_id)
    execution = get_execution(controller.conn, intent["execution_id"])
    directory = Path(execution["handoff_path"]).parent / "decision-locks"
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ExecutionConflict("decision lock alias refused")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        directory / (delivery_id + ".lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "a") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ExecutionConflict("decision lock identity differs")
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _epoch_decision_action(function):
    from functools import wraps

    @wraps(function)
    def locked(controller, delivery_id, *args, **kwargs):
        from harness.execution_epoch_lock import epoch_action_lock
        from harness.execution_epochs import require_active_epoch

        intent = _intent(controller.conn, delivery_id)
        execution = get_execution(controller.conn, intent["execution_id"])
        with epoch_action_lock(execution):
            require_active_epoch(
                controller.conn,
                execution["id"],
                expected_epoch_id=execution["epoch_id"],
                expected_authority_generation=execution["authority_generation"],
            )
            return function(controller, delivery_id, *args, **kwargs)

    return locked


def _epoch_execution_action(function):
    from functools import wraps

    @wraps(function)
    def locked(controller, execution_id, *args, **kwargs):
        from harness.execution_epoch_lock import epoch_action_lock
        from harness.execution_epochs import require_active_epoch

        execution = get_execution(controller.conn, execution_id)
        with epoch_action_lock(execution):
            require_active_epoch(
                controller.conn,
                execution_id,
                expected_epoch_id=execution["epoch_id"],
                expected_authority_generation=execution["authority_generation"],
            )
            return function(controller, execution_id, *args, **kwargs)

    return locked


@_epoch_execution_action
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
    expected_epoch_id=None,
    expected_authority_generation=None,
):
    identity(request_id)
    request = {
        "blocker_id": blocker_id,
        "blocker_revision": blocker_revision,
        "verdict": verdict,
        "text": decision_text,
        "tasks": tasks,
        "criteria": criteria,
        "amendment": amendment,
    }
    validate_decision_request(request)
    request = request | {"tasks": sorted(tasks), "criteria": sorted(criteria)}
    conn = controller.conn
    with transaction(conn):
        from harness.execution_epochs import require_active_epoch

        current = require_active_epoch(
            conn,
            execution_id,
            expected_epoch_id=expected_epoch_id,
            expected_authority_generation=expected_authority_generation,
            mutation=True,
        )
        require_execution_authority(controller, execution_id)
        previous = conn.execute(
            (
                "SELECT * FROM execution_decisions WHERE execution_id=? AND epoch_id=? AND "
                "request_id=?"
            ),
            (execution_id, current["epoch_id"], request_id),
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
                "WHERE execution_id=? AND epoch_id=? AND blocker_id=? AND blocker_revision=? "
                "AND state NOT IN ('Rejected')",
                (execution_id, current["epoch_id"], blocker_id, blocker_revision),
            ).fetchone():
                raise ExecutionConflict(
                    "blocker already has a pending decision; retry its request ID"
                )
            delivery_id = "decision-" + secrets.token_hex(12)
            now = utcnow()
            conn.execute(
                "INSERT INTO execution_decisions "
                "(id,execution_id,blocker_id,blocker_revision,request_id,request,state,"
                "created_at,updated_at,epoch_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                    current["epoch_id"],
                ),
            )
            controller.workspace(get_execution(conn, execution_id)["task_id"]).record_event(
                "execution.decision.intent",
                {"execution_id": execution_id, "delivery_id": delivery_id},
            )
    return recover_decision_intent(controller, delivery_id)


@_epoch_decision_action
def materialize_decision(controller, delivery_id):
    conn = controller.conn
    intent = _intent(conn, delivery_id)
    execution = get_execution(conn, intent["execution_id"])
    envelope = require_execution_authority(controller, execution["id"])
    directory = Path(envelope["repository"]) / "docs" / "decisions"
    _request(intent)
    if intent["state"] == "Rejected":
        raise ExecutionConflict("rejected or retired decision cannot be recovered")
    registry = DecisionRegistry(directory)
    with _registry_lock(directory, execution):
        with transaction(conn):
            intent = _intent(conn, delivery_id)
            if intent["state"] not in {"Intent", "Ready", "Delivered"}:
                return intent
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


@_epoch_decision_action
def deliver_decision(controller, delivery_id):
    conn = controller.conn
    intent = _intent(conn, delivery_id)
    execution = get_execution(conn, intent["execution_id"])
    require_execution_authority(controller, execution["id"])
    if intent["state"] not in {"Ready", "Delivered"}:
        return intent
    if not execution["al_run_id"]:
        raise ExecutionConflict("AL run identity is not bound yet")
    request = _request(intent)
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


@_epoch_decision_action
def recover_decision_intent(controller, delivery_id):
    with _decision_lock(controller, delivery_id):
        intent = _intent(controller.conn, delivery_id)
        if intent["state"] == "Rejected":
            raise ExecutionConflict("rejected or retired decision cannot be recovered")
        _request(intent)
        try:
            materialize_decision(controller, delivery_id)
            return deliver_decision(controller, delivery_id)
        except (OSError, ValueError) as error:
            controller.conn.execute(
                "UPDATE execution_decisions SET error=?,updated_at=? WHERE id=? "
                "AND state IN ('Intent','Ready','Delivered')",
                (str(error), utcnow(), delivery_id),
            )
            raise


def deliver_pending(controller, execution_id):
    rows = controller.conn.execute(
        "SELECT id FROM execution_decisions "
        "WHERE execution_id=? AND epoch_id=(SELECT epoch_id FROM executions WHERE id=?) "
        "AND state IN ('Intent','Ready') AND error IS NULL",
        (execution_id, execution_id),
    ).fetchall()
    for row in rows:
        try:
            recover_decision_intent(controller, row["id"])
        except (OSError, ValueError):
            # Persistent retry diagnostics belong to the intent. Never acknowledge here.
            pass


@_epoch_execution_action
def retry_execution_decision(
    controller,
    execution_id,
    delivery_id,
    *,
    expected_epoch_id=None,
    expected_authority_generation=None,
):
    """Retry only a persisted request; presentation text is never an authority input."""
    from harness.execution_epochs import require_active_epoch

    current = require_active_epoch(
        controller.conn,
        execution_id,
        expected_epoch_id=expected_epoch_id,
        expected_authority_generation=expected_authority_generation,
        mutation=True,
    )
    identity(delivery_id)
    intent = _intent(controller.conn, delivery_id)
    if intent["epoch_id"] != current["epoch_id"]:
        raise ExecutionConflict(
            "stale_epoch: historical decision cannot be delivered into current runtime"
        )
    if intent["execution_id"] != execution_id:
        raise LookupError("execution decision not found for execution")
    require_execution_authority(controller, execution_id, operation="read")
    identity(intent["request_id"])
    _request(intent)
    if intent["state"] == "Rejected":
        raise ExecutionConflict("rejected or retired decision cannot be recovered")
    if intent["state"] in {"Resolved", "RevalidationRequired"}:
        return intent
    if intent["state"] not in {"Intent", "Ready", "Delivered"}:
        raise ExecutionConflict("decision state cannot be retried")
    return recover_decision_intent(controller, delivery_id)


def _safe_bytes(path, maximum=32 * 1024 * 1024):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ExecutionConflict("retirement evidence alias refused")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise ExecutionConflict("retirement evidence must be bounded and ordinary")
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (
        len(raw) > maximum
        or path.stat().st_ino != before.st_ino
        or any(
            getattr(before, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")
        )
    ):
        raise ExecutionConflict("retirement evidence changed during observation")
    return raw


def _mentions(value, delivery_id):
    if isinstance(value, dict):
        return any(_mentions(k, delivery_id) or _mentions(v, delivery_id) for k, v in value.items())
    if isinstance(value, list):
        return any(_mentions(v, delivery_id) for v in value)
    return isinstance(value, str) and delivery_id in value


def _registry_provenance(intent, envelope):
    fields = [
        intent[k] for k in ("registry_id", "registry_path", "registry_bytes", "registry_hash")
    ]
    if all(value is None for value in fields):
        return None
    if any(not isinstance(value, str) or not value for value in fields):
        raise ExecutionConflict("incomplete frozen Registry provenance")
    path = Path(intent["registry_path"])
    if path.parent != Path(envelope["repository"]) / "docs/decisions":
        raise ExecutionConflict("foreign frozen Registry path")
    raw = _safe_bytes(path)
    if raw != intent["registry_bytes"].encode() or digest(raw) != intent["registry_hash"]:
        raise ExecutionConflict("frozen Registry bytes changed")
    parsed = parse_decision(path)
    if parsed.id != intent["registry_id"] or parsed.version != 1:
        raise ExecutionConflict("frozen Registry identity changed")
    return {"id": intent["registry_id"], "path": str(path), "sha256": intent["registry_hash"]}


def _retirement_binding(execution, initial):
    return {
        k: execution[k]
        for k in ("id", "task_id", "binding", "handoff_hash", "al_run_id", "al_session_id")
    } | {"pin_identity": Path(initial["pin_root"]).name}


def _existing_retirement(
    controller, execution, delivery_id, expected, reason, binding, registry, prior_error
):
    found = []
    for event in controller.workspace(execution["task_id"]).events():
        if (
            event.kind == "execution.decision.invalid_retired"
            and event.payload.get("delivery_id") == delivery_id
        ):
            found.append(event.payload)
    if len(found) != 1:
        raise ExecutionConflict("rejected decision lacks one committed invalid-request retirement")
    event = found[0]
    reference = event["receipt"]
    path = Path(reference["path"])
    if path.parent != Path(execution["handoff_path"]).parent / "decision-retirements":
        raise ExecutionConflict("foreign retirement receipt")
    raw = _safe_bytes(path)
    receipt = json.loads(raw)
    if (
        digest(raw) != reference["sha256"]
        or receipt["kind"] != "invalid-decision-retirement-v1"
        or receipt["binding"] != binding
        or receipt["delivery_id"] != delivery_id
        or receipt["request_sha256"] != expected
        or receipt["reason"] != reason
        or event["execution_id"] != execution["id"]
        or event.get("request_sha256") != expected
        or receipt.get("registry") != registry
        or receipt.get("prior_error") != prior_error
    ):
        raise ExecutionConflict("retirement replay identity or digest differs")
    return _retirement_result(delivery_id, expected, reference)


def _retirement_result(delivery_id, expected, reference):
    return {
        "status": "Retired",
        "kind": "invalid-request-retirement",
        "state": "Rejected",
        "delivery_id": delivery_id,
        "request_sha256": expected,
        "receipt": reference,
    }


def _absence(controller, execution, intent, state):
    from harness.execution_contract import validate_event
    from harness.execution_handoff import same_path

    integration = state.get("integration")
    expected = execution["binding"] | {
        "handoff_hash": execution["handoff_hash"],
        "al_session_id": execution["al_session_id"],
        "al_run_id": execution["al_run_id"],
    }
    if (
        not isinstance(integration, dict)
        or integration.get("binding") != expected
        or state.get("run_id") != execution["al_run_id"]
        or state.get("session_id") != execution["al_session_id"]
        or not same_path(integration.get("handoff"), execution["handoff_path"])
    ):
        raise ExecutionConflict("retirement canonical AL source binding differs")
    if (
        not isinstance(integration.get("deliveries"), dict)
        or not isinstance(integration.get("outbox"), list)
        or type(integration.get("sequence")) is not int
        or integration["sequence"] != len(integration["outbox"])
    ):
        raise ExecutionConflict("retirement source acknowledgment history is ambiguous")
    for event in integration["outbox"]:
        validate_event(event)
    if _mentions(state, intent["id"]):
        raise ExecutionConflict("canonical AL source contains this decision identity")
    request = json.loads(intent["request"])
    blocker = entity(controller.conn, execution["id"], "blocker", intent["blocker_id"])
    source_blocker = integration.get("blockers", {}).get(intent["blocker_id"])
    for current in (blocker, source_blocker):
        if (
            not isinstance(current, dict)
            or current.get("state") != "Open"
            or current.get("revision") != intent["blocker_revision"]
            or set(current.get("affected", current.get("tasks", []))) != set(request["tasks"])
        ):
            raise ExecutionConflict("retirement blocker revision or affected scope differs")
    if (
        request.get("blocker_id") != intent["blocker_id"]
        or request.get("blocker_revision") != intent["blocker_revision"]
    ):
        raise ExecutionConflict("retirement request blocker binding differs")
    inbox = Path(execution["handoff_path"]).parent / "inbox" / (intent["id"] + ".json")
    if os.path.lexists(inbox):
        raise ExecutionConflict("decision inbox entry already exists")
    for row in controller.conn.execute(
        "SELECT record FROM execution_events WHERE execution_id=?", (execution["id"],)
    ):
        event = json.loads(row["record"])
        validate_event(event)
        if _mentions(event, intent["id"]):
            raise ExecutionConflict("persisted AL event contains this decision identity")
    for event in controller.workspace(execution["task_id"]).events():
        if not _mentions(event.payload, intent["id"]):
            continue
        if event.kind == "execution.decision.intent" and event.payload == {
            "execution_id": execution["id"],
            "delivery_id": intent["id"],
        }:
            continue
        raise ExecutionConflict("persisted delivery evidence contains this decision identity")


def _retire_invalid_decision(
    controller, execution_id, delivery_id, *, expected_request_digest, reason
):
    """Operator-only administrative retirement; never a business verdict or AL acknowledgment."""
    from harness.execution_hosts import managed_launcher
    from harness.execution_initialization import validate_existing_origin
    from harness.execution_worker import worker_lock

    identity(delivery_id)
    text(reason, "retirement operator reason")
    if (
        len(reason.strip()) < 20
        or not isinstance(expected_request_digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_request_digest)
    ):
        raise ValueError("retirement requires exact request digest and substantive operator reason")
    execution = get_execution(controller.conn, execution_id)
    with worker_lock(execution), _decision_lock(controller, delivery_id):
        envelope = require_execution_authority(controller, execution_id, operation="read")
        intent = _intent(controller.conn, delivery_id)
        if (
            intent["execution_id"] != execution_id
            or digest(intent["request"].encode()) != expected_request_digest
        ):
            raise ExecutionConflict("retirement execution or frozen request digest differs")
        initial = validate_existing_origin(execution, envelope, managed_launcher())
        binding = _retirement_binding(execution, initial)
        registry = _registry_provenance(intent, envelope)
        if intent["state"] == "Rejected":
            return _existing_retirement(
                controller,
                execution,
                delivery_id,
                expected_request_digest,
                reason,
                binding,
                registry,
                intent["error"],
            )
        if intent["state"] not in {"Intent", "Ready"} or intent["delivery"] is not None:
            raise ExecutionConflict("only undelivered invalid Intent/Ready requests can retire")
        try:
            validate_decision_request(json.loads(intent["request"]))
        except ValueError:
            pass
        else:
            raise ExecutionConflict("valid developer decisions cannot be retired")
        with Path(initial["coordination_lock"]).open("rb") as source_lock:
            try:
                fcntl.flock(source_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ExecutionConflict("canonical AL source has a competing operation") from None
            try:
                source_raw = _safe_bytes(initial["source"])
                state = json.loads(source_raw)
                _absence(controller, execution, intent, state)
                receipt = {
                    "kind": "invalid-decision-retirement-v1",
                    "binding": binding,
                    "delivery_id": delivery_id,
                    "request_sha256": expected_request_digest,
                    "prior_state": intent["state"],
                    "prior_error": intent["error"],
                    "registry": registry,
                    "source_sha256": digest(source_raw),
                    "absence": {
                        "inbox": True,
                        "canonical_delivery_or_ack": True,
                        "persisted_delivery_or_ack": True,
                    },
                    "reason": reason,
                    "at": utcnow(),
                }
                raw = canonical(receipt).encode()
                path = (
                    Path(execution["handoff_path"]).parent
                    / "decision-retirements"
                    / (digest(raw).split(":")[1] + ".json")
                )
                durable_file(path, raw)
                reference = {"path": str(path), "sha256": digest(raw)}
                with transaction(controller.conn):
                    current = _intent(controller.conn, delivery_id)
                    if current != intent or _safe_bytes(initial["source"]) != source_raw:
                        raise ExecutionConflict("retirement evidence changed before commit")
                    require_execution_authority(controller, execution_id, operation="read")
                    _absence(controller, execution, current, state)
                    _registry_provenance(current, envelope)
                    controller.conn.execute(
                        "UPDATE execution_decisions SET state='Rejected',updated_at=? WHERE id=?",
                        (utcnow(), delivery_id),
                    )
                    controller.workspace(execution["task_id"]).record_event(
                        "execution.decision.invalid_retired",
                        {
                            "execution_id": execution_id,
                            "delivery_id": delivery_id,
                            "request_sha256": expected_request_digest,
                            "receipt": reference,
                        },
                    )
                return _retirement_result(delivery_id, expected_request_digest, reference)
            finally:
                fcntl.flock(source_lock, fcntl.LOCK_UN)


def retire_invalid_decision(
    controller, execution_id, delivery_id, *, expected_request_digest, reason
):
    try:
        return _retire_invalid_decision(
            controller,
            execution_id,
            delivery_id,
            expected_request_digest=expected_request_digest,
            reason=reason,
        )
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        raise ExecutionConflict("retirement evidence is unreadable or ambiguous") from None
