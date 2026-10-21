"""Task-owned factual observations; structural coverage is not semantic approval."""

from pathlib import Path

import al_proof_json as J
import al_behavior_observation_items as Items
import al_scope as S
import al_store
from al_schema import refuse

FIELDS = {
    "schema_version",
    "kind",
    "obligation_id",
    "obligation_revision",
    "scenario_id",
    "evidence_ids",
    "binding",
    "starting_state",
    "real_components",
    "simulated_dependencies",
    "exercised_transitions",
    "observed_outcomes",
    "operational_failures",
    "assertion_locations",
    "artifact_refs",
    "limitations",
}
BINDING = {
    "repository_identity",
    "session_id",
    "run_id",
    "verification_authority_hash",
    "document_hash",
    "candidate_fingerprint",
    "input_bindings",
}


def current(st):
    from al_verification_authority import authority_binding

    authority = authority_binding(st)
    if authority is None:
        refuse("behavior observations require admitted task-owned obligations")
    reference = authority["value"]["behavior_obligations"]
    return authority, reference, J.read(reference)


def validate(st, observation, obligations=None):
    from al_task_editorial import editorial_bridge

    authority, reference, actual = current(st)
    if obligations is not None and obligations != actual:
        refuse("foreign behavior obligations")
    J.closed(observation, FIELDS, "behavior observation")
    if (
        type(observation["schema_version"]) is not int
        or observation["schema_version"] != 1
        or observation["kind"] != "behavior-observation-v1"
    ):
        refuse("unsupported behavior observation version")
    if observation["obligation_revision"] != {
        "artifact": reference,
        "revision": actual["revision"],
    }:
        refuse("behavior observation references a foreign obligation revision")
    J.text(observation["obligation_id"], "observed obligation ID")
    J.text(observation["scenario_id"], "observed scenario ID")
    groups = {group["id"]: group for group in actual["obligations"]}
    group = groups.get(observation["obligation_id"])
    if not group or group["applicability"]["status"] != "required":
        refuse("observation needs a required current obligation")
    scenarios = {value["id"]: value for value in group["scenarios"]}
    scenario = scenarios.get(observation["scenario_id"])
    if scenario is None:
        refuse("observation references an unknown required scenario")
    binding = J.closed(observation["binding"], BINDING, "behavior observation binding")
    expected = {
        "repository_identity": st["identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
        "verification_authority_hash": authority["hash"],
        "candidate_fingerprint": S.capture_candidate(st)["fingerprint"],
    }
    if any(binding[key] != value for key, value in expected.items()):
        refuse("behavior observation candidate/run/authority differs")
    from al_verification_authority import document_binding

    editorial_bridge(st, binding["document_hash"], document_binding(st)["hash"], authority["hash"])
    Items.references(binding["input_bindings"])
    if (
        authority["artifact"] not in binding["input_bindings"]
        or reference not in binding["input_bindings"]
    ):
        refuse("behavior input bindings omit authority or obligation bytes")
    Items.evidence(st, observation["evidence_ids"])
    required = set().union(
        *(
            set(scenario[key])
            for key in (
                "initial_state_ids",
                "transition_ids",
                "outcome_ids",
                "failure_case_ids",
                "real_component_ids",
            )
        )
    )
    assertions = Items.assertions(
        st, observation["assertion_locations"], required, observation["evidence_ids"]
    )
    for field, key, identifier, fields in (
        ("starting_state", "initial_state_ids", "state_id", {"observation", "artifact_refs"}),
        (
            "real_components",
            "real_component_ids",
            "component_id",
            {"evidence_ids", "artifact_refs"},
        ),
        (
            "exercised_transitions",
            "transition_ids",
            "transition_id",
            {"evidence_ids", "artifact_refs"},
        ),
        (
            "observed_outcomes",
            "outcome_ids",
            "outcome_id",
            {"observation", "evidence_ids", "artifact_refs"},
        ),
        (
            "operational_failures",
            "failure_case_ids",
            "failure_case_id",
            {"observation", "evidence_ids", "artifact_refs"},
        ),
    ):
        Items.rows(
            st,
            observation[field],
            set(scenario[key]),
            identifier,
            fields,
            observation["evidence_ids"],
            assertions,
        )
    simulated = observation["simulated_dependencies"]
    if not isinstance(simulated, list):
        refuse("invalid simulated behavior dependencies")
    ids = []
    for row in simulated:
        J.closed(row, {"component_id", "mechanism", "limitations"}, "simulated behavior dependency")
        J.text(row["mechanism"], "simulation mechanism")
        J.text(row["limitations"], "simulation limitations")
        ids.append(row["component_id"])
    if (
        any(not isinstance(identifier, str) for identifier in ids)
        or len(ids) != len(set(ids))
        or set(ids) != set(scenario["permitted_simulated_component_ids"])
    ):
        refuse("unknown, duplicate or required-real simulated dependency")
    Items.references(observation["artifact_refs"])
    J.strings(observation["limitations"], "behavior result limitations")
    return observation


def validate_observation(st, observation, obligations=None):
    try:
        validate(st, observation, obligations)
    except (OSError, ValueError, KeyError, TypeError, SystemExit) as exc:
        return ["invalid behavior observation: " + str(exc)]
    return []


def behavior_gaps(st):
    if "proof_admission" not in st:
        return []
    if st["proof_admission"]["status"] != "admitted":
        return ["task-owned behavior obligations are not admitted"]
    _, _, obligations = current(st)
    required = {
        (group["id"], scenario["id"])
        for group in obligations["obligations"]
        if group["applicability"]["status"] == "required"
        for scenario in group["scenarios"]
    }
    seen = set()
    references = st.get("behavior_observations", [])
    if not isinstance(references, list):
        return ["invalid behavior observation registry"]
    for reference in references:
        try:
            if reference.get("kind") != "behavior-observation-v1":
                return ["unknown behavior observation artifact kind"]
            value = J.read(reference)
            if not validate_observation(st, value):
                seen.add((value["obligation_id"], value["scenario_id"]))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, SystemExit):
            return ["corrupt behavior observation history"]
    return [
        "missing current behavior scenario: " + group + "/" + scenario
        for group, scenario in sorted(required - seen)
    ]


