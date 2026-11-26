"""Allowlisted projections of authoritative Harness records; never diagnostic payloads."""

import json

from harness.design_models import EFFECTIVE
from harness.execution_contract import validate_event
from harness.knowledge.models import (
    EXTRACTOR_VERSION,
    MAX_SOURCE_BYTES,
    Assertion,
    Extraction,
    SourceVersion,
    canonical,
    digest,
    identity,
)
from harness.knowledge.sources import _SECRET
from harness.requirement_registry import registry


def _source(scope, kind, key, payload, *, runtime=False):
    raw = canonical(payload).encode()
    if _SECRET.search(raw.decode()):
        raise ValueError("sensitive structured source excluded")
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("structured source exceeds supported bound")
    locator = f"harness:{kind}:{key}"
    sid = identity(scope.project_id, locator)
    visibility = "task:" + scope.task_id if scope.task_id else "project"
    parts = (sid, digest(raw), EXTRACTOR_VERSION, kind)
    version = identity(*parts, visibility) if visibility != "project" else identity(*parts)
    return SourceVersion(
        sid,
        version,
        locator,
        kind,
        digest(raw),
        raw,
        {"adapter": kind, "key": key, "visibility": visibility},
        "runtime-record" if runtime else "harness-record",
        visibility,
    )


def _payload(controller, kind, key):
    if kind == "requirements":
        task_id = key
        ws = controller.workspace(task_id)
        accepted = ws.latest_checkpoint("context_accepted")
        if not accepted:
            raise ValueError("accepted requirements unavailable")
        # registry is the authority for identity; source text remains in its accepted source.
        value = registry(controller, task_id, "")
        return {
            "task_id": task_id,
            "checkpoint_id": accepted.id,
            "criteria": value["criteria"],
            "invariants": value["invariants"],
            "ticket_id": value["ticket"].get("id"),
        }
    if kind == "design":
        task_id, entry_id = key.rsplit(":", 1)
        entry = controller.workspace(task_id).design_entry(int(entry_id))
        if entry.status not in EFFECTIVE:
            raise ValueError("design entry is not effective")
        return {
            "task_id": task_id,
            "entry_id": entry.ref,
            "section": entry.section.value,
            "binding": entry.binding.value,
            "status": entry.status.value,
            "text": entry.text,
            "rationale": entry.rationale,
        }
    if kind == "checkpoint":
        task_id, checkpoint_id = key.rsplit(":", 1)
        row = controller.conn.execute(
            "SELECT id,task_id,phase,label FROM checkpoints WHERE task_id=? AND id=?",
            (task_id, int(checkpoint_id)),
        ).fetchone()
        if not row:
            raise ValueError("checkpoint unavailable")
        # Payloads may contain stdout, arguments or arbitrary source text: never copy them.
        return dict(row)
    if kind == "task-event":
        task_id, event_id = key.rsplit(":", 1)
        row = controller.conn.execute(
            "SELECT id,task_id,kind,payload FROM events WHERE task_id=? AND id=?",
            (task_id, int(event_id)),
        ).fetchone()
        allowed = {
            "task.created": ("complexity", "source"),
            "design.stated": ("id", "section", "binding"),
        }
        if not row or row["kind"] not in allowed:
            raise ValueError("unsupported task event")
        payload = json.loads(row["payload"])
        return {
            "id": row["id"],
            "task_id": task_id,
            "kind": row["kind"],
            "fields": {k: payload[k] for k in allowed[row["kind"]] if k in payload},
        }
    if kind == "execution-event":
        execution_id, epoch_id, event_id = key.split(":", 2)
        row = controller.conn.execute(
            "SELECT e.*,x.task_id,x.binding,x.handoff_hash,x.al_session_id,x.al_run_id "
            "FROM execution_events e JOIN executions x ON x.id=e.execution_id "
            "WHERE e.execution_id=? AND e.epoch_id=? AND e.event_id=?",
            (execution_id, epoch_id, event_id),
        ).fetchone()
        if not row:
            raise ValueError("execution event unavailable")
        event = validate_event(json.loads(row["record"]))
        expected = json.loads(row["binding"]) | {
            "handoff_hash": row["handoff_hash"],
            "al_session_id": row["al_session_id"],
            "al_run_id": row["al_run_id"],
        }
        if (
            event["binding"] != expected
            or event["event_id"] != row["event_id"]
            or (event["sequence"] != row["sequence"] or event["kind"] != row["kind"])
        ):
            raise ValueError("execution event binding mismatch")
        fields = {
            k: event["payload"][k]
            for k in (
                "id",
                "status",
                "area",
                "scope",
                "validity",
                "phase",
                "role",
                "revision",
                "receipt_hash",
                "task_id",
            )
            if k in event["payload"]
        }
        return {
            "task_id": row["task_id"],
            "execution_id": execution_id,
            "epoch_id": epoch_id,
            "event_id": event_id,
            "kind": event["kind"],
            "sequence": event["sequence"],
            "fields": fields,
        }
    raise ValueError("unsupported structured source")


