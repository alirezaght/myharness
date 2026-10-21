"""Bound next-action data never waives the underlying command guards."""

from pathlib import Path

import al_proof_json as J
import al_scope as S
import al_store
import al_task
import al_recovery_subjects as Subjects
from al_schema import refuse

FIELDS = {"schema_version", "kind", "code", "subject", "reason", "binding", "recovery"}
BINDING = {
    "repository_identity",
    "session_id",
    "run_id",
    "document_hash",
    "verification_authority_hash",
    "manifest_hash",
    "subject_observation_hash",
}
COMMANDS = {
    "gates.run": ("gates", "run", {"stage", "id", "resume", "retry_failed", "timeout"}),
    "evidence.run": (
        "evidence",
        "run",
        {
            "area",
            "kind",
            "tier",
            "cwd",
            "scope",
            "sources_file",
            "env",
            "preflight",
            "needs",
            "fresh",
            "timeout",
            "tail",
        },
    ),
    "planning.packet": ("planning", "packet", {"replace_unavailable_attempt"}),
    "planning.admit": ("planning", "admit", {"file"}),
    "review.packet": ("review-packet", None, {"stage", "replace_unavailable_attempt", "output"}),
}


_active = None


def descriptors(st):
    from al_verification_authority import authority_binding

    authority = authority_binding(st)
    candidate = S.capture_candidate(st)["fingerprint"]
    common = {
        "repository_identity": st["identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
        "document_hash": al_task.task_hash(st),
        "verification_authority_hash": authority["hash"] if authority else None,
        "manifest_hash": st["gate_manifests"][-1]["sha256"] if st.get("gate_manifests") else None,
    }
    result = []
    for issue in Subjects.issues(st):
        observation = issue.pop("observation")
        binding = dict(
            common,
            subject_observation_hash=J.digest(
                {
                    "candidate": candidate,
                    "status": st["status"],
                    "phase": st["phase"],
                    "subject": observation,
                }
            ),
        )
        result.append(
            dict(schema_version=1, kind="recovery-descriptor-v1", **issue, binding=binding)
        )
    return result


def descriptor(st, code, subject, reason=None, context=None):
    matches = [
        value for value in descriptors(st) if value["code"] == code and value["subject"] == subject
    ]
    if len(matches) != 1:
        refuse("recovery subject is not a unique current blocker")
    value = matches[0]
    if reason is not None and value["reason"] != reason:
        refuse("recovery reason differs from current guard observation")
    if context is not None and context != value["binding"]:
        refuse("recovery context differs from current guard binding")
    return value


def validate_recovery_binding(st, value):
    try:
        J.closed(value, FIELDS, "recovery descriptor")
        J.closed(value["binding"], BINDING, "recovery binding")
        J.closed(value["subject"], {"kind", "id"}, "recovery subject")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["kind"] != "recovery-descriptor-v1"
        ):
            return ["unsupported recovery descriptor"]
        current = descriptor(st, value["code"], value["subject"])
        if J.raw(value) != J.raw(current):
            return ["recovery descriptor is stale or altered"]
    except (OSError, ValueError, TypeError, KeyError, SystemExit) as exc:
        return ["invalid recovery descriptor: " + str(exc)]
    return []


def require_invocation(st, value, arguments):
    gaps = validate_recovery_binding(st, value)
    if gaps:
        refuse("; ".join(gaps))
    recovery = value["recovery"]
    if recovery["kind"] != "public_operation" or recovery["command"] not in COMMANDS:
        refuse("recovery requires the named input; no executable action is authorized")
    if getattr(arguments, "command_argv", []):
        refuse("recovery descriptor cannot authorize additional executable argv")
    command, action, selectors = COMMANDS[recovery["command"]]
    if (
        arguments.cmd != command
        or action is not None
        and getattr(arguments, "action", None) != action
        or set(recovery["arguments"]) != selectors
        or any(
            getattr(arguments, key, None) != expected
            for key, expected in recovery["arguments"].items()
        )
    ):
        refuse("recovery descriptor does not match this exact public operation")
    # Every normal parser, phase, reservation, ownership and readiness guard
    # still runs after this binding check.


def reset():
    global _active
    _active = None


def activate(st, value, arguments):
    global _active
    require_invocation(st, value, arguments)
    _active = value


def check_active(st):
    if _active is not None:
        gaps = validate_recovery_binding(st, _active)
        if gaps:
            refuse("; ".join(gaps))


def verify_commit(previous):
    if _active is not None:
        if previous is None:
            refuse("recovery cannot create an unbound run")
        check_active(previous)
        reset()


def refuse_guided(st, message):
    offered = descriptors(st)
    if not offered:
        refuse(message)
    refuse(message + "\nAL_RECOVERY " + J.raw(offered[0]).decode())


def cmd_recovery(a, sid):
    st = al_store.load(sid)
    if a.action == "validate":
        value = J.loads(Path(a.file).read_bytes())
        gaps = validate_recovery_binding(st, value)
        if gaps:
            refuse("; ".join(gaps))
        print(J.raw({"current": True, "descriptor": value}).decode())
        return
    print(
        J.raw(
            {"schema_version": 1, "kind": "recovery-guidance-v1", "descriptors": descriptors(st)}
        ).decode()
    )


def register(sub):
    children = sub.add_parser("recovery").add_subparsers(dest="action", required=True)
    children.add_parser("next").set_defaults(fn=cmd_recovery)
    p = children.add_parser("validate")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_recovery)
