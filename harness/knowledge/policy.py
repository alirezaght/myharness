"""Server-issued purposes and packet-local bindings; context never grants authority."""

from dataclasses import asdict, replace

from harness.db import transaction, utcnow
from harness.knowledge.models import KnowledgeScope, canonical, digest, identity

_PURPOSES = {
    "operator",
    "investigation",
    "implementation",
    "planning",
    "fresh-review",
    "comparison-review",
}
_PLANNING = {
    "al-architect",
    "al-architect-fallback",
    "al-designer",
    "al-designer-fallback",
    "al-scout",
}
_ARCHITECTS = {"al-architect", "al-architect-fallback"}


def operator_scope(repository, *, task_id=None, purpose="operator"):
    root = repository.resolve()
    return KnowledgeScope(identity(str(root)), root, "local-operator", purpose, task_id=task_id)


def current_operator_scope(conn, repository, *, task_id=None, purpose="operator"):
    scope = operator_scope(repository, task_id=task_id, purpose=purpose)
    row = conn.execute(
        "SELECT policy_generation FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    return replace(scope, policy_generation=row[0] if row else 0)


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
        if scope.purpose in {"fresh-review", "comparison-review"}:
            projection = conn.execute(
                "SELECT * FROM knowledge_review_projections WHERE project_id=? AND packet_hash=?",
                (scope.project_id, scope.packet_hash),
            ).fetchone()
            if not projection or (
                projection["execution_id"],
                projection["review_id"],
                projection["snapshot_id"],
                projection["policy_generation"],
                projection["permitted_digest"],
            ) != (
                scope.execution_id,
                scope.review_id,
                scope.snapshot_id,
                scope.policy_generation,
                scope.permitted_digest,
            ):
                raise PermissionError(
                    "knowledge:clean-review-provenance-unavailable (review projection is stale)"
                )
            raise PermissionError("knowledge:clean-review-provenance-unavailable")
    return project


def policy_digest(scope):
    data = asdict(scope)
    data["repository"] = str(scope.repository.resolve())
    allowed = scope.permitted_source_versions
    data["permitted_source_versions"] = scope.permitted_digest or (
        sorted(allowed) if allowed is not None else None
    )
    return digest(canonical(data).encode())


def derive_scope(controller, execution, principal, state, repository, *, operation=None):
    root = repository.resolve()
    project_id = identity(str(root))
    project = controller.conn.execute(
        "SELECT * FROM knowledge_projects WHERE project_id=?", (project_id,)
    ).fetchone()
    if not project:
        raise PermissionError("knowledge:not-indexed")
    role = principal["role"]
    agent = state["integration"]["agents"].get(principal["thread_id"])
    if agent and (agent["role"] != role or agent["status"] in {"Completed", "Failed", "Stopped"}):
        raise PermissionError("knowledge:binding-missing")
    if (
        role == "orchestrator"
        or role in {"al-engineer", "al-builder"}
        and principal.get("writable")
    ):
        purpose = "implementation"
    elif role in _PLANNING and state["phase"] != "REVIEW":
        purpose = "planning"
    elif role in _ARCHITECTS and state["phase"] == "REVIEW":
        purpose = (
            "fresh-review"
            if (state.get("review") or {}).get("stage") == "fresh"
            else "comparison-review"
        )
    else:
        raise PermissionError("knowledge:binding-missing")
    scope = KnowledgeScope(
        project_id,
        root,
        principal["thread_id"],
        purpose,
        task_id=execution["task_id"],
        execution_id=execution["id"],
        epoch_id=execution["epoch_id"],
        authority_generation=execution["authority_generation"],
        policy_generation=project["policy_generation"],
        role=role,
    )
    if purpose in {"fresh-review", "comparison-review"}:
        if operation is None:
            raise PermissionError("knowledge:review-projection-unavailable")
        from harness.knowledge.review_projection import issued

        stage = "fresh" if purpose == "fresh-review" else "compare"
        issued(operation, execution, state, stage)
        raise PermissionError("knowledge:clean-review-provenance-unavailable")
    with transaction(controller.conn):
        previous = controller.conn.execute(
            "SELECT purpose,packet_hash FROM knowledge_scope_issues WHERE execution_id=? "
            "AND principal_id=?",
            (execution["id"], principal["thread_id"]),
        ).fetchall()
        if any(
            (purpose == "fresh-review" and (r[0] != purpose or r[1] != scope.packet_hash))
            or (purpose != "fresh-review" and r[0] == "fresh-review")
            for r in previous
        ):
            raise PermissionError("knowledge:review-principal-tainted")
        controller.conn.execute(
            "INSERT OR IGNORE INTO knowledge_scope_issues VALUES (?,?,?,?,?)",
            (execution["id"], principal["thread_id"], purpose, scope.packet_hash or "", utcnow()),
        )
    return scope