def behavior_packet(st):
    from al_verification_authority import document_binding

    authority, reference, obligations = current(st)
    observations = []
    for ref in st.get("behavior_observations", []):
        value = J.read(ref)
        if not validate_observation(st, value):
            observations.append({"artifact": ref, "content": value})
    return {
        "verification_authority": {"hash": authority["hash"], "artifact": authority["artifact"]},
        "obligations": {"artifact": reference, "content": obligations},
        "document_hash": document_binding(st)["hash"],
        "candidate_fingerprint": S.capture_candidate(st)["fingerprint"],
        "observations": observations,
        "gaps": behavior_gaps(st),
    }


def cmd_observe(a, sid):
    st = al_store.load(sid)
    if st["status"] != "RUNNING":
        refuse("behavior observations require a running run")
    candidate = S.capture_candidate(st)
    observation = J.loads(Path(a.file).read_bytes())
    validate(st, observation)
    reference = J.snapshot(sid, observation, "behavior-observation-v1", a.file)
    if S.capture_candidate(st) != candidate:
        refuse("candidate moved during behavior observation publication")
    validate(st, observation)
    history = st.setdefault("behavior_observations", [])
    if any(ref["sha256"] == reference["sha256"] for ref in history):
        refuse("duplicate immutable behavior observation")
    history.append(reference)
    al_store.save(sid, st)
    print(J.raw({"artifact": reference, "gaps": behavior_gaps(st)}).decode())


def cmd_packet(a, sid):
    st = al_store.load(sid)
    before = S.capture_candidate(st)
    result = behavior_packet(st)
    if S.capture_candidate(st) != before:
        refuse("candidate moved during behavior packet observation")
    print(J.raw(result).decode())


def register(sub):
    children = sub.add_parser("behavior").add_subparsers(dest="action", required=True)
    p = children.add_parser("packet")
    p.set_defaults(fn=cmd_packet)
    p = children.add_parser("observe")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_observe)
