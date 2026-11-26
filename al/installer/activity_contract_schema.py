"""Closed exchange metadata. Validation conveys no execution or approval authority."""

import hashlib
import json

FAMILIES = {
    "requirement-analysis": {"observed", "incomplete"},
    "architecture-proposal": {"proposed", "observed"},
    "design-decision": {"recorded"},
    "implementation-plan": {"authored"},
    "task-assignment": {"assigned"},
    "implementation-result": {"authored", "failed"},
    "verification-result": {"observed"},
    "failure-report": {"observed", "resolved"},
    "escalation-request": {"requested", "resolved"},
    "final-delivery": {"observed"},
}
FIELDS = {
    "schema_version",
    "kind",
    "context",
    "source_specification",
    "required_inputs",
    "expected_outputs",
    "acceptance_criteria",
    "evidence_refs",
    "constraints",
    "status",
    "failure",
    "payload",
    "field_applicability",
}
CONTEXT = {
    "al_run",
    "al_session",
    "task",
    "execution",
    "subtask",
    "graph_revision",
    "task_revision",
    "producer",
    "epoch",
    "authority_generation",
    "applicability",
}
REASONS = {"standalone", "pre-execution", "not-assigned", "not-observed", "multiple-producers"}


class ContractError(ValueError):
    def __init__(self, pointer="/", *, kind=None, code="INVALID_AGENT_CONTRACT"):
        self.feedback = {
            "code": code,
            "contract_kind": kind if kind in FAMILIES else None,
            "schema_version": 1,
            "field_pointer": pointer,
            "expected": "current versioned declared exchange",
            "repair_action": "regenerate from current assignment and immutable inputs",
            "state_changed": False,
        }
        super().__init__(json.dumps(self.feedback, sort_keys=True))


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _check(ok, pointer="/", kind=None):
    if not ok:
        raise ContractError(pointer, kind=kind)


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 4096


def reference(value, pointer="/required_inputs", kind=None):
    _check(isinstance(value, dict) and set(value) == {"kind", "id", "sha256"}, pointer, kind)
    _check(all(_text(value[k]) for k in value), pointer, kind)
    sha = value["sha256"].removeprefix("sha256:")
    _check(len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), pointer, kind)
    return value


def ref(record):
    return {
        "kind": record.get("kind", "execution-artifact"),
        "id": record.get("id", record.get("artifact_id")),
        "sha256": record["sha256"],
    }


def validate(value, *, context=None, specification=None, resolve=None):
    _check(isinstance(value, dict) and set(value) == FIELDS)
    kind = value["kind"] if isinstance(value["kind"], str) and value["kind"] in FAMILIES else None
    _check(kind is not None, "/kind")
    _check(
        type(value["schema_version"]) is int and value["schema_version"] == 1,
        "/schema_version",
        kind,
    )
    ctx = value["context"]
    _check(isinstance(ctx, dict) and set(ctx) == CONTEXT, "/context", kind)
    absent = {k for k in CONTEXT - {"applicability"} if ctx[k] is None}
    apps = ctx["applicability"]
    _check(isinstance(apps, dict) and set(apps) == absent, "/context/applicability", kind)
    _check(
        all(isinstance(v, str) and v in REASONS for v in apps.values()),
        "/context/applicability",
        kind,
    )
    for key in CONTEXT - {"applicability"} - absent:
        _check(
            type(ctx[key]) is int and ctx[key] >= 0
            if key in {"graph_revision", "task_revision", "authority_generation"}
            else _text(ctx[key]),
            "/context",
            kind,
        )
    if context is not None:
        _check(ctx == context, "/context", kind)
    spec = value["source_specification"]
    _check(
        isinstance(spec, dict) and set(spec) == {"reference", "amendments"},
        "/source_specification",
        kind,
    )
    reference(spec["reference"], "/source_specification", kind)
    _check(
        isinstance(spec["amendments"], list) and len(spec["amendments"]) <= 256,
        "/source_specification",
        kind,
    )
    for item in spec["amendments"]:
        reference(item, "/source_specification", kind)
    if specification is not None:
        _check(spec == specification, "/source_specification", kind)
    for field in ("required_inputs", "evidence_refs", "constraints"):
        rows = value[field]
        _check(isinstance(rows, list) and len(rows) <= 256, "/" + field, kind)
        for row in rows:
            _check(
                isinstance(row, dict) and set(row) == {"reference", "purpose", "required"},
                "/" + field,
                kind,
            )
            _check(_text(row["purpose"]) and type(row["required"]) is bool, "/" + field, kind)
            reference(row["reference"], "/" + field, kind)
    _check(
        isinstance(value["expected_outputs"], list) and len(value["expected_outputs"]) <= 256,
        "/expected_outputs",
        kind,
    )
    for row in value["expected_outputs"]:
        _check(
            isinstance(row, dict)
            and set(row) == {"name", "kind"}
            and _text(row["name"])
            and isinstance(row["kind"], str)
            and row["kind"] in FAMILIES,
            "/expected_outputs",
            kind,
        )
    _check(
        isinstance(value["acceptance_criteria"], list)
        and len(value["acceptance_criteria"]) <= 256
        and all(_text(x) for x in value["acceptance_criteria"]),
        "/acceptance_criteria",
        kind,
    )
    _check(isinstance(value["status"], str) and value["status"] in FAMILIES[kind], "/status", kind)
    failure = value["failure"]
    _check(
        failure is None
        or isinstance(failure, dict)
        and set(failure) == {"classification", "diagnostic_ref"}
        and isinstance(failure["classification"], str)
        and failure["classification"] in {"operational", "validation", "business", "execution"},
        "/failure",
        kind,
    )
    if failure:
        reference(failure["diagnostic_ref"], "/failure", kind)
    payload = value["payload"]
    _check(
        isinstance(payload, dict)
        and set(payload) == {"record_type", "record_id", "record_digest"}
        and payload["record_type"] == kind
        and _text(payload["record_id"]),
        "/payload",
        kind,
    )
    _check(
        isinstance(payload["record_digest"], str)
        and len(payload["record_digest"]) == 64
        and all(c in "0123456789abcdef" for c in payload["record_digest"]),
        "/payload",
        kind,
    )
    empty = {
        f
        for f in (
            "required_inputs",
            "expected_outputs",
            "acceptance_criteria",
            "evidence_refs",
            "constraints",
        )
        if not value[f]
    }
    _check(
        isinstance(value["field_applicability"], dict)
        and set(value["field_applicability"]) == empty
        and all(
            v in {"not-declared", "not-observed"} if isinstance(v, str) else False
            for v in value["field_applicability"].values()
        ),
        "/field_applicability",
        kind,
    )
    if resolve is not None:
        refs = [spec["reference"], *spec["amendments"]]
        refs += [
            r["reference"]
            for f in ("required_inputs", "evidence_refs", "constraints")
            for r in value[f]
            if r["required"]
        ]
        if failure:
            refs.append(failure["diagnostic_ref"])
        for item in refs:
            try:
                raw = resolve(item)
                _check(
                    hashlib.sha256(raw).hexdigest() == item["sha256"].removeprefix("sha256:"),
                    "/required_inputs",
                    kind,
                )
            except (OSError, ValueError, KeyError, TypeError):
                raise ContractError("/required_inputs", kind=kind) from None
    return value


