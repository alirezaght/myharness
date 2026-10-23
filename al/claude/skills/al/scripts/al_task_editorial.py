"""Closed presentation overlays; normative history and approval remain immutable."""

import copy
from pathlib import Path

import al_proof_json as J
import al_requirement_bindings as Q
from al_schema import refuse, valid_id

REQUEST = {
    "schema_version",
    "kind",
    "id",
    "base_document_hash",
    "verification_authority_hash",
    "changes",
    "source",
}
OVERLAY = {"schema_version", "kind", "request", "authority"}


def _authority(st, reference):
    from al_verification_authority import FIELDS, expected

    value = J.read(reference)
    J.closed(value, FIELDS, "editorial retained authority")
    if (
        value["repository_identity"] != st["identity"]
        or value["session_id"] != st["session_id"]
        or value["run_id"] != st["run_id"]
        or value["original_task_package"] != st["task_package"]
    ):
        refuse("foreign editorial authority")
    amendments = value["substantive_amendments"]
    current = [entry["artifact"] for entry in st["amendments"]]
    if not isinstance(amendments, list) or current[: len(amendments)] != amendments:
        refuse("editorial authority has no accepted substantive prefix")
    historical = copy.deepcopy(st)
    historical["amendments"] = historical["amendments"][: len(amendments)]
    generation = value["invalidation_generation"]
    if type(generation) is not int or generation < 0:
        refuse("invalid editorial authority generation")
    historical["proof_admission"]["invalidation_generation"] = generation
    policy = value["verification_policy"]
    J.closed(policy, {"id", "version", "digest", "artifact_refs"}, "editorial policy")
    if not isinstance(policy["artifact_refs"], list) or len(policy["artifact_refs"]) != 1:
        refuse("invalid editorial authority policy closure")
    if value != expected(historical, value["behavior_obligations"], policy["artifact_refs"][0]):
        refuse("editorial authority differs from accepted normative bytes")
    return value


def _changes(request, authority):
    J.closed(request, REQUEST, "editorial request")
    if (
        type(request["schema_version"]) is not int
        or request["schema_version"] != 1
        or request["kind"] != "task-editorial-revision-v1"
        or not valid_id(request["id"])
    ):
        refuse("invalid editorial request version/identity")
    J.text(request["source"], "editorial provenance")
    criteria = {entry["id"] for entry in authority["criteria"]}
    behavior = J.read(authority["behavior_obligations"])
    scenarios = {entry["id"] for group in behavior["obligations"] for entry in group["scenarios"]}
    if not isinstance(request["changes"], list) or not request["changes"]:
        refuse("editorial changes must be nonempty")
    seen = set()
    for change in request["changes"]:
        if not isinstance(change, dict):
            refuse("invalid editorial presentation change")
        field = change.get("field")
        if field in {"task_title", "display_summary"}:
            J.closed(change, {"field", "value"}, "editorial presentation change")
            identifier = None
        elif field in {"criterion_label", "scenario_label"}:
            J.closed(change, {"field", "id", "value"}, "editorial label")
            identifier = change["id"]
            if not isinstance(identifier, str) or identifier not in (
                criteria if field == "criterion_label" else scenarios
            ):
                refuse("editorial label references an unknown authoritative ID")
        else:
            refuse("unsupported editorial field; use a substantive amendment")
        if (
            not isinstance(change["value"], str)
            or field == "task_title"
            and not change["value"].strip()
        ):
            refuse("invalid editorial display value")
        key = (field, identifier)
        if key in seen:
            refuse("duplicate editorial presentation key")
        seen.add(key)


def _hash(lineage, references):
    if not references:
        return lineage
    return J.digest(
        {
            "kind": "task-document-v1",
            "requirement_lineage_hash": lineage,
            "editorial_overlays": [ref["sha256"] for ref in references],
        }
    )


