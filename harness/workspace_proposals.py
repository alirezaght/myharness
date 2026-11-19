"""Append-only, task/base-bound proposals. These records grant no execution authority."""

import copy
import math
import re
import uuid

from harness.dashboard.workspace_view import canonical, digest, source_snapshot
from harness.db import transaction, utcnow


class ProposalConflict(ValueError):
    """Reload source state or use a new request identity."""


def text(value, name, limit=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a non-empty string of at most {limit} characters")
    return value


def apply_operations(source, operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
        raise ValueError("operations must contain 1 to 200 edits")
    result = copy.deepcopy(source)
    result["proposed_links"] = []
    requirements = {row["key"]: row for row in result["requirements"]}
    plan = {row["key"]: row for row in result["plan"] if row["effective"]}
    nodes = {row["id"]: row for row in result["graph"]["nodes"]}
    edges = {row["id"]: row for row in result["graph"]["edges"]}
    seen = set()
    schemas = {
        "requirement": {"text"},
        "plan": {"text"},
        "node_label": {"label"},
        "node_position": {"x", "y"},
        "connection_add": {"from", "to", "label"},
        "connection_change": {"from", "to", "label"},
        "connection_remove": set(),
        "traceability": {"requirement"},
    }
    for operation in operations:
        if (
            not isinstance(operation, dict)
            or not isinstance(operation.get("type"), str)
            or operation.get("type") not in schemas
        ):
            raise ValueError("unknown operation type")
        kind = operation["type"]
        if set(operation) != {"type", "target"} | schemas[kind]:
            raise ValueError(f"invalid keys for {kind}")
        target = text(operation["target"], "target", 200)
        family = "connection" if kind.startswith("connection_") else kind
        unique = (family, target)
        if unique in seen:
            raise ValueError("duplicate operation target")
        seen.add(unique)
        store = (
            requirements
            if kind == "requirement"
            else plan
            if kind == "plan"
            else nodes
            if kind.startswith("node_")
            else edges
        )
        if kind == "traceability":
            if target not in nodes and target not in edges and target not in plan:
                raise LookupError("unknown traceability target")
            if operation["requirement"] not in requirements:
                raise LookupError("unknown requirement")
            result["proposed_links"].append(
                {"target": target, "requirement": operation["requirement"]}
            )
            continue
        if kind == "connection_add":
            if target in edges or not re.fullmatch(r"proposed:[A-Za-z0-9-]{1,80}", target):
                raise ValueError("added connection needs a unique proposed: ID")
            edges[target] = {"id": target, "sources": [], "proposed": True}
        elif target not in store:
            raise LookupError(f"unknown {kind} target {target}")
        if kind == "connection_remove":
            del edges[target]
            continue
        if kind in ("connection_add", "connection_change"):
            if any(operation[name] not in nodes for name in ("from", "to")):
                raise LookupError("connection endpoints must belong to this task graph")
            edges[target].update({name: operation[name] for name in ("from", "to")})
        for name in schemas[kind] - {"from", "to"}:
            value = operation[name]
            if name in ("x", "y"):
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not -10000 <= value <= 10000
                ):
                    raise ValueError("coordinates must be finite numbers between -10000 and 10000")
            elif name == "label" and kind.startswith("connection_"):
                if not isinstance(value, str) or len(value) > 500:
                    raise ValueError("connection label must be at most 500 characters")
            else:
                text(value, name, 500 if name == "label" else 16000)
            store[target][name] = value
    result["graph"]["edges"] = list(edges.values())
    return result


class WorkspaceProposalService:
    def __init__(self, controller, task_id):
        self.controller = controller
        self.conn = controller.conn
        self.task = controller.get_task(task_id)

    @staticmethod
    def decode(row):
        import json

        value = dict(row)
        for key in ("source", "operations", "result"):
            value[key] = json.loads(value.pop(f"{key}_json"))
        value.pop("request_hash")
        return value

    def list(self, current_base):
        rows = self.conn.execute(
            "SELECT * FROM workspace_proposals WHERE task_id=? ORDER BY rowid", (self.task.id,)
        )
        return [
            self.decode(row)
            | {
                "stale": row["base"] != current_base,
                "stale_reason": "Source or execution identity changed; reload before editing"
                if row["base"] != current_base
                else None,
            }
            for row in rows
        ]

    def add(self, base, operations, request_id, store, execution):
        text(base, "base", 64)
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", request_id):
            raise ValueError("request_id must be 8 to 64 letters, digits or hyphens")
        request_hash = digest({"base": base, "operations": operations})
        if len(canonical(operations).encode()) > 100000:
            raise ValueError("operations exceed 100000 bytes")
        with transaction(self.conn):
            prior = self.conn.execute(
                "SELECT * FROM workspace_proposals WHERE task_id=? AND request_id=?",
                (self.task.id, request_id),
            ).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise ProposalConflict("request_id already used with different content")
                return self.decode(prior), False
            source = source_snapshot(
                self.controller, self.controller.get_task(self.task.id), store, execution()
            )
            if digest(source) != base:
                raise ProposalConflict(
                    "Source or execution identity changed; reload before editing"
                )
            result = apply_operations(source, operations)
            proposal_id = str(uuid.uuid4())
            self.conn.execute(
                "INSERT INTO workspace_proposals VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id,
                    self.task.id,
                    request_id,
                    request_hash,
                    base,
                    canonical(source),
                    canonical(operations),
                    canonical(result),
                    utcnow(),
                ),
            )
            self.controller.workspace(self.task.id).record_event(
                "workspace.proposed", {"id": proposal_id, "base": base, "edits": len(operations)}
            )
            return self.decode(
                self.conn.execute(
                    "SELECT * FROM workspace_proposals WHERE id=?", (proposal_id,)
                ).fetchone()
            ), True
