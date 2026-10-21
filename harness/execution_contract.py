"""Version-one bounded AL execution protocol. No workflow authority is granted here."""

import hashlib
import json
import math
import re

VERSION = 1
MAX_RECORD = 262144
MAX_HANDOFF = 4194304
BINDING_FIELDS = {
    "execution_id",
    "task_id",
    "approval_id",
    "spec_version",
    "content_hash",
    "revision",
    "review_state_hash",
    "handoff_hash",
    "al_session_id",
    "al_run_id",
}
KINDS = {
    "run.started",
    "run.ended",
    "phase.changed",
    "agent.registered",
    "agent.updated",
    "task.registered",
    "task.updated",
    "dependency.changed",
    "ownership.changed",
    "handoff.created",
    "blocker.raised",
    "blocker.resolved",
    "decision.acknowledged",
    "test.started",
    "test.result",
    "implementation.result",
    "delivery.updated",
    "writer.updated",
    "amendment.created",
    "amendment.revalidated",
}
STATUSES = {"Idle", "Running", "Waiting", "Blocked", "Failed", "Completed"}


# A trusted harness caller pins the import root; isolated Python ignores target cwd/PYTHONPATH.
BOOTSTRAP = """import os,runpy,sys
root=os.environ.get('HARNESS_AUTHORITY_ROOT','')
if not root or not os.path.isabs(root):
    raise SystemExit('trusted harness authority root is unavailable')
module=sys.argv.pop(1)
if module not in {'harness.execution_authority','harness.execution_worker'}:
    raise SystemExit('unsupported execution bootstrap module')
sys.path.insert(0,root)
runpy.run_module(module,run_name='__main__',alter_sys=True)
"""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    raw = value if isinstance(value, bytes) else canonical(value).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def loads(raw, *, max_bytes=MAX_RECORD):
    if len(raw) > max_bytes:
        raise ValueError("protocol record exceeds limit")
    return json.loads(
        raw,
        object_pairs_hook=_pairs,
        parse_constant=lambda s: (_ for _ in ()).throw(ValueError("nonfinite JSON: " + s)),
    )


def bounded(value, depth=0):
    if depth > 12:
        raise ValueError("protocol nesting exceeds limit")
    if isinstance(value, str):
        if len(value) > 65536:
            raise ValueError("protocol string exceeds limit")
    elif isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("protocol object exceeds limit")
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError("non-string JSON key")
            bounded(k, depth + 1)
            bounded(v, depth + 1)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise ValueError("protocol collection exceeds limit")
        for v in value:
            bounded(v, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite number")
    elif value is not None and type(value) not in (bool, int, float):
        raise ValueError("invalid JSON value")


def text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 65536:
        raise ValueError("invalid " + label)
    return value


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid entity identity")
    return value


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid " + label)
    return value


def validate_binding(value):
    if not isinstance(value, dict) or set(value) != BINDING_FIELDS:
        raise ValueError("invalid immutable integration binding fields")
    for key in BINDING_FIELDS - {"approval_id", "spec_version"}:
        text(value[key], key)
    integer(value["approval_id"], "approval_id", 1)
    integer(value["spec_version"], "spec_version", 1)
    for key in ("content_hash", "review_state_hash", "handoff_hash"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value[key]):
            raise ValueError("invalid " + key)
    for key in ("execution_id", "task_id", "al_session_id", "al_run_id"):
        identity(value[key])
    return value


def progress(value):
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("invalid progress")
    if set(value) == {"completed", "total", "unit"}:
        integer(value["completed"], "completed")
        integer(value["total"], "total", 1)
        if value["completed"] > value["total"]:
            raise ValueError("progress exceeds total")
        text(value["unit"], "unit")
    elif set(value) == {"value", "source", "label"}:
        if (
            type(value["value"]) not in (float, int)
            or not math.isfinite(value["value"])
            or not 0 <= value["value"] <= 100
        ):
            raise ValueError("invalid estimate")
        text(value["source"], "estimate source")
        text(value["label"], "estimate label")
    else:
        raise ValueError("progress needs measured units or labelled estimate")


def string_list(value, label, *, ids=False, nonempty=False):
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(v, str) or not v for v in value)
    ):
        raise ValueError("invalid " + label)
    if len(value) != len(set(value)):
        raise ValueError("duplicate " + label)
    for item in value:
        (identity if ids else lambda v: text(v, label))(item)
    return value


def paths(value):
    string_list(value, "ownership paths", nonempty=True)
    from pathlib import PurePosixPath

    for item in value:
        path = PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts or item in {".", ""} or path.as_posix() != item:
            raise ValueError("invalid repository-relative ownership path")


