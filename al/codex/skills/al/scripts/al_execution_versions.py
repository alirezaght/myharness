"""Explicit proof versions; only document provenance may cross editorial edges."""

import copy

import al_verification_inputs as Inputs


V2_FIELDS = {
    "execution-projection": set(
        "schema_version kind repository_identity worktree_identity run_id invalidation_epoch runtime_binding verifier_policy_binding adapter_resource_binding execution_profile source_projection environment setup_binding dependency_lock_binding prerequisites isolation_policy generated_output_policy document_hash verification_authority".split()
    ),
    "verification-preparation": set(
        "schema_version kind repository_identity session_id run_id destination spec evidence_plan_digest runtime_binding command_profiles configuration_bindings dependency_closure setup_recipe executable_identities prerequisite_receipts environment_policy isolation_policy cache_policy expected_evidence_destinations limitations blockers document_hash verification_authority invalidation_epoch".split()
    ),
    "execution-receipt": set(
        "schema_version kind projection projection_digest original_evidence_id original_reservation before_observation after_observation process_ownership cleanup status exit_code counts log_path log_sha256 generated_output_disposition measured_durations execution_identity_digest".split()
    ),
    "execution-association": set(
        "schema_version kind receipt_ref repository_identity run_id destination reservation current_observation document_hash verification_authority editorial_lineage".split()
    ),
}


def epoch(st):
    evidence = st.get("evidence", {})
    return Inputs.digest(
        {
            "global": evidence.get("invalidated_at"),
            "scoped": evidence.get("scope_invalidations", []),
        }
    )


def enable(st, value):
    if st.get("proof_admission", {}).get("status") != "admitted":
        return value
    from al_verification_authority import authority_binding, document_binding

    authority = authority_binding(st)
    value = copy.deepcopy(value)
    stem = value["kind"].rsplit("-v", 1)[0]
    if stem not in {"execution-projection", "verification-preparation"}:
        raise ValueError("unsupported authority-enabled record")
    number = version(value, stem)
    value.update(
        schema_version=2,
        kind=value["kind"][:-1] + "2",
        document_hash=document_binding(st)["hash"],
        verification_authority={"hash": authority["hash"], "artifact": authority["artifact"]},
        invalidation_epoch=epoch(st),
    )
    if number == 1:
        value.pop("task_hash")
    return value


def version(value, stem):
    number = value.get("schema_version")
    if (
        type(number) is not int
        or number not in (1, 2)
        or value.get("kind") != stem + "-v" + str(number)
    ):
        raise ValueError("unsupported proof kind/version")
    if number == 2 and set(value) != V2_FIELDS[stem]:
        raise ValueError("invalid closed version-two proof fields")
    return number


def bridge(st, original, destination):
    from al_verification_authority import authority_binding
    from al_task_editorial import editorial_bridge

    authority = authority_binding(st)
    if (
        authority is None
        or original["verification_authority"] != destination["verification_authority"]
    ):
        raise ValueError("execution authority differs")
    expected = {"hash": authority["hash"], "artifact": authority["artifact"]}
    if original["verification_authority"] != expected or original["invalidation_epoch"] != epoch(
        st
    ):
        raise ValueError("execution authority/invalidation is not current")
    return editorial_bridge(
        st, original["document_hash"], destination["document_hash"], authority["hash"]
    )


def equivalent(st, original, current, stem):
    number = version(original, stem)
    if version(current, stem) != number:
        return False
    if number == 1:
        return original == current
    bridge(st, original, current)
    normalized = copy.deepcopy(original)
    normalized["document_hash"] = current["document_hash"]
    return normalized == current


def execution_identity(projection):
    value = copy.deepcopy(projection)
    if version(value, "execution-projection") == 2:
        if "task_hash" in value or not isinstance(value.get("document_hash"), str):
            raise ValueError("invalid version-two document provenance")
        value.pop("document_hash")
    return Inputs.digest(value)
