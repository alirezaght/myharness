"""Validated preferences and server-observed bindings; neither grants authority."""

import json
from dataclasses import asdict, dataclass

from harness.knowledge.models import ContextRequest, QueryLimits, canonical, digest
from harness.knowledge.policy import validate_scope

STAGES = frozenset({"investigation", "planning", "implementation", "verification", "recovery"})


def preferences(request):
    if not isinstance(request, ContextRequest) or not isinstance(request.limits, QueryLimits):
        raise ValueError("invalid knowledge request")
    request.limits.validate()
    if type(request.request_version) is not int or request.request_version != 1:
        raise ValueError("unsupported knowledge request version")
    for text in (request.query, request.objective):
        if not isinstance(text, str) or len(text.encode()) > 4096 or "\0" in text:
            raise ValueError("knowledge request text exceeds supported bounds")
    for refs in (request.entity_refs, request.affected_entities):
        if (
            not isinstance(refs, tuple)
            or len(refs) > 40
            or any(
                not isinstance(ref, str) or not ref or len(ref.encode()) > 512 or "\0" in ref
                for ref in refs
            )
        ):
            raise ValueError("invalid knowledge entity preferences")
    if request.stage is not None and (
        not isinstance(request.stage, str) or request.stage not in STAGES
    ):
        raise ValueError("invalid knowledge stage")
    if request.estimated_tokens is not None and (
        type(request.estimated_tokens) is not int or not 1 <= request.estimated_tokens <= 32768
    ):
        raise ValueError("estimated token budget exceeds supported bounds")
    return {
        "version": request.request_version,
        "query": request.query,
        "entity_refs": list(request.entity_refs),
        "objective": request.objective,
        "stage": request.stage,
        "affected_entities": list(request.affected_entities),
        "estimated_tokens": request.estimated_tokens,
        "limits": asdict(request.limits),
        "as_of_snapshot": request.as_of_snapshot,
    }


def task_aware(request):
    return bool(
        request.objective or request.stage or request.affected_entities or request.estimated_tokens
    )


@dataclass(frozen=True)
class ContextBinding:
    """Canonical immutable bytes derived only after actual scope validation."""

    encoded: str

    @property
    def identity(self):
        return digest(self.encoded.encode())

    def to_dict(self):
        return json.loads(self.encoded)


def derive_binding(conn, scope, snapshot, reads, *, snapshot_observation=None):
    validate_scope(conn, scope)
    state = asdict(scope)
    state["repository"] = str(scope.repository.resolve())
    allowed = state.pop("permitted_source_versions")
    state["permitted_versions_digest"] = (
        digest(canonical(sorted(allowed)).encode()) if allowed is not None else None
    )
    restricted = scope.permitted_source_versions is not None
    state["snapshot_id"] = scope.snapshot_id if restricted else snapshot
    state["snapshot_revision"] = None
    state["source_versions"] = {"requirements": None, "design": None, "spec": None}
    state["source_version_state"] = "restricted-current-task-versions-unavailable"
    state["missing_versions"] = ["design", "requirements", "spec"]
    if restricted:
        return ContextBinding(canonical(state))
    from harness.knowledge.adapters import binding_record, context_versions

    meter = {"count": 0, "bytes": 0}
    supplied = (
        {"observation_json": snapshot_observation} if snapshot_observation is not None else None
    )
    value, reason = binding_record(
        conn,
        "knowledge_snapshots",
        {"observation_json": "observation_json"},
        "snapshot_id=? AND project_id=?",
        (snapshot, scope.project_id),
        reads,
        meter,
        supplied=supplied,
    )
    if reason:
        raise PermissionError("knowledge snapshot binding unavailable: " + reason)
    observation = json.loads(value["observation_json"])
    if not isinstance(observation, dict):
        raise PermissionError("knowledge snapshot binding malformed")
    state["snapshot_revision"] = observation.get("head")
    state["snapshot_observation_version"] = digest(canonical(value).encode())
    versions = context_versions(conn, scope, reads, meter)
    state["source_versions"] = versions
    state["source_version_state"] = "bounded-current-record-subset"
    state["requirements_unavailable"] = "registry-dependency-reads-not-bounded"
    state["missing_versions"] = sorted(kind for kind, value in versions.items() if value is None)
    return ContextBinding(canonical(state))


def candidate_sources(conn, request, cap):
    """Metadata-only entry points from the already established permitted partition."""
    from harness.knowledge.context_ranking import affected_paths, guidance_scope

    source_explicit, guidance = set(), set()
    wanted = {str(path) for path in affected_paths(request)}
    source_sql = (
        "SELECT DISTINCT a.assertion_id,s.locator FROM knowledge_assertions a "
        "JOIN temp.k_eligible k USING(assertion_id) JOIN knowledge_evidence e USING(assertion_id) "
        "JOIN knowledge_source_versions v USING(version_id) "
        "JOIN knowledge_sources s USING(source_id) "
    )
    if wanted:
        source_explicit.update(
            row[0]
            for row in conn.execute(
                source_sql + "WHERE s.locator IN (" + ",".join("?" for _ in wanted) + ") "
                "ORDER BY a.assertion_id LIMIT ?",
                (*sorted(wanted), cap),
            )
        )
    for row in conn.execute(
        source_sql + "WHERE s.locator='AGENTS.md' OR s.locator='CLAUDE.md' "
        "OR s.locator LIKE '%/AGENTS.md' OR s.locator LIKE '%/CLAUDE.md' "
        "ORDER BY a.assertion_id LIMIT ?",
        (cap,),
    ):
        if guidance_scope(row["locator"], request):
            guidance.add(row["assertion_id"])
    return source_explicit, guidance


def candidate_context(conn, rows, cap):
    """Bounded eligible provenance/state metadata for pure ranking, not approval grants."""
    located = {}
    if rows:
        for row in conn.execute(
            "SELECT e.assertion_id,s.locator FROM knowledge_evidence e "
            "JOIN temp.k_eligible k USING(assertion_id) JOIN knowledge_source_versions v "
            "USING(version_id) JOIN knowledge_sources s USING(source_id) "
            "WHERE e.assertion_id IN (" + ",".join("?" for _ in rows) + ") "
            "ORDER BY e.assertion_id,s.locator LIMIT ?",
            (*rows, cap * 40),
        ):
            located.setdefault(row["assertion_id"], set()).add(row["locator"])
        states = {}
        subjects = sorted({row["subject_id"] for row in rows.values()})
        for row in conn.execute(
            "SELECT a.subject_id,a.value_json,a.applicability_json FROM knowledge_assertions a "
            "JOIN temp.k_eligible k USING(assertion_id) WHERE a.predicate='status' "
            "AND a.subject_id IN (" + ",".join("?" for _ in subjects) + ") "
            "ORDER BY a.assertion_id LIMIT ?",
            (*subjects, cap),
        ):
            if not json.loads(row[2]).get("historical"):
                states.setdefault(row[0], set()).add(row[1])
        for row in rows.values():
            row["_approved_decision"] = (
                row["subject"].startswith("decision:")
                and not json.loads(row["applicability_json"]).get("historical")
                and states.get(row["subject_id"]) == {canonical("approved")}
            )
    return located
