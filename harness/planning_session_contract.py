"""Closed cooperative planning protocol. Credentials never belong in public documents."""

import re
import uuid

from harness.authoring_contract import BASE_KEYS, canonical, exact, request_identity, text

PROTOCOL_VERSION = 1
MAX_WAIT_SECONDS = 25
LISTENING_LEASE_SECONDS = 60
BUSY_LEASE_SECONDS = 300
CLOCK_TOLERANCE_SECONDS = 5
MAX_EVENT_BATCH = 32
MAX_EVENT_BATCH_BYTES = 131072
OWNER_KEYS = {
    "schema_version",
    "project",
    "task",
    "attachment_id",
    "epoch",
    "request_id",
    "expected",
}
ERRORS = {
    "SESSION_INTERVIEW_PREREQUISITE": (
        "Run session prepare-interview from the owning agent before "
        "proposing an uncited clarification."
    ),
    "SESSION_PROTOCOL_UNSUPPORTED": "Upgrade Harness to the supported session protocol.",
    "SESSION_IDENTITY_UNAVAILABLE": (
        "Run this command inside the invoking Codex or locally loaded Claude session."
    ),
    "SESSION_CAPABILITY_INVALID": (
        "Use the original owner-only capability file, or explicitly attach again."
    ),
    "SESSION_OWNER_LOST": "Ownership changed. Stop this loop and inspect session status.",
    "SESSION_LEASE_EXPIRED": "Explicitly reattach from the invoking provider session.",
    "SESSION_CLOCK_UNAVAILABLE": (
        "Harness could not obtain a validated host boot identity "
        "needed to protect this session lease."
    ),
    "SESSION_CLOCK_UNCERTAIN": (
        "Clock continuity is uncertain. Explicitly reattach into a new epoch."
    ),
    "SESSION_BASE_STALE": "Reload the committed task and session base before publishing.",
    "SESSION_REQUEST_CONFLICT": "Use a new request ID for different content.",
    "SESSION_WAIT_ACTIVE": "An owned foreground wait is already active.",
    "SESSION_STANDALONE_UNSETTLED": "Cancel and actually settle standalone work before attaching.",
    "SESSION_OPERATION_INCOMPATIBLE": (
        "Use the attached session publication or explicitly switch mode; "
        "no second provider was started."
    ),
    "SESSION_UPGRADE_REQUIRED": "Upgrade Harness before writing this session-enabled database.",
}


class SessionError(ValueError):
    def __init__(self, code, *, current=None, reason=None):
        self.code = code
        self.current = current
        self.reason = reason
        message = ERRORS[code]
        if code == "SESSION_CLOCK_UNAVAILABLE":
            from harness.planning_session_clock import REASONS

            self.reason = reason if reason in REASONS else "read_failed"
            message += " Reason: " + self.reason + "."
            if self.reason == "permission_denied":
                message += (
                    " Ask the provider to run this same Harness command with host permission, "
                    "then retry the unchanged request with the same caller identity and capability."
                )
            else:
                message += " Resolve this host prerequisite before retrying the unchanged command."
        super().__init__(code + ": " + message)

    def public(self):
        result = {"error": str(self), "code": self.code, "current": self.current}
        if self.code == "SESSION_CLOCK_UNAVAILABLE":
            result["reason"] = self.reason
        return result


def identifier(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED") from None
    return value


def validate_request(document, extra=()):
    exact(document, OWNER_KEYS | set(extra))
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    if not isinstance(document["project"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", document["project"]
    ):
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    text(document["task"], "task", 100)
    identifier(document["attachment_id"])
    if type(document["epoch"]) is not int or document["epoch"] < 1:
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    request_identity(document["request_id"])
    exact(document["expected"], {"authoring", "input_revision"})
    exact(document["expected"]["authoring"], BASE_KEYS)
    if (
        document["expected"]["authoring"]["task"] != document["task"]
        or document["expected"]["authoring"]["project"] != document["project"]
    ):
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    if (
        type(document["expected"]["input_revision"]) is not int
        or document["expected"]["input_revision"] < 0
    ):
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    if len(canonical(document).encode()) > 1048576:
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    return document


def validate_publication(document):
    validate_request(document, {"consumes", "content"})
    events = document["consumes"]
    if (
        not isinstance(events, list)
        or len(events) > MAX_EVENT_BATCH
        or any(not isinstance(e, str) for e in events)
        or len(set(events)) != len(events)
    ):
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    for event in events:
        identifier(event)
    content = document["content"]
    if not isinstance(content, dict) or len(content) != 1:
        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
    schemas = {
        "task_message": {"text"},
        "progress": {"stage", "text"},
        "candidate": {"body", "graph"},
        "source_bundle": {"attached_envelope"},
        "question_proposal": {
            "concern",
            "text",
            "rationale",
            "citations",
            "supersedes",
            "changed_premise_reason",
        },
        "user_response": {"target", "target_revision", "mode", "text"},
    }
    kind = next(iter(content))
    if kind not in schemas:
        raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
    keys = schemas[kind]
    if kind == "user_response" and content[kind].get("mode") == "defer":
        keys = keys | {"source", "ref"}
    exact(content[kind], keys)
    if kind in {"task_message", "progress", "question_proposal", "user_response"}:
        text(content[kind]["text"], "text", 16000, empty=kind == "user_response")
    if kind == "progress":
        text(content[kind]["stage"], "stage", 100)
    return kind, content[kind]
