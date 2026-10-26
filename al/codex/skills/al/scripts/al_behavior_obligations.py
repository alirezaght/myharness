"""Authored observable obligations with exact normative and scenario bindings."""

import al_behavior_sections as S
import al_proof_json as J
import al_requirement_bindings as Q
from al_schema import refuse, valid_id


TOP = {
    "schema_version",
    "kind",
    "id",
    "revision",
    "supersedes",
    "binding",
    "authorship",
    "criteria_registry",
    "implementation_context",
    "obligations",
}
GROUP = {
    "id",
    "criterion_ids",
    "requirement_refs",
    "observable_outcome",
    "applicability",
    *S.SECTIONS,
    "consumer_inventory",
    "scenarios",
}


def binding(st, document_hash=None):
    from al_verification_authority import document_binding

    return {
        "repository_identity": st["identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
        "requirement_lineage_hash": Q.lineage(st),
        "document_hash_at_authoring": document_binding(st)["hash"] if document_hash is None else document_hash,
    }


def consumers(value):
    J.closed(
        value,
        {"applicability", "reason", "entries", "discovery_refs", "uncertainties"},
        "behavior consumer inventory",
    )
    J.text(value["reason"], "consumer discovery reason")
    entries = value["entries"]
    if (
        value["applicability"] not in {"applicable", "not_applicable"}
        or not isinstance(entries, list)
        or bool(entries) != (value["applicability"] == "applicable")
    ):
        refuse("incomplete consumer inventory")
    if not isinstance(value["discovery_refs"], list):
        refuse("invalid consumer discovery references")
    for ref in value["discovery_refs"]:
        J.reference(ref)
    J.strings(value["uncertainties"], "consumer uncertainties")
    if value["uncertainties"]:
        refuse("unresolved behavior consumer discovery")
    seen = set()
    for entry in entries:
        J.closed(
            entry,
            {"id", "role", "kind", "contract", "locations", "source_refs"},
            "behavior consumer",
        )
        identifier = J.text(entry["id"], "consumer ID")
        if (
            identifier in seen
            or entry["role"] not in {"producer", "consumer"}
            or entry["kind"] not in {"code", "link", "message", "export", "other"}
        ):
            refuse("duplicate or invalid behavior consumer")
        seen.add(identifier)
        J.text(entry["contract"], "consumer contract")
        J.strings(entry["locations"], "consumer source locations", nonempty=True)
        if not isinstance(entry["source_refs"], list):
            refuse("invalid consumer source references")
        for ref in entry["source_refs"]:
            J.reference(ref)


def validate_obligations(st, artifact, *, report_ref, planning_occurrence, observation_ref, document_hash=None):
    J.closed(artifact, TOP, "behavior obligations")
    if (
        type(artifact["schema_version"]) is not int
        or artifact["schema_version"] != 1
        or artifact["kind"] != "behavior-obligations-v1"
        or not valid_id(artifact["id"])
        or type(artifact["revision"]) is not int
        or artifact["revision"] < 1
        or artifact["binding"] != binding(st, document_hash)
    ):
        refuse("foreign or stale behavior obligation binding")
    if artifact["supersedes"] is not None:
        J.reference(artifact["supersedes"])
    J.closed(
        artifact["authorship"], {"role", "planning_occurrence", "report_ref"}, "planning authorship"
    )
    expected = {
        "role": "al-architect",
        "planning_occurrence": planning_occurrence,
        "report_ref": report_ref,
    }
    if type(artifact["authorship"]["planning_occurrence"]) is not int or J.raw(
        artifact["authorship"]
    ) != J.raw(expected):
        refuse("behavior authorship differs from issued Architect report")
    if report_ref is not None:
        J.reference(report_ref)
    context = J.closed(
        artifact["implementation_context"], {"status", "observation_ref"}, "implementation context"
    )
    if any(w.get('degraded') for w in st.get('writers', {}).values()) and context['status'] != 'partially_built':
        refuse('degraded tracked work requires partially_built implementation_context and the current issued observation_ref')
    if context["status"] == "partially_built":
        if context["observation_ref"] != observation_ref:
            refuse("partially built context lacks the issued current observation")
        J.reference(observation_ref)
    elif context != {"status": "not_started", "observation_ref": None}:
        refuse("invalid implementation context")
    registry = Q.criteria(st, artifact["criteria_registry"])
    groups = artifact["obligations"]
    if not isinstance(groups, list) or not groups:
        refuse("missing observable behavior obligations")
    covered, ids, scenarios = set(), set(), set()
    required = False
    for group in groups:
        J.closed(group, GROUP, "observable obligation")
        identifier = J.text(group["id"], "obligation ID")
        if identifier in ids:
            refuse("duplicate observable obligation ID")
        ids.add(identifier)
        criteria = J.strings(group["criterion_ids"], "obligation criteria", nonempty=True)
        if not set(criteria) <= registry.keys():
            refuse("obligation references unknown exact criterion")
        references = group["requirement_refs"]
        if not isinstance(references, list) or len(references) != len(criteria):
            refuse("obligation omits exact requirement references")
        if {J.digest(ref) for ref in references} != {J.digest(registry[name]) for name in criteria}:
            refuse("obligation requirement references differ from registry")
        covered.update(criteria)
        J.text(group["observable_outcome"], "observable outcome")
        applicability = J.closed(
            group["applicability"], {"status", "reason"}, "obligation applicability"
        )
        J.text(applicability["reason"], "obligation applicability reason")
        if applicability["status"] not in {"required", "not_applicable"}:
            refuse("unsupported obligation applicability")
        needed = applicability["status"] == "required"
        required |= needed
        scenario_ids = S.validate(group, required=needed)
        if scenarios & scenario_ids:
            refuse("scenario IDs must be unique across observable obligations")
        scenarios.update(scenario_ids)
        consumers(group["consumer_inventory"])
    if covered != set(registry) or not required:
        refuse("observable obligations omit required accepted criteria/outcomes")
    return artifact
