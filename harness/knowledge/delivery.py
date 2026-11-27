"""Task preferences and bounded presentation, never assignment or execution authority."""

from enum import Enum

from harness.knowledge.models import ContextRequest, QueryLimits, canonical, digest


class AutomaticDeliveryPolicy(Enum):
    """Trusted controller setup selects presentation only, never query authority."""

    ENABLED = "enabled"
    EXPLICIT_ONLY = "explicit-only"


def _text(value, limit=4096):
    return str(value).replace("\0", "").encode()[:limit].decode("utf-8", errors="ignore")


def request_for(controller, execution, principal, state, *, scope=None):
    """Use only this authenticated principal's task, or the supervisor's objective."""
    integration = state.get("integration", {})
    agent = integration.get("agents", {}).get(principal["thread_id"])
    task = integration.get("tasks", {}).get(agent.get("task")) if agent else None
    if principal["role"] != "orchestrator" and task is None:
        raise PermissionError("knowledge:binding-missing")
    objective = task.get("title", "") if task else controller.get_task(execution["task_id"]).request
    phase = state.get("phase", "")
    stage = (
        "recovery"
        if state.get("gate_recovery")
        or phase in {"RECOVERY", "REWORK", "CORRECTION", "REWORK_REASSESSMENT"}
        else "verification"
        if phase in {"VERIFICATION", "FINAL_VERIFICATION", "REVIEW"}
        else "planning"
        if phase in {"PLANNING", "PSEUDOCODE", "DESIGN", "DISCOVERY"}
        or principal["role"]
        in {
            "al-scout",
            "al-architect",
            "al-architect-fallback",
            "al-designer",
            "al-designer-fallback",
        }
        else "implementation"
    )
    if stage == "recovery" and state.get("phase_occurrences"):
        occurrence = state["phase_occurrences"][-1]
        objective = (
            _text(objective, 3900)
            + "\nRecovery occurrence: "
            + digest(canonical(occurrence).encode())
        )
    files = task.get("files", ()) if task else ()
    affected = affected_files(controller.conn, scope, files) if scope is not None else ()
    return ContextRequest(
        _text(objective),
        objective=_text(objective),
        stage=stage,
        affected_entities=affected,
        estimated_tokens=8192,
        limits=QueryLimits(bytes=28672),
    )


def affected_files(conn, scope, paths):
    """Expand only authorized indexed directory members; never infer corpus authority."""
    from pathlib import PurePosixPath

    from harness.knowledge.policy import validate_scope
    from harness.knowledge.query import _partition

    if not paths:
        return ()
    if len(paths) > 40:
        raise ValueError("knowledge:assignment-scope-exceeds-context-budget")
    project = validate_scope(conn, scope)
    _partition(conn, scope, project["active_snapshot_id"])
    resolved = set()
    for raw in paths:
        if not isinstance(raw, str):
            raise PermissionError("knowledge:invalid-assignment-scope")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts:
            raise PermissionError("knowledge:invalid-assignment-scope")
        name = path.as_posix()
        if len(("file:" + name).encode()) > 512:
            raise ValueError("knowledge:assignment-scope-exceeds-context-budget")
        prefix = "" if name == "." else name + "/"
        rows = conn.execute(
            "SELECT DISTINCT s.locator FROM knowledge_sources s "
            "JOIN knowledge_source_versions v USING(source_id) "
            "JOIN knowledge_evidence e USING(version_id) "
            "JOIN temp.k_eligible k USING(assertion_id) "
            "WHERE s.project_id=? AND s.kind IN ('python','text') "
            "AND (s.locator=? OR substr(s.locator,1,?)=?) "
            "ORDER BY s.locator LIMIT 41",
            (scope.project_id, name, len(prefix), prefix),
        ).fetchall()
        if len(rows) > 40:
            raise ValueError("knowledge:directory-scope-exceeds-context-budget")
        resolved.update("file:" + row[0] for row in rows)
        if not rows:
            # The query reports unresolved identities; absence from this index is not absence
            # from the repository. Do not scan another source or silently widen the scope.
            resolved.add("file:" + name)
        if len(resolved) > 40 or any(len(ref.encode()) > 512 for ref in resolved):
            raise ValueError("knowledge:assignment-scope-exceeds-context-budget")
    return tuple(sorted(resolved))


