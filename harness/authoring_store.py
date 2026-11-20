"""Transaction-owned immutable authoring records; reads never initialize state."""

import json
import uuid
from dataclasses import asdict

from harness.authoring_contract import AuthoringConflict, canonical, digest, request_identity
from harness.db import utcnow


def identity():
    return str(uuid.uuid4())


def source_state(controller, task_id):
    ws = controller.workspace(task_id)
    task = controller.get_task(task_id)

    def stable(row):
        return {
            k: v
            for k, v in asdict(row).items()
            if k not in {"created_at", "updated_at", "asked_at", "resolved_at"}
        }

    state = {
        "request": task.request,
        "complexity": str(task.complexity),
        "complexity_source": task.complexity_source,
    }
    from harness.investigation_artifacts import stored

    managed = stored(controller.conn, task_id)
    if managed:
        state["managed_artifacts"] = managed["digest"]
    from harness.effective_records import effective_records

    effective = effective_records(ws)
    state["questions"] = [stable(row) for row in effective.questions]
    state["proposals"] = [stable(row) for row in effective.proposals]
    state["effective_lineage"] = effective.metadata()
    state["historical"] = {
        "questions": [
            stable(row)
            for row in effective.historical_questions
            if row.id in effective.question_successors
        ],
        "proposals": [
            stable(row)
            for row in effective.historical_proposals
            if row.id in effective.proposal_successors
        ],
    }
    for name in ("design_entries", "scenarios", "decision_changes"):
        state[name] = [stable(row) for row in getattr(ws, name)()]
    state["plan_approval"] = (
        stable(ws.latest_plan_approval()) if ws.latest_plan_approval() else None
    )
    for label in ("investigation", "context_accepted"):
        checkpoint = ws.latest_checkpoint(label)
        state[label] = checkpoint.payload if checkpoint else None
        if label == "investigation" and checkpoint:
            from harness.current_decisions import current_decisions

            state["historical"]["investigation"] = checkpoint.payload
            state[label] = dict(checkpoint.payload, decisions=current_decisions(ws))
    from pathlib import Path

    from harness.current_decisions import current_recorded_decisions
    from harness.spec_manager import _recorded_decisions

    def serial(value):
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {k: serial(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [serial(v) for v in value]
        return value

    state["recorded_decisions"] = [
        serial(asdict(record)) for record in current_recorded_decisions(ws)
    ]
    state["historical"]["recorded_decisions"] = [
        serial(asdict(record)) for record in _recorded_decisions(ws.events())
    ]
    from harness.investigation_store import InvestigationStore

    investigation = InvestigationStore(controller, None, task_id).semantic()
    if investigation is not None:
        state["source_investigation"] = investigation
    return state


def authority_state(controller, task_id):
    from harness.execution_store import latest_execution

    execution = latest_execution(controller.conn, task_id)
    approval = controller.conn.execute(
        "SELECT * FROM approvals WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)
    ).fetchone()
    return {
        "phase": str(controller.get_task(task_id).phase),
        "approval": dict(approval) if approval else None,
        "execution": {
            k: execution.get(k)
            for k in ("id", "status", "epoch_id", "authority_generation", "approval_id")
        }
        if execution
        else None,
    }


class AuthoringStore:
    def __init__(self, controller, project, task_id):
        self.controller, self.conn, self.project, self.task_id = (
            controller,
            controller.conn,
            project,
            task_id,
        )
        controller.get_task(task_id)
        row = self.binding()
        if row and row["project"] != project:
            raise LookupError("task belongs to another project")

    def writing(self):
        if not self.conn.in_transaction:
            raise RuntimeError("authoring write requires caller transaction")

    def binding(self):
        return self.conn.execute(
            "SELECT * FROM authoring_tasks WHERE task_id=?", (self.task_id,)
        ).fetchone()

    def initialize(self):
        self.writing()
        self.conn.execute(
            "INSERT OR IGNORE INTO authoring_tasks(task_id,project) VALUES(?,?)",
            (self.task_id, self.project),
        )

    def base(self):
        row = self.binding()
        working = self.revision(row["working_id"]) if row else None
        return {
            "project": self.project,
            "task": self.task_id,
            "generation": row["generation"] if row else 0,
            "source": digest(source_state(self.controller, self.task_id)),
            "working": row["working_id"] if row else None,
            "graph": working["graph_id"] if working else None,
            "authority": digest(authority_state(self.controller, self.task_id)),
        }

    def check(self, expected):
        if expected != self.base():
            raise AuthoringConflict("authoring source or revision changed; reload")

    def bump(self):
        self.writing()
        self.conn.execute(
            "UPDATE authoring_tasks SET generation=generation+1 WHERE task_id=?", (self.task_id,)
        )

    def revision(self, ident):
        if ident is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM authoring_revisions WHERE task_id=? AND id=?", (self.task_id, ident)
        ).fetchone()
        if row is None:
            raise LookupError("unknown task revision")
        value = dict(row)
        for key in ("source", "obligations"):
            value[key] = json.loads(value.pop(key + "_json"))
        value["graph"] = self.graph(value["graph_id"])
        return value

    def graph(self, ident):
        if ident is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM authoring_graphs WHERE task_id=? AND id=?", (self.task_id, ident)
        ).fetchone()
        if row is None:
            raise LookupError("unknown task graph")
        return {"id": ident, "digest": row["digest"], "value": json.loads(row["graph_json"])}

    def add_revision(self, body, *, actor, parent=None, graph=None, graph_id=None, obligations=()):
        self.writing()
        if graph is not None:
            graph_id = identity()
            self.conn.execute(
                "INSERT INTO authoring_graphs VALUES(?,?,?,?,?)",
                (graph_id, self.task_id, canonical(graph), digest(graph), utcnow()),
            )
        source, ident = source_state(self.controller, self.task_id), identity()
        self.conn.execute(
            "INSERT INTO authoring_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                ident,
                self.task_id,
                parent,
                body,
                digest(body),
                graph_id,
                canonical(source),
                digest(source),
                actor,
                canonical(list(obligations)),
                utcnow(),
            ),
        )
        return ident

    def contribution(self, kind, actor, payload, supersedes=None):
        self.writing()
        ident = identity()
        self.conn.execute(
            "INSERT INTO authoring_contributions VALUES(?,?,?,?,?,?,?)",
            (ident, self.task_id, kind, actor, canonical(payload), supersedes, utcnow()),
        )
        return ident

    def receipt(self, request_id, payload, *, scope=None):
        request_identity(request_id)
        scope = scope or self.task_id
        row = self.conn.execute(
            "SELECT * FROM authoring_receipts WHERE scope=? AND request_id=?", (scope, request_id)
        ).fetchone()
        if row and row["payload_hash"] != digest(payload):
            raise AuthoringConflict("request_id already used with different content")
        return json.loads(row["result_json"]) if row else None

    def record_receipt(self, request_id, payload, result, *, scope=None):
        self.writing()
        self.conn.execute(
            "INSERT INTO authoring_receipts VALUES(?,?,?,?)",
            (scope or self.task_id, request_id, digest(payload), canonical(result)),
        )


def adopted_revision(controller, task_id):
    row = controller.conn.execute(
        "SELECT * FROM authoring_tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    return (
        AuthoringStore(controller, row["project"], task_id).revision(row["adopted_id"])
        if row and row["adopted_id"]
        else None
    )


def approval_token(controller, task_id, report_hash):
    row = controller.conn.execute(
        "SELECT project FROM authoring_tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    if not row:
        return report_hash
    return "sha256:" + digest(
        {"review": report_hash, "base": AuthoringStore(controller, row["project"], task_id).base()}
    )