def task_sources(controller, scope, task_id, *, max_records=200):
    """Supervisor-selected task only; caller owns publication and broader discovery."""
    if not scope.task_id or scope.task_id != task_id or not 1 <= max_records <= 2000:
        raise PermissionError("structured indexing requires a matching task scope")
    selections = []
    ws = controller.workspace(task_id)
    if ws.latest_checkpoint("context_accepted"):
        selections.append(("requirements", task_id, False))
    selections.extend(
        ("design", f"{task_id}:{r['id']}", False)
        for r in controller.conn.execute(
            "SELECT id FROM design_entries WHERE task_id=? "
            "AND status IN ('stated','accepted','modified') "
            "ORDER BY id LIMIT ?",
            (task_id, max_records + 1),
        )
    )
    selections.extend(
        ("checkpoint", f"{task_id}:{r['id']}", False)
        for r in controller.conn.execute(
            "SELECT id FROM checkpoints WHERE task_id=? ORDER BY id LIMIT ?", (task_id, max_records)
        )
    )
    selections.extend(
        ("task-event", f"{task_id}:{r['id']}", True)
        for r in controller.conn.execute(
            "SELECT id FROM events WHERE task_id=? AND kind IN ('task.created','design.stated') "
            "ORDER BY id LIMIT ?",
            (task_id, max_records + 1),
        )
    )
    # Only the explicitly bound execution is eligible; no neighbor-project scan.
    if scope.execution_id:
        selections.extend(
            ("execution-event", f"{r['execution_id']}:{r['epoch_id']}:{r['event_id']}", True)
            for r in controller.conn.execute(
                "SELECT e.execution_id,e.epoch_id,e.event_id FROM execution_events e "
                "JOIN executions x ON x.id=e.execution_id WHERE e.execution_id=? AND x.task_id=? "
                "ORDER BY e.sequence LIMIT ?",
                (scope.execution_id, task_id, max_records),
            )
        )
    sources, coverage = [], []
    for kind, key, runtime in selections[:max_records]:
        try:
            sources.append(
                _source(scope, kind, key, _payload(controller, kind, key), runtime=runtime)
            )
        except (ValueError, KeyError, TypeError):
            coverage.append({"status": "structured-source-unavailable", "kind": kind})
    if len(selections) > max_records:
        coverage.append({"status": "structured-record-cap", "limit": max_records})
    return tuple(sources), tuple(dict(gap, visibility="task:" + task_id) for gap in coverage)


def current_bytes(conn, scope, kind, observation):
    """Reconstruct the same allowlisted record, never a synthetic filesystem locator."""
    from harness.controller import HarnessController

    return canonical(_payload(HarnessController(conn), kind, observation["key"])).encode()


def extract_record(source):
    value = json.loads(source.raw)
    task = f"task:{value['task_id']}"
    assertions = []

    def add(subject, predicate, obj=None, scalar=None):
        assertions.append(
            Assertion(
                subject,
                predicate,
                obj,
                scalar,
                canonical(scalar if scalar is not None else obj),
                0,
                len(source.raw),
            )
        )

    if source.kind == "requirements":
        for key in ("criteria", "invariants"):
            for ref in value[key]:
                add(task, "governed-by", f"requirement:{value['task_id']}:{ref}")
        if value["ticket_id"]:
            add(task, "references", f"ticket:{value['ticket_id']}")
    elif source.kind == "design":
        subject = f"design:{value['task_id']}:{value['entry_id']}"
        add(task, "designed-by", subject)
        for key in ("section", "binding", "status", "text", "rationale"):
            add(subject, key, scalar=value[key])
    elif source.kind == "checkpoint":
        subject = f"checkpoint:{value['task_id']}:{value['id']}"
        add(task, "observed-at", subject)
        add(subject, "phase", scalar=value["phase"])
        add(subject, "label", scalar=value["label"])
    elif source.kind == "task-event":
        subject = f"task-event:{value['task_id']}:{value['id']}"
        add(task, "observed-at", subject)
        add(subject, "event-kind", scalar=value["kind"])
        for key, scalar in value["fields"].items():
            add(subject, f"observed-{key}", scalar=scalar)
    elif source.kind == "execution-event":
        subject = f"execution-event:{value['execution_id']}:{value['epoch_id']}:{value['event_id']}"
        add(task, "observed-at", subject)
        add(subject, "event-kind", scalar=value["kind"])
        for key, scalar in value["fields"].items():
            add(subject, f"observed-{key}", scalar=scalar)
    else:
        raise ValueError("unsupported structured source")
    return Extraction(tuple(assertions))


