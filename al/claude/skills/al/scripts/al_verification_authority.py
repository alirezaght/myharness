"""Reconstruct admitted normative authority independently from presentation."""

import al_artifacts as A
import al_proof_json as J
import al_requirement_bindings as Q
from al_schema import refuse


FIELDS = {
    "schema_version",
    "kind",
    "repository_identity",
    "session_id",
    "run_id",
    "original_task_package",
    "substantive_amendments",
    "requirement_lineage_hash",
    "criteria",
    "constraints",
    "invariants",
    "behavior_obligations",
    "verification_policy",
    "invalidation_generation",
}


def document_binding(st):
    from al_task_editorial import document_binding as binding

    return binding(st)


def expected(st, obligations, policy_ref):
    from al_new_origin import policy

    if J.read(policy_ref) != policy():
        refuse("authority verifier policy artifact differs from current pinned policy")
    behavior = J.read(obligations)
    return {
        "schema_version": 1,
        "kind": "verification-authority-v1",
        "repository_identity": st["identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
        "original_task_package": st["task_package"],
        "substantive_amendments": [entry["artifact"] for entry in st["amendments"]],
        "requirement_lineage_hash": Q.lineage(st),
        "criteria": behavior["criteria_registry"],
        "constraints": Q.registry(st, "constraints"),
        "invariants": Q.registry(st, "invariants"),
        "behavior_obligations": obligations,
        "verification_policy": dict(policy(), artifact_refs=[policy_ref]),
        "invalidation_generation": st["proof_admission"]["invalidation_generation"],
    }


def publish(st, sid, obligations):
    from al_new_origin import policy

    policy_ref = J.snapshot(sid, policy(), "verification-policy-v1")
    value = expected(st, obligations, policy_ref)
    return J.snapshot(sid, value, "verification-authority-v1")


def authority_binding(st):
    if st.get("proof_admission", {}).get("status") != "admitted":
        return None
    from al_planning import validate_admitted

    validate_admitted(st)
    reference = st["proof_admission"]["authority"]
    value = J.read(reference)
    J.closed(value, FIELDS, "verification authority")
    policy = value["verification_policy"]
    J.closed(policy, {"id", "version", "digest", "artifact_refs"}, "verification authority policy")
    if not isinstance(policy["artifact_refs"], list) or len(policy["artifact_refs"]) != 1:
        refuse("unsupported authority policy reference closure")
    if J.raw(value) != J.raw(
        expected(st, value["behavior_obligations"], policy["artifact_refs"][0])
    ):
        refuse("verification authority differs from current independent reconstruction")
    return {"hash": A.digest(J.raw(value)), "artifact": reference, "value": value}
