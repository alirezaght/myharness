"""Closed typed sections and scenario reference conservation."""

import al_proof_json as J
from al_schema import refuse


SECTIONS = {
    "initial_state": {"id", "description"},
    "transitions": {"id", "from_state", "action", "to_state"},
    "owners": {"id", "subject", "responsible_component_id"},
    "preserve": {"id", "subject", "expected_observation"},
    "reset": {"id", "subject", "expected_observation"},
    "journey_end": {"id", "subject", "expected_observation"},
    "persistence_observations": {"id", "subject", "expected_observation"},
    "operational_failures": {"id", "trigger", "expected_observation"},
    "required_real_components": {"id", "description", "boundary"},
    "permitted_simulated_boundaries": {"id", "description", "boundary"},
}
SCENARIO = {
    "id",
    "initial_state_ids",
    "transition_ids",
    "outcome_ids",
    "failure_case_ids",
    "real_component_ids",
    "permitted_simulated_component_ids",
}


def section(value, fields, name):
    J.closed(value, {"applicability", "reason", "items"}, name)
    J.text(value["reason"], name + " applicability reason")
    items = value["items"]
    if (
        value["applicability"] not in {"applicable", "not_applicable"}
        or not isinstance(items, list)
        or bool(items) != (value["applicability"] == "applicable")
    ):
        refuse("incomplete behavior section: " + name)
    ids = []
    for item in items:
        J.closed(item, fields, name + " item")
        for key, entry in item.items():
            J.text(entry, name + "." + key)
        ids.append(item["id"])
    if len(ids) != len(set(ids)):
        refuse("duplicate behavior section IDs: " + name)
    return {item["id"]: item for item in items}


def validate(group, *, required=True):
    sections = {name: section(group[name], fields, name) for name, fields in SECTIONS.items()}
    states = set(sections["initial_state"])
    real = set(sections["required_real_components"])
    simulated = set(sections["permitted_simulated_boundaries"])
    if real & simulated:
        refuse("real behavior component cannot be declared simulated")
    for transition in sections["transitions"].values():
        if transition["from_state"] not in states or transition["to_state"] not in states:
            refuse("behavior transition references unknown state")
    for owner in sections["owners"].values():
        if owner["responsible_component_id"] not in real | simulated:
            refuse("behavior owner references unknown responsible component")
    outcomes = set()
    for name in ("preserve", "reset", "journey_end", "persistence_observations"):
        if outcomes & sections[name].keys():
            refuse("ambiguous behavior outcome ID")
        outcomes.update(sections[name])
    expected = {
        "initial_state_ids": states,
        "transition_ids": set(sections["transitions"]),
        "outcome_ids": outcomes,
        "failure_case_ids": set(sections["operational_failures"]),
        "real_component_ids": real,
        "permitted_simulated_component_ids": simulated,
    }
    scenarios = group["scenarios"]
    if not isinstance(scenarios, list):
        refuse("invalid behavior scenarios")
    if not required and not scenarios:
        return set()
    if not scenarios or not outcomes:
        refuse("required behavior needs scenarios and observable outcomes")
    seen = set()
    covered = {key: set() for key in expected}
    for scenario in scenarios:
        J.closed(scenario, SCENARIO, "behavior scenario")
        identifier = J.text(scenario["id"], "scenario ID")
        if identifier in seen:
            refuse("duplicate behavior scenario ID")
        seen.add(identifier)
        for key, allowed in expected.items():
            values = set(J.strings(scenario[key], "scenario " + key))
            if not values <= allowed:
                refuse("scenario references unknown " + key)
            covered[key].update(values)
        if not scenario["outcome_ids"]:
            refuse("scenario omits observable outcome")
    for key in ("transition_ids", "outcome_ids", "failure_case_ids", "real_component_ids"):
        if covered[key] != expected[key]:
            refuse("behavior scenarios omit required " + key)
    used_states = set(covered["initial_state_ids"])
    for transition in sections["transitions"].values():
        used_states.update((transition["from_state"], transition["to_state"]))
    if used_states != states:
        refuse("behavior scenarios omit declared states")
    return seen
