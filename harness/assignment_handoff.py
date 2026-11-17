"""Authenticated delivery of the current generated AL writer handoff, without new authority."""

import hashlib
import json
from pathlib import Path

from harness.execution_contract import digest
from harness.execution_hosts import al_source
from harness.execution_logs import MAX_TEXT, _redact
from harness.knowledge.sources import read_artifact

RESOURCE = "harness:assignment-handoff"


def _selected(operation, controller, principal):
    assignment = operation.assignments.get(principal["thread_id"])
    if not principal.get("writable") or not assignment:
        raise ValueError("writer handoff requires this principal's writable assignment")
    execution, state, _ = operation.admitted(
        controller, principal, assignment["task"], assignment["revision"]
    )
    writer = next(
        w
        for w in state["writers"].values()
        if w.get("agent") == principal["thread_id"]
        and w["state"] == "ACTIVE"
        and w.get("task") == assignment["task"]
    )
    if writer["phase"] != state["phase"]:
        raise ValueError("writer handoff phase is stale")
    receipts = writer.get("handoffs", [])
    if not receipts:
        return None
    receipt = receipts[-1]
    areas = receipt["areas"]
    plan = state["evidence"]["plan"]
    if not areas or any(area not in plan for area in areas):
        raise ValueError("writer handoff evidence plan is stale")
    expected = hashlib.sha256(
        json.dumps({a: plan[a] for a in areas}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if expected != receipt["plan_digest"]:
        raise ValueError("writer handoff evidence plan changed; regenerate it")
    reference = receipt["artifact"]
    if (
        reference.get("kind") != "writer-handoff"
        or reference.get("captured_at", 0) < writer["started_at"]
    ):
        raise ValueError("writer handoff generation binding differs")
    captured = reference["captured_at"]
    structural = {
        "task.registered",
        "dependency.changed",
        "ownership.changed",
        "amendment.created",
        "amendment.revalidated",
    }
    if any(
        e["kind"] in structural and e["timestamp"] > captured
        for e in state["integration"].get("outbox", [])
    ):
        raise ValueError("writer handoff predates changed task graph; regenerate it")
    raw = read_artifact(
        Path(al_source(execution, operation.envelope)).parent
        / execution["al_session_id"]
        / "artifacts",
        reference,
    )
    text = raw.decode("utf-8")
    begin, end = (
        "<!-- AL MANAGED WRITER EVIDENCE BEGIN -->",
        "<!-- AL MANAGED WRITER EVIDENCE END -->",
    )
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) >= text.index(end):
        raise ValueError("complete managed writer evidence block required")
    block = text.split(begin, 1)[1].split(end, 1)[0]
    expected_lines = [
        f"Writer: {writer['id']} · role: {writer['role']} · phase: {writer['phase']}",
        "Ownership: " + json.dumps(writer["files"]),
        "Repository: " + str(operation.envelope["repository"]),
        "AL run: " + state["run_id"],
    ]
    if not all(line in block.splitlines() for line in expected_lines):
        raise ValueError("managed writer handoff differs from current ownership")
    context = {
        "assignment": assignment,
        "principal": principal["thread_id"],
        "execution": execution["id"],
        "epoch": execution.get("epoch_id", "legacy-0"),
        "authority_generation": execution.get("authority_generation", 0),
        "phase": state["phase"],
        "run": state["run_id"],
        "writer": {
            k: writer.get(k)
            for k in ("id", "task", "agent", "phase", "files", "commands", "started_at", "state")
        },
        "task": state["integration"]["tasks"][assignment["task"]],
        "graph_revision": state["integration"]["graph_revision"],
        "amendments": state.get("amendments", []),
        "task_package": state.get("task_package"),
        "evidence": receipt,
        "policy": principal["policy"],
    }
    binding = digest(context).removeprefix("sha256:")
    rendered = _redact(text).encode()
    return {"binding": binding, "raw": raw, "presentation": rendered, "reference": reference}


def descriptor(operation, controller, principal):
    selected = _selected(operation, controller, principal)
    if selected is None:
        return {"status": "not-generated"}
    return {
        "status": "available",
        "resource": RESOURCE + ":" + selected["binding"],
        "sha256": selected["reference"]["sha256"],
        "size": len(selected["raw"]),
        "presentation_sha256": hashlib.sha256(selected["presentation"]).hexdigest(),
        "presentation_size": len(selected["presentation"]),
        "offset_unit": "presentation-utf8-byte",
    }


def page(operation, controller, principal, resource, *, offset=0, limit=16384):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536:
        raise ValueError("bounded writer handoff cursor required")
    selected = _selected(operation, controller, principal)
    if selected is None:
        return {"status": "not-generated"}
    expected = RESOURCE + ":" + selected["binding"]
    if resource != expected and not (resource == RESOURCE and offset == 0):
        raise ValueError("writer handoff context changed; obtain the current assignment")
    raw = selected["presentation"]
    if offset > len(raw) or offset < len(raw) and raw[offset] & 0xC0 == 0x80:
        raise ValueError("writer handoff cursor splits presentation")
    value = raw[offset : offset + min(limit, MAX_TEXT)]
    while value:
        try:
            text = value.decode("utf-8")
            break
        except UnicodeDecodeError as error:
            if error.reason != "unexpected end of data" or error.end != len(value):
                raise ValueError("invalid writer handoff UTF-8") from None
            value = value[: error.start]
    else:
        if offset < len(raw):
            raise ValueError("writer handoff limit too small for UTF-8")
        text = ""
    return {
        "status": "available",
        "resource": expected,
        "text": text,
        "sha256": selected["reference"]["sha256"],
        "size": len(selected["raw"]),
        "presentation_sha256": hashlib.sha256(raw).hexdigest(),
        "presentation_size": len(raw),
        "presentation_redacted": raw != selected["raw"],
        "offset": offset,
        "next_offset": offset + len(value),
        "truncated": offset + len(value) < len(raw),
        "offset_unit": "presentation-utf8-byte",
    }