def history(st):
    references = st.get("editorial_history", [])
    if not isinstance(references, list):
        refuse("invalid editorial history registry")
    prefix = []
    ids = set()
    edges = []
    for reference in references:
        J.reference(reference)
        if reference.get("kind") != "task-editorial-overlay-v1":
            refuse("unsupported editorial history artifact")
        overlay = J.read(reference)
        J.closed(overlay, OVERLAY, "editorial overlay")
        if (
            type(overlay["schema_version"]) is not int
            or overlay["schema_version"] != 1
            or overlay["kind"] != "task-editorial-overlay-v1"
        ):
            refuse("invalid editorial overlay version")
        authority = _authority(st, overlay["authority"])
        request = overlay["request"]
        _changes(request, authority)
        lineage = authority["requirement_lineage_hash"]
        before = _hash(lineage, prefix)
        authority_hash = J.digest(authority)
        if (
            request["base_document_hash"] != before
            or request["verification_authority_hash"] != authority_hash
            or request["id"] in ids
        ):
            refuse("broken, duplicate or foreign editorial lineage")
        ids.add(request["id"])
        prefix.append(reference)
        edges.append(
            {
                "from": before,
                "to": _hash(lineage, prefix),
                "authority": authority_hash,
                "reference": reference,
            }
        )
    return prefix, edges


def document_binding(st):
    references, _ = history(st)
    lineage = Q.lineage(st)
    return {"hash": _hash(lineage, references), "requirement_lineage_hash": lineage}


def require_document(st, value):
    references, edges = history(st)
    lineage = Q.lineage(st)
    allowed = {_hash(lineage, [])}
    for index, edge in enumerate(edges):
        authority = _authority(st, J.read(edge["reference"])["authority"])
        if authority["requirement_lineage_hash"] == lineage:
            allowed.update((edge["from"], edge["to"]))
    allowed.add(_hash(lineage, references))
    if value not in allowed:
        refuse("document does not belong to current normative history")
    return value


def validate_editorial(st, request):
    from al_verification_authority import authority_binding

    authority = authority_binding(st)
    if authority is None:
        refuse("editorial revisions require current admitted verification authority")
    _changes(request, authority["value"])
    references, _ = history(st)
    if any(J.read(ref)["request"]["id"] == request["id"] for ref in references):
        refuse("duplicate editorial revision ID")
    if (
        request["base_document_hash"] != document_binding(st)["hash"]
        or request["verification_authority_hash"] != authority["hash"]
    ):
        refuse("stale or foreign editorial document/authority binding")
    return {
        "schema_version": 1,
        "kind": "task-editorial-overlay-v1",
        "request": copy.deepcopy(request),
        "authority": authority["artifact"],
    }


def editorial_bridge(st, from_document, to_document, authority):
    from al_verification_authority import authority_binding

    current = authority_binding(st)
    if (
        current is None
        or authority != current["hash"]
    ):
        refuse("editorial bridge requires current document and authority")
    require_document(st, from_document)
    require_document(st, to_document)
    _, edges = history(st)
    cursor = from_document
    references = []
    for edge in edges:
        if cursor == to_document:
            break
        if edge["from"] != cursor:
            continue
        if edge["authority"] != authority:
            refuse("editorial bridge crosses changed verification authority")
        references.append(edge["reference"])
        cursor = edge["to"]
    if cursor != to_document:
        refuse("no complete editorial-only document bridge")
    return {
        "schema_version": 1,
        "kind": "editorial-lineage-v1",
        "from_document_hash": from_document,
        "to_document_hash": to_document,
        "verification_authority_hash": authority,
        "overlay_refs": references,
    }


def cmd_editorial(a, sid):
    import al_store
    from al_task_attribution import observe

    st = al_store.load(sid)
    if st["status"] != "RUNNING":
        refuse("editorial revision requires a running run")
    before = observe(st)
    request = J.loads(Path(a.file).read_bytes())
    overlay = validate_editorial(st, request)
    reference = J.snapshot(sid, overlay, "task-editorial-overlay-v1", a.file)
    validate_editorial(st, request)
    if observe(st, expected=before) != before:
        refuse("candidate moved during editorial publication")
    st.setdefault("editorial_history", []).append(reference)
    document = document_binding(st)
    al_store.save(sid, st)
    print(J.raw({"document": document, "overlay": reference}).decode())