def make(
    kind,
    context,
    specification,
    record_id,
    record,
    *,
    status=None,
    inputs=(),
    outputs=(),
    criteria=(),
    evidence=(),
    constraints=(),
    failure=None,
):
    value = {
        "schema_version": 1,
        "kind": kind,
        "context": context,
        "source_specification": specification,
        "required_inputs": list(inputs),
        "expected_outputs": list(outputs),
        "acceptance_criteria": list(criteria),
        "evidence_refs": list(evidence),
        "constraints": list(constraints),
        "status": status or sorted(FAMILIES[kind])[0],
        "failure": failure,
        "payload": {
            "record_type": kind,
            "record_id": str(record_id),
            "record_digest": fingerprint(record),
        },
    }
    value["field_applicability"] = {
        f: "not-declared"
        for f in (
            "required_inputs",
            "expected_outputs",
            "acceptance_criteria",
            "evidence_refs",
            "constraints",
        )
        if not value[f]
    }
    return validate(value)


def dependency_records(state, task):
    """Metadata only: immutable receipt contents selected by the existing task graph."""
    tasks = state.get("integration", {}).get("tasks", {})
    found = {}

    def visit(identifier, stack):
        if identifier in stack:
            raise ContractError("/required_inputs", kind="task-assignment")
        for dep in tasks[identifier]["dependencies"]:
            if dep in (*stack, identifier):
                raise ContractError("/required_inputs", kind="task-assignment")
            if dep in found:
                continue
            producer = tasks[dep]
            receipt = (
                producer.get("completion_binding")
                if producer["status"] == "Completed"
                else producer.get("authored_output")
            )
            if not receipt:
                raise ContractError("/required_inputs", kind="task-assignment")
            found[dep] = {
                "task_id": dep,
                "task_revision": producer["revision"],
                "graph_revision": state["integration"]["graph_revision"],
                "receipt": receipt,
            }
            visit(dep, (*stack, identifier))

    if task in tasks:
        visit(task, ())
    return found


def validate_dependencies(value, state, task, resolve):
    expected = dependency_records(state, task)
    rows = {
        r["purpose"].removeprefix("dependency:"): r["reference"]
        for r in value["required_inputs"]
        if r["required"] and r["purpose"].startswith("dependency:")
    }
    _check(
        len(rows)
        == sum(
            r["required"] and r["purpose"].startswith("dependency:")
            for r in value["required_inputs"]
        ),
        "/required_inputs",
        value["kind"],
    )
    _check(set(rows) == set(expected), "/required_inputs", value["kind"])
    for identifier, record in expected.items():
        _check(
            rows[identifier]["kind"] == "dependency-receipt-v1", "/required_inputs", value["kind"]
        )
        try:
            actual = json.loads(resolve(rows[identifier]))
        except (OSError, ValueError, KeyError, TypeError):
            raise ContractError("/required_inputs", kind=value["kind"]) from None
        _check(actual == record, "/required_inputs", value["kind"])
