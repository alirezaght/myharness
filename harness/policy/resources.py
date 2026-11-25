"Derive actual scopes from authenticated registry, admitted writers and parsed handlers."

import secrets

from harness.command_safety import classify_command
from harness.execution_contract import canonical
from harness.execution_files import parse_patch, relative_path
from harness.execution_store import get_execution
from harness.policy.admission import Attempt
from harness.policy.composition import inherited_roles, select
from harness.policy.contracts import OperationIntent, require
from harness.reliability.identity import verified_principal
from harness.reliability.process_context import CURRENT

CLEANUP = {"poll_process", "cancel_process", "await_assignment"}
READS = {"read_file", "knowledge_query", "knowledge_explain", "knowledge_consumers", "diagnostic"}


CONTROL_READS = {
    "harness:pending-work",
    "harness:tooling",
    "harness:capabilities",
    "harness:assignment",
    "harness:operational-issues",
    "harness:planning-adoptions",
    "harness:outputs",
    "harness:handoff",
    "harness:task-package",
    "harness:al-state",
}


def _control_read(path):
    from harness.assignment_handoff import RESOURCE

    skill = path in {"harness:skill", "harness:skill/index"} or path.startswith(
        "harness:skill/reference/"
    )
    require(not path.startswith("harness:skill") or skill)
    return (
        path in CONTROL_READS
        or skill
        or path == RESOURCE
        or path.startswith(RESOURCE + ":")
        or path.startswith("artifact:")
    )


def _file_resources(path):
    relative_path(path)
    return ("file:" + path,)