def validate_event(value):
    bounded(value)
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "event_id",
        "sequence",
        "timestamp",
        "binding",
        "kind",
        "payload",
    }:
        raise ValueError("invalid event envelope fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != VERSION:
        raise ValueError("unsupported event schema")
    validate_binding(value["binding"])
    identity(value["event_id"])
    integer(value["sequence"], "event sequence", 1)
    if (
        type(value["timestamp"]) not in (float, int)
        or not math.isfinite(value["timestamp"])
        or value["timestamp"] <= 0
    ):
        raise ValueError("invalid event timestamp")
    kind, p = value["kind"], value["payload"]
    if kind not in KINDS or not isinstance(p, dict):
        raise ValueError("unknown event kind/payload")
    required = {
        "run.started": {"status"},
        "run.ended": {"status"},
        "phase.changed": {"phase"},
        "agent.registered": {"id", "name", "role", "status"},
        "agent.updated": {"id", "status"},
        "task.registered": {
            "id",
            "title",
            "status",
            "dependencies",
            "files",
            "criteria",
            "revision",
        },
        "task.updated": {"id", "status", "revision"},
        "dependency.changed": {"task", "dependencies"},
        "ownership.changed": {"task", "files"},
        "handoff.created": {"from", "to", "text"},
        "blocker.raised": {
            "id",
            "revision",
            "tasks",
            "question",
            "context",
            "alternatives",
            "recommendation",
        },
        "blocker.resolved": {"id", "delivery_id"},
        "decision.acknowledged": {"delivery_id", "blocker_id", "state", "decision_hash"},
        "test.started": {"id", "area", "status", "scope"},
        "test.result": {"id", "area", "status", "scope", "validity"},
        "implementation.result": {"status", "approval"},
        "delivery.updated": {"status"},
        "writer.updated": {"id", "state"},
        "amendment.created": {"id", "tasks", "revision"},
        "amendment.revalidated": {"id", "tasks", "revision"},
    }[kind]
    if not required <= p.keys():
        raise ValueError("missing payload fields for " + kind)
    if kind == "delivery.updated" and "projection_version" in p:
        from harness.execution_publication_contract import validate_delivery

        validate_delivery(p, value["binding"])
    if kind.startswith(("agent.", "task.")):
        identity(p["id"])
        if p["status"] not in STATUSES:
            raise ValueError("invalid activity status")
        progress(p.get("progress"))
    if kind.startswith("task."):
        integer(p["revision"], "task revision", 1)
    if kind == "task.registered":
        text(p["title"], "task title")
        paths(p["files"])
        string_list(p["dependencies"], "task dependencies", ids=True)
        string_list(p["criteria"], "task criteria", nonempty=True)
    if kind == "agent.registered":
        text(p["name"], "agent name")
        text(p["role"], "agent role")
        if p.get("task") is not None:
            identity(p["task"])
    if kind in {"dependency.changed", "ownership.changed"}:
        identity(p["task"])
        if kind == "dependency.changed":
            string_list(p["dependencies"], "task dependencies", ids=True)
        else:
            paths(p["files"])
    if kind == "blocker.raised":
        identity(p["id"])
        integer(p["revision"], "blocker revision", 1)
        string_list(p["tasks"], "blocker tasks", ids=True, nonempty=True)
        string_list(p["alternatives"], "blocker alternatives")
        for key in ("question", "context", "recommendation"):
            text(p[key], key)
    if kind == "handoff.created":
        identity(p["from"])
        identity(p["to"])
        text(p["text"], "handoff text")
    if kind.startswith("test."):
        identity(p["id"])
        text(p["area"], "test area")
        string_list(p["scope"], "test scope", nonempty=True)
        if p["status"] not in {"RUNNING", "PASS", "FAIL", "EMPTY", "INFRA", "TORN"}:
            raise ValueError("invalid test execution status")
    if kind == "decision.acknowledged":
        identity(p["delivery_id"])
        identity(p["blocker_id"])
        text(p["decision_hash"], "decision hash")
    if kind == "blocker.resolved":
        identity(p["id"])
        identity(p["delivery_id"])
    if len(canonical(value).encode()) > MAX_RECORD:
        raise ValueError("event exceeds record limit")
    return value


def validate_delivery(value):
    bounded(value)
    required = {
        "schema_version",
        "id",
        "binding",
        "blocker_id",
        "blocker_revision",
        "verdict",
        "text",
        "tasks",
        "criteria",
        "amendment",
        "registry",
        "base_revision",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or type(value["schema_version"]) is not int
        or value["schema_version"] != VERSION
    ):
        raise ValueError("invalid decision delivery")
    validate_binding(value["binding"])
    identity(value["id"])
    identity(value["blocker_id"])
    integer(value["blocker_revision"], "blocker revision", 1)
    if (
        value["verdict"] not in {"accept", "reject", "modify", "alternative"}
        or type(value["amendment"]) is not bool
    ):
        raise ValueError("invalid developer verdict")
    text(value["text"], "developer decision")
    if (
        not isinstance(value["tasks"], list)
        or not value["tasks"]
        or not isinstance(value["criteria"], list)
    ):
        raise ValueError("invalid developer decision scope")
    string_list(value["tasks"], "decision tasks", ids=True, nonempty=True)
    string_list(value["criteria"], "decision criteria")
    if not isinstance(value["registry"], dict) or set(value["registry"]) != {
        "id",
        "path",
        "sha256",
    }:
        raise ValueError("invalid Registry provenance")
    text(value["base_revision"], "base revision")
    for key in ("id", "path", "sha256"):
        text(value["registry"][key], "Registry " + key)
    if value["verdict"] == "reject" and value["amendment"]:
        raise ValueError("rejection cannot authorize an amendment")
    return value