def binding(operation, controller, principal, *, include_handoff=True):
    """Capture current authority inputs without retaining an open dispatch connection."""
    from harness.assignment_handoff import descriptor
    from harness.assignment_observation import registered_assignment

    execution, state = operation.authority(controller, principal, operation="read")
    observed = registered_assignment(state, execution, principal)
    result = {
        "execution": execution["id"],
        "epoch": execution["epoch_id"],
        "authority_generation": execution["authority_generation"],
        "phase": state["phase"],
        "registration": observed,
        "request": request_for(controller, execution, principal, state),
        "assignment": dict(operation.assignments.get(principal["thread_id"], {})),
        "task_package": state.get("task_package"),
        "recovery": state.get("gate_recovery"),
        "rework": state.get("rework"),
        "rework_recovery": state.get("rework_recovery"),
        "phase_history": state.get("phase_occurrences"),
        "task": state.get("integration", {}).get("tasks", {}).get(observed.get("al_task_id")),
        "writers": [
            w for w in state.get("writers", {}).values() if w.get("agent") == principal["thread_id"]
        ],
        "evidence_plan": state.get("evidence", {}).get("plan"),
        "amendments": state.get("amendments"),
        "outbox": state.get("integration", {}).get("outbox"),
        "policy": principal["policy"],
    }
    if include_handoff and principal.get("writable") and principal["role"] != "orchestrator":
        result["handoff"] = descriptor(operation, controller, principal)
    return result


def unavailable(reason):
    return {
        "status": "unavailable",
        "reason": reason,
        "fallback": (
            "Use authorized read_file, repository search and immutable task/handoff sources. "
            "Context availability does not waive approval or execution checks."
        ),
    }


def size_estimate(payload):
    """Expose only K1a's numeric estimate and fixed labels across execution redaction."""
    value = payload["budget"]["estimated_tokens"]
    requested = payload["budget"]["max_estimated_tokens"]
    if type(value) is not int or type(requested) is not int or not 0 <= value <= requested <= 32768:
        raise ValueError("invalid knowledge size estimate")
    return {
        "value": value,
        "requested_budget": requested,
        "unit": "estimated tokens",
        "method": "serialized-utf8-bytes-div4-plus50pct-margin-not-tokenizer-exact",
        "uncertainty": (
            "50% safety margin, not an upper bound; IDs, language and tokenizer can exceed it."
        ),
    }


def presentation(bundle):
    bundle = dict(bundle)
    estimate = bundle.pop("size_estimate")
    raw = canonical(bundle).encode()
    if len(raw) > 32768:
        raise ValueError("knowledge presentation exceeds supported bounds")
    result = {
        "status": "available",
        "bundle_hash": bundle["bundle_hash"],
        "snapshot_id": bundle["snapshot_id"],
        "receipt_id": bundle["receipt_id"],
        "assertion_ids": [a["assertion_id"] for a in bundle["assertions"]],
        "context": bundle,
        "presentation": {
            "sha256": digest(raw),
            "size": len(raw),
            "encoding": "canonical-json-utf8",
            "boundary": "await_assignment-return",
            "observation": "server-return-prepared",
            "model_use": "unobserved",
            "transformation": "execution-redaction; cited evidence must be unchanged",
            "size_estimate": estimate,
            "scope_basis": (
                "Normalized assignment paths; directory expansion uses authorized indexed evidence "
                "only, not a complete repository inventory; maximum 40 paths."
            ),
        },
    }
    if len(canonical(result).encode()) > 32768:
        raise ValueError("knowledge presentation exceeds supported bounds")
    return result


def validate_return(conn, scope, returned, receipt):
    """Recheck cited sources with the original receipt's remaining shared read budget."""
    import json

    from harness.execution_logs import redact_value
    from harness.knowledge.models import MAX_REFRESH_BYTES
    from harness.knowledge.query import (
        _citation_current,
        _evidence,
        _explanation_binding,
        _partition,
    )

    payload = json.loads(receipt["payload"])
    expected = redact_value(
        {"bundle_hash": receipt["bundle_hash"], **payload, "receipt_id": receipt["receipt_id"]}
    )
    presented = {k: v for k, v in returned.items() if k != "source_coverage"}
    if presented != expected:
        raise ValueError("knowledge returned bytes differ from issued receipt")
    detail = json.loads(receipt["integrity_json"])
    reads = {
        "count": receipt["source_reads"],
        "bytes": detail["source_bytes"],
        "max_count": 80,
        "max_bytes": MAX_REFRESH_BYTES,
    }
    if reads["count"] > 76 or reads["bytes"] > MAX_REFRESH_BYTES - 4 * 1024 * 1024:
        raise ValueError("knowledge:delivery-budget-unavailable")
    _explanation_binding(conn, scope, payload, reads)
    _partition(conn, scope, payload["snapshot_id"])
    cache = {}
    for assertion in payload["assertions"]:
        aid = assertion["assertion_id"]
        if not conn.execute(
            "SELECT 1 FROM temp.k_eligible WHERE assertion_id=?", (aid,)
        ).fetchone():
            raise PermissionError("selected knowledge no longer permitted")
        if not all(
            _citation_current(scope, ref, conn=conn, cache=cache, reads=reads)
            for ref in _evidence(conn, aid)
        ):
            if reads.get("limited"):
                raise ValueError("knowledge:delivery-budget-unavailable")
            raise ValueError("selected source changed before assignment return")