def prepare(operation, controller, principal, name, a, state):
    from pathlib import Path

    from harness.native_tool_policy import TOOLS

    operation._policy_repository = str(Path(operation.envelope["repository"]).resolve())
    execution = get_execution(controller.conn, operation.execution_id)
    if name not in TOOLS:
        raise ValueError("unknown operation")
    require(verified_principal(operation, principal) is not None, "AUTHORITY_STALE")
    docs, digest = select(
        controller.conn, execution, principal, roles=inherited_roles(operation, principal)
    )
    if name in CLEANUP:
        return None
    mutating = name not in READS
    task = None
    revision = None
    resources = ()
    retry = False
    retry_reference = None
    if name == "apply_patch":
        paths = [edit["path"] for edit in parse_patch(a["patch"])]
        operation.admitted(
            controller,
            principal,
            a["task_id"],
            a["expected_revision"],
            paths=paths,
            verify_outputs=False,
        )
        task, revision = a["task_id"], a["expected_revision"]
        resources = tuple("file:" + path for path in sorted(paths))
    elif name == "read_file":
        if a["path"] == "harness:repository-audit":
            resources = ("control:repository_audit",)
        elif a["path"] in {"harness:assignment", "harness:assignment-handoff"} or a[
            "path"
        ].startswith("harness:assignment-handoff:"):
            assignment = operation.assignments.get(principal["thread_id"])
            if assignment and principal.get("writable"):
                operation.admitted(
                    controller,
                    principal,
                    assignment["task"],
                    assignment["revision"],
                    verify_outputs=False,
                )
                resources = ("control:typed_assignment",)
        elif not _control_read(a["path"]):
            resources = _file_resources(a["path"])
    elif name == "diagnostic":
        request = a["request"]
        if (
            isinstance(request, dict)
            and set(request) <= {"kind", "path"}
            and request.get("kind") in {"source", "log", "config"}
        ):
            resources = _file_resources(request["path"])
        else:
            resources = ("control:diagnostic",)
    elif name == "submit_output" and a.get("contract") is not None:
        from harness.agent_contracts import validate
        from harness.agent_exchange import context, specification, task_for

        output_task = task_for(state, principal)
        validate(
            a["contract"],
            context=context(execution, state, principal, output_task),
            specification=specification(state),
        )
        resources = ("control:typed_output",)
    elif name == "run_command":
        decision = classify_command(
            {
                "argv": a["argv"],
                "cwd": str(__import__("pathlib").Path(operation.envelope["repository"]) / a["cwd"]),
            },
            {"repository": operation.envelope["repository"]},
        )
        mutating = decision["decision"] != "read_only"
        writable = ()
        if mutating:
            _, _, writable = operation.admitted(
                controller, principal, a["task_id"], a["expected_revision"], verify_outputs=False
            )
            task, revision = a["task_id"], a["expected_revision"]
        # Whole profile authority is the resource, never caller effects or a guessed target.
        resources = tuple("file:" + path for path in sorted(writable)) if mutating else ("file:.",)
    elif name == "al_command":
        from harness.execution_operations import read_only

        args = a.get("arguments", [])
        mutating = not read_only(args)
        retry = bool(args and args[0] == "retry")
        if retry and len(args) >= 2:
            failure = state.get("failure")
            checkpoint = failure.get("checkpoint") if isinstance(failure, dict) else None
            if isinstance(checkpoint, dict) and failure.get("agent") == args[1]:
                from harness.execution_contract import identity

                # Existing trusted checkpoint ID identifies this AL failure occurrence;
                # no new digest of its reason or invented native invocation is created.
                retry_reference = (
                    identity(state["run_id"]),
                    identity(operation.envelope["al_session_id"]),
                    identity(args[1]),
                    identity(checkpoint["id"]),
                )
        if principal["role"] != "orchestrator" and args[:2] == ["evidence", "run"]:
            assignment = operation.assignments.get(principal["thread_id"])
            require(assignment is not None, "AUTHORITY_STALE")
            task, revision = assignment["task"], assignment["revision"]
            operation.admitted(controller, principal, task, revision, verify_outputs=False)
        resources = (
            ("control:delegation",)
            if args[:2] == ["integration", "agent-register"]
            else ("control:al",)
        )
    elif name == "environment_operation":
        if a.get("operation") == "inspect":
            mutating = False
        else:
            from harness.task_environment import task_unit

            task_unit(operation, controller, state, principal, a, verify_outputs=False)
            task, revision = a["task_id"], a["expected_revision"]
        resources = ("control:environment",)
    elif name == "delivery_operation":
        mutating = a.get("operation") != "status"
        if mutating:
            require(principal["role"] == "orchestrator", "POLICY_DENIED")
        resources = ("control:delivery",)
    elif name == "diagram_operation":
        mutating = a.get("operation") != "inspect"
        resources = ("control:diagram",)
    else:
        resources = ("control:" + name,)
    if not hasattr(operation, "_policy_owner"):
        operation._policy_owner = object()
        operation._policy_supervisor = secrets.token_hex(16)
    boundary = (
        "unqualified_process_tree"
        if name in {"run_command", "al_command", "knowledge_consumers"}
        or name == "environment_operation"
        and mutating
        else "external_operation"
        if name == "delivery_operation" and mutating
        else "closed_handler"
    )
    liability = (
        "unresolved"
        if name in {"delivery_operation", "environment_operation"} and mutating
        else "none"
    )
    from harness.policy import claims, domains
    from harness.policy.resource_bindings import derive

    private_claims = derive(
        operation,
        principal,
        state,
        task_id=task,
        resources=resources,
        mutating=mutating,
        boundary=boundary,
    )
    enrolled = domains.affected(controller.conn, private_claims)
    immutable_exchange = bool(set(resources) & {"control:typed_assignment", "control:typed_output"})
    strict = any(c.get("purpose") == "mutable_dependency" for c in private_claims)
    legacy = not docs and not enrolled and not immutable_exchange and not strict
    # The live source/Git audit has no qualified coordinated execution boundary.
    require(
        legacy or resources != ("control:repository_audit",),
        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    )
    control = None
    finish = name == "al_command" and a.get("arguments", [])[:2] == ["writer", "finish"]
    if (retry or finish) and not legacy:
        from harness.policy.control_qualification import prepare as qualify

        require(principal["role"] == "orchestrator", "POLICY_DENIED")
        control = qualify(operation, a["arguments"], state)
        control.validate()
        boundary = control.method.replace("-", "_")
        if finish:
            mutating = False
            private_claims = (
                claims.claim(
                    "al-authority:"
                    + str(__import__("pathlib").Path(operation.initialization["source"]).parent),
                    ".",
                    "write",
                    execution=execution["id"],
                    principals=(principal["thread_id"],),
                ),
            )
    if legacy:
        claims.require_clear(controller.conn, private_claims)
    if legacy and boundary == "closed_handler" and not mutating and not task:
        return None
    source_support = None
    if strict:
        from harness.policy.source_dependencies import bind_protocol, prepare_support

        source_support = prepare_support(controller.conn)
        private_claims = bind_protocol(private_claims, source_support)
    context = CURRENT.get()
    intent = OperationIntent(
        secrets.token_hex(16),
        execution["id"],
        execution["epoch_id"],
        execution["authority_generation"],
        principal["thread_id"],
        principal["role"],
        task,
        revision,
        name,
        resources,
        mutating,
        retry,
        canonical(execution["binding"]),
        context.observation_id if context else None,
        boundary,
        liability,
        retry_reference,
        private_claims,
    )
    requested = a.get("timeout", 900 if name == "al_command" else 300)
    require(type(requested) is int and requested > 0)
    attempt = Attempt(operation, principal, intent, digest, requested, legacy=legacy)
    attempt.control = control
    if strict:
        attempt.source_support = source_support
    attempt.versions = tuple(sorted(doc["policy_id"] + "-v" + str(doc["version"]) for doc in docs))
    attempt.admit()
    if task and principal["role"] != "orchestrator":
        try:
            with __import__("harness.policy.context", fromlist=["scope"]).scope(attempt):
                operation.admitted(controller, principal, task, revision)
        except BaseException:
            attempt.release_unstarted()
            raise
    return attempt
