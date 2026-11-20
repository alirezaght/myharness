"""Task-wide material identity and explicit handling, independent of owner delivery."""

import json

from harness.authoring_contract import canonical, digest, exact, text
from harness.db import utcnow


def is_material(kind, origin, payload):
    if kind in {"session.started", "session.user_response"}:
        return True
    if kind.startswith("session."):
        return False
    if origin not in {"browser", "tui_user", "legacy_cli"}:
        return False
    if kind in {"phase.changed", "batch.accepted", "final_review.passed"}:
        return False
    if kind.startswith("authoring."):
        action = kind.removeprefix("authoring.")
        if action in {
            "reconcile",
            "accept-investigation",
            "investigation-review",
            "start",
            "regenerate",
            "cancel-job",
            "retry-job",
            "investigate",
            "reconnect-jira",
            "jira-handoff",
        }:
            return False
        if action == "planning" and payload.get("input", {}).get("operation", {}).get("op") in {
            "advance",
            "back",
        }:
            return False
    return True


def projection(conn, task_id, *, include_inputs=True):
    from harness.planning_session_store import schema_available

    rows = (
        conn.execute(
            (
                "SELECT id,sequence,kind,semantic_base_json,payload_json FROM "
                "planning_session_events WHERE task_id=? AND material=1 ORDER BY "
                "sequence"
            ),
            (task_id,),
        ).fetchall()
        if schema_available(conn)
        else []
    )
    handling = (
        [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM planning_session_handling WHERE task_id=? ORDER BY rowid", (task_id,)
            )
        ]
        if rows
        else []
    )
    handled = {row["input_id"] for row in handling}
    identity = [
        {
            "id": row["id"],
            "base": json.loads(row["semantic_base_json"]),
            "payload": digest(json.loads(row["payload_json"])),
        }
        for row in rows
    ]
    result = {
        "material_revision": len(rows),
        "material_digest": digest(identity),
        "pending_material_ids": [row["id"] for row in rows if row["id"] not in handled],
        "handling": handling[-50:],
        "handling_count": len(handling),
        "handling_digest": digest([row["receipt_id"] for row in handling]),
    }
    if include_inputs:
        included, size = [], 0
        for row in rows:
            if row["id"] in handled:
                continue
            value = {
                "input_id": row["id"],
                "kind": row["kind"],
                "base": json.loads(row["semantic_base_json"]),
                "payload": json.loads(row["payload_json"]),
            }
            length = len(canonical(value).encode())
            if len(included) >= 32 or size + length > 131072:
                break
            included.append(value)
            size += length
        result["inputs"] = included
        result["omitted_input_count"] = len(result["pending_material_ids"]) - len(included)

    return result


def basis(conn, task_id):
    value = projection(conn, task_id, include_inputs=False)
    return {key: value[key] for key in ("material_revision", "material_digest")}


def record(conn, task_id, input_ids, kind, reference, captured):
    current = projection(conn, task_id, include_inputs=False)
    if len(set(input_ids)) != len(input_ids) or not set(input_ids) <= set(
        current["pending_material_ids"]
    ):
        raise ValueError("material inputs must be current, pending and task-owned")
    for ident in input_ids:
        conn.execute(
            "INSERT INTO planning_session_handling VALUES(?,?,?,?,?,?,?,?)",
            (
                task_id,
                ident,
                kind,
                reference,
                captured["material_revision"],
                captured["material_digest"],
                digest(
                    {"input": ident, "kind": kind, "reference": reference, "captured": captured}
                ),
                utcnow(),
            ),
        )


def validate_output(entries, inputs, output):
    if not isinstance(entries, list) or len(entries) > 32:
        raise ValueError("bounded material handling entries required")
    captured = inputs.get("session_material", {})
    allowed = set(captured.get("pending_material_ids", []))
    seen = set()
    for entry in entries:
        exact(entry, {"input_id", "disposition", "rationale", "reference"})
        if entry["input_id"] not in allowed or entry["input_id"] in seen:
            raise ValueError("handling references uncaptured material input")
        seen.add(entry["input_id"])
        text(entry["rationale"], "handling rationale", 4000)
        if entry["disposition"] == "no_change":
            if entry["reference"] != "assistant":
                raise ValueError("no-change handling must reference the actual response")
        elif entry["disposition"] == "proposed_content":
            if (
                entry["reference"] != "candidate"
                or output.get("body") is None
                or output.get("graph") is None
            ):
                raise ValueError("handling must reference actual proposed content")
        elif entry["disposition"] == "clarification":
            index = entry["reference"]
            if type(index) is not int or not 0 <= index < len(output["questions"]):
                raise ValueError("handling must reference an actual clarification")
        else:
            raise ValueError("unknown material handling disposition")
    return entries


def validate_manual(entries, preview, selected):
    if not isinstance(entries, list) or len(entries) > 32:
        raise ValueError("bounded explicit material reconciliation required")
    pending = set(preview["material"]["pending_material_ids"])
    allowed = {"revision:" + selected["id"]}
    if selected["graph_id"]:
        allowed.add("graph:" + selected["graph_id"])
    allowed.update(
        "target:" + row["target"] + "@" + row["revision"] for row in preview["decision_lineage"]
    )
    seen = set()
    for entry in entries:
        exact(entry, {"input_id", "disposition", "rationale", "references"})
        if entry["input_id"] not in pending or entry["input_id"] in seen:
            raise ValueError("reconciliation input must be current pending task input")
        seen.add(entry["input_id"])
        if entry["disposition"] not in {"incorporated", "no_change"}:
            raise ValueError("invalid reconciliation disposition")
        rationale = text(entry["rationale"], "reconciliation rationale", 4000)
        if len(rationale.strip()) < 20:
            raise ValueError("substantive per-input reconciliation rationale required")
        refs = entry["references"]
        if (
            not isinstance(refs, list)
            or not refs
            or len(refs) > 20
            or any(ref not in allowed for ref in refs)
        ):
            raise ValueError("reconciliation must reference selected content or current decision")