BINDING_READS = 4
BINDING_BYTES = 4 * 1024 * 1024


def binding_record(conn, table, fields, where, args, reads, meter, *, supplied=None):
    """Bounded internal SQL projections; IDs never substitute for body validation."""
    size_sql = "+".join(f"coalesce(length(CAST({field} AS BLOB)),0)" for field in fields.values())
    size = conn.execute(f"SELECT {size_sql} FROM {table} WHERE {where}", args).fetchone()
    if size is None:
        return None, "absent"
    remaining = min(
        MAX_SOURCE_BYTES, BINDING_BYTES - meter["bytes"], reads["max_bytes"] - reads["bytes"]
    )
    if meter["count"] >= BINDING_READS or reads["count"] >= reads["max_count"]:
        return None, "binding-read-budget"
    meter["count"] += 1
    reads["count"] += 1
    # JSON escaping can expand each UTF-8 byte sixfold; leave room for keys/numbers.
    if size[0] * 6 + 1024 > remaining:
        return None, "binding-byte-budget"
    if supplied is None:
        columns = ",".join(f"{field} AS {name}" for name, field in fields.items())
        row = conn.execute(
            f"SELECT {columns} FROM {table} WHERE {where} AND ({size_sql})<=?",
            (*args, (remaining - 1024) // 6),
        ).fetchone()
        if row is None:
            return None, "binding-source-changed"
        value = dict(row)
    else:
        value = supplied
    raw = canonical(value).encode()
    # Also guard supplied observations and the exact serialized projection.
    if len(raw) > remaining:
        return None, "binding-byte-budget"
    reads["bytes"] += len(raw)
    meter["bytes"] += len(raw)
    if _SECRET.search(raw.decode()):
        return None, "sensitive-binding-source"
    return value, None


def context_versions(conn, scope, reads, meter):
    """A freshly validated bounded subset, never a complete task decision inventory."""
    unavailable = {"requirements": None, "design": None, "spec": None}
    if not scope.task_id or scope.permitted_source_versions is not None:
        return unavailable
    metadata = conn.execute(
        "SELECT id FROM design_entries WHERE task_id=? "
        "AND status IN ('stated','accepted','modified') ORDER BY id LIMIT 3",
        (scope.task_id,),
    ).fetchall()
    selected = [row[0] for row in metadata[:2]]
    design = {
        "selected_ids": selected,
        "overflow": len(metadata) > 2,
        "complete": len(metadata) <= 2,
        "selection_ids": [row[0] for row in metadata],
        "versions": [],
        "unavailable": [],
    }
    spec_id = conn.execute(
        "SELECT id FROM spec_versions WHERE task_id=? ORDER BY version DESC LIMIT 1",
        (scope.task_id,),
    ).fetchone()
    spec = None
    if spec_id:
        value, reason = binding_record(
            conn,
            "spec_versions s LEFT JOIN approvals a ON a.task_id=s.task_id "
            "AND a.spec_version=s.version",
            {
                "version": "s.version",
                "content_hash": "s.content_hash",
                "status": "s.status",
                "revision": "s.revision",
                "approval_id": "a.id",
            },
            "s.task_id=? AND s.id=?",
            (scope.task_id, spec_id[0]),
            reads,
            meter,
        )
        spec = {
            "selected_id": spec_id[0],
            "content_version": digest(canonical(value).encode()) if value is not None else None,
            "unavailable": reason,
        }
        if value is not None:
            spec.update(value, approval_state="recorded-reference-not-execution-authority")
    for entry_id in selected:
        value, reason = binding_record(
            conn,
            "design_entries",
            {
                name: name
                for name in (
                    "task_id",
                    "section",
                    "binding",
                    "status",
                    "statement",
                    "response",
                    "rationale",
                )
            },
            "task_id=? AND id=? AND status IN ('stated','accepted','modified')",
            (scope.task_id, entry_id),
            reads,
            meter,
        )
        if reason:
            design["complete"] = False
            design["unavailable"].append({"id": entry_id, "reason": reason})
        else:
            design["versions"].append(
                {"id": entry_id, "content_version": digest(canonical(value).encode())}
            )
    return {"requirements": None, "design": design if metadata else None, "spec": spec}
