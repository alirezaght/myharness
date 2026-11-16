"""Scope validation before any graph selection, counts, ranking or cached reads."""

from dataclasses import asdict

from harness.knowledge.models import KnowledgeScope, canonical, digest, identity

_PURPOSES = {"operator", "investigation", "implementation", "fresh-review", "comparison-review"}


def operator_scope(repository, *, task_id=None, purpose="operator"):
    root = repository.resolve()
    return KnowledgeScope(identity(str(root)), root, "local-operator", purpose, task_id=task_id)


def validate_scope(conn, scope):
    if scope.purpose not in _PURPOSES or not scope.principal_id:
        raise PermissionError("unissued knowledge purpose")
    if scope.purpose in {"fresh-review", "comparison-review"} and (
        not scope.packet_hash or scope.permitted_source_versions is None
    ):
        raise PermissionError("review requires an explicit packet-local source binding")
    project = conn.execute(
        "SELECT * FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    if not project or project["repository_identity"] != str(scope.repository.resolve()):
        raise PermissionError("knowledge project mismatch")
    if project["policy_generation"] != scope.policy_generation:
        raise PermissionError("knowledge policy revoked or stale")
    if scope.execution_id:
        execution = conn.execute(
            "SELECT * FROM executions WHERE id=?", (scope.execution_id,)
        ).fetchone()
        if not execution or (
            execution["task_id"],
            execution["epoch_id"],
            execution["authority_generation"],
        ) != (scope.task_id, scope.epoch_id, scope.authority_generation):
            raise PermissionError("knowledge execution scope is stale")
    return project


def policy_digest(scope):
    data = asdict(scope)
    data["repository"] = str(scope.repository.resolve())
    allowed = scope.permitted_source_versions
    data["permitted_source_versions"] = sorted(allowed) if allowed is not None else None
    return digest(canonical(data).encode())


def derive_scope(controller, execution, principal, state, repository):
    """Called only after OperationController's live-principal/authority admission.

    Review roles remain closed until the supervisor issues packet-local source bindings.
    They cannot elect their own purpose or allowlist through tool arguments.
    """
    if principal["role"] != "orchestrator" and not principal.get("writable"):
        raise PermissionError("packet-local knowledge binding required for read-only agent")
    root = repository.resolve()
    project_id = identity(str(root))
    project = controller.conn.execute(
        "SELECT policy_generation FROM knowledge_projects WHERE project_id=?", (project_id,)
    ).fetchone()
    if not project:
        raise ValueError("knowledge not indexed; run native investigation first")
    return KnowledgeScope(
        project_id,
        root,
        principal["thread_id"],
        "implementation",
        task_id=execution["task_id"],
        execution_id=execution["id"],
        epoch_id=execution["epoch_id"],
        authority_generation=execution["authority_generation"],
        policy_generation=project["policy_generation"],
        role=principal["role"],
    )
