"""Only the external Architect response is supplied by this protocol fixture."""

import copy
import hashlib
import json

from al.tests.runtime_run_helpers import public_run


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def section(items, reason):
    return {
        "applicability": "applicable" if items else "not_applicable",
        "reason": reason,
        "items": items,
    }


def report_for(packet):
    value = packet["content"]
    reference = value["task_package"]
    package = json.loads(open(reference["stored_path"]).read())
    registry = [
        {
            "id": name,
            "artifact": reference,
            "pointer": "/criteria/" + str(index),
            "value_sha256": digest(name),
        }
        for index, name in enumerate(package["criteria"])
    ]
    group = {
        "id": "admission-protocol",
        "criterion_ids": package["criteria"],
        "requirement_refs": registry,
        "observable_outcome": "Current planning proof creates authority while task approval remains pending.",
        "applicability": {
            "status": "required",
            "reason": "This task requires observable planning authority.",
        },
        "initial_state": section(
            [
                {"id": "pending", "description": "No admitted authority"},
                {"id": "admitted", "description": "Current authority retained"},
            ],
            "Actual runtime states.",
        ),
        "transitions": section(
            [
                {
                    "id": "admit",
                    "from_state": "pending",
                    "action": "Validate current report and obligations",
                    "to_state": "admitted",
                }
            ],
            "Public admission transition.",
        ),
        "owners": section(
            [
                {
                    "id": "authority-owner",
                    "subject": "run proof",
                    "responsible_component_id": "runtime",
                }
            ],
            "Runtime owns admission.",
        ),
        "preserve": section(
            [
                {
                    "id": "pending-on-refusal",
                    "subject": "run state",
                    "expected_observation": "Malformed proof leaves state byte-identical.",
                }
            ],
            "Refusal is atomic.",
        ),
        "reset": section([], "No mutable application state is reset by admission."),
        "journey_end": section(
            [
                {
                    "id": "authority-present",
                    "subject": "run authority",
                    "expected_observation": "Authority is present without task approval.",
                }
            ],
            "Terminal state of this admission journey.",
        ),
        "persistence_observations": section(
            [
                {
                    "id": "retained-proof",
                    "subject": "immutable artifacts",
                    "expected_observation": "Stored report and materialized obligations retain independent hashes.",
                }
            ],
            "Artifacts remain readable.",
        ),
        "operational_failures": section(
            [
                {
                    "id": "bad-proof",
                    "trigger": "Foreign or malformed planning report",
                    "expected_observation": "Admission refuses without changing state.",
                }
            ],
            "Invalid provider input is supported.",
        ),
        "required_real_components": section(
            [
                {
                    "id": "runtime",
                    "description": "Managed AL runtime",
                    "boundary": "Real CLI, protected pin and immutable artifact admission",
                }
            ],
            "All runtime operations are real.",
        ),
        "permitted_simulated_boundaries": section(
            [
                {
                    "id": "provider",
                    "description": "Architect model response",
                    "boundary": "Only external model response is fixture data",
                }
            ],
            "No runtime approval or gate is mocked.",
        ),
        "consumer_inventory": {
            "applicability": "not_applicable",
            "reason": "No application export consumer is introduced by this standalone protocol fixture.",
            "entries": [],
            "discovery_refs": [],
            "uncertainties": [],
        },
        "scenarios": [
            {
                "id": "admission-success",
                "initial_state_ids": ["pending"],
                "transition_ids": ["admit"],
                "outcome_ids": ["authority-present", "retained-proof"],
                "failure_case_ids": [],
                "real_component_ids": ["runtime"],
                "permitted_simulated_component_ids": ["provider"],
            },
            {
                "id": "admission-refusal",
                "initial_state_ids": ["pending"],
                "transition_ids": [],
                "outcome_ids": ["pending-on-refusal"],
                "failure_case_ids": ["bad-proof"],
                "real_component_ids": ["runtime"],
                "permitted_simulated_component_ids": ["provider"],
            },
        ],
    }
    binding = value["binding"]
    behavior = {
        "schema_version": 1,
        "kind": "behavior-obligations-v1",
        "id": "observable-admission",
        "revision": 1,
        "supersedes": None,
        "binding": {
            key: binding[key]
            for key in ("repository_identity", "session_id", "run_id", "requirement_lineage_hash")
        },
        "authorship": {
            "role": "al-architect",
            "planning_occurrence": binding["planning_occurrence"],
            "report_ref": None,
        },
        "criteria_registry": registry,
        "implementation_context": {
            "status": "partially_built",
            "observation_ref": value["source_observation"],
        },
        "obligations": [group],
    }
    behavior["binding"]["document_hash_at_authoring"] = binding["document_hash"]
    attempt = value["attempt"]
    return {
        "schema_version": 1,
        "kind": "architect-planning-report-v1",
        "attempt_id": attempt["attempt_id"],
        "invocation_id": "fixture-external-architect-response",
        "model": attempt["model"],
        "effort": attempt["effort"],
        "policy_digest": attempt["policy_digest"],
        "packet_sha256": packet["packet"]["sha256"],
        "binding": copy.deepcopy(binding),
        "specification": {
            "status": "SPEC_READY",
            "reason": "The accepted protocol task has concrete observable outcomes.",
        },
        "routing": value["routing"],
        "authored_obligations": behavior,
    }


def prepare(
    launched, platform="codex", spec="Establish independently authored observable admission proof."
):
    for args in [
        ("start", "--spec", spec),
        ("spec-gate", "SPEC_READY"),
        ("classify", "SMALL", "--designer", "no", "--reason", "Bounded protocol task."),
    ]:
        result = public_run(launched, platform, *args)
        assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    result = public_run(launched, platform, "planning", "packet")
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    packet = json.loads(result.stdout)
    return packet, report_for(packet)


def save_report(launched, platform, report):
    path = launched[2].parent / "model-response.json"
    path.write_text(json.dumps(report))
    return public_run(launched, platform, "planning", "report", "--file", str(path))
