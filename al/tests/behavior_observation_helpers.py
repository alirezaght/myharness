"""Real executable JSON journeys with model responses only at the provider boundary."""

import hashlib
import json
from al.tests.editorial_receipt_helpers import plan_fixture_gates, state, succeed
from al.tests.planning_helpers import prepare, save_report, section
from al.tests.runtime_run_helpers import invoke, public_run

TEST_SOURCE = """import json
from pathlib import Path
import subprocess
import sys


def test_fresh_process_preserves_and_reads_input():
    path = Path("rows.json")
    before = path.read_bytes()
    child = subprocess.run([sys.executable, "-I", "-m", "json.tool", str(path)], capture_output=True, check=True, timeout=5)
    assert json.loads(child.stdout) == {"rows": [1, 2]}
    assert path.read_bytes() == before


def test_malformed_input_is_rejected():
    before = Path("rows.json").read_bytes()
    try:
        json.loads("{")
    except ValueError:
        rejected = True
    else:
        rejected = False
    assert rejected
    assert Path("rows.json").read_bytes() == before
"""


def authored_json_report(report):
    group = report["authored_obligations"]["obligations"][0]
    group.update(
        id="json-journey",
        observable_outcome="A fresh parser reads persisted JSON and malformed input is rejected without changing the file.",
    )
    group["initial_state"] = section(
        [
            {"id": name, "description": description}
            for name, description in (
                ("ready", "Persisted JSON input exists"),
                ("parsed", "Fresh parser returned the expected value"),
                ("rejected", "Malformed JSON was refused"),
            )
        ],
        "Actual parser journey states.",
    )
    group["transitions"] = section(
        [
            {
                "id": "parse",
                "from_state": "ready",
                "action": "Read in a fresh owned parser process",
                "to_state": "parsed",
            },
            {
                "id": "reject",
                "from_state": "ready",
                "action": "Parse malformed JSON",
                "to_state": "rejected",
            },
        ],
        "Actual executable transitions.",
    )
    group["owners"]["items"][0].update(subject="JSON parsing", responsible_component_id="parser")
    group["preserve"] = section(
        [
            {
                "id": "unchanged",
                "subject": "persisted input",
                "expected_observation": "Input bytes remain unchanged.",
            }
        ],
        "Read-only journey.",
    )
    group["journey_end"] = section(
        [
            {
                "id": "expected-value",
                "subject": "parser result",
                "expected_observation": "Fresh process returns rows 1 and 2.",
            }
        ],
        "The process actually exits successfully.",
    )
    group["persistence_observations"] = section(
        [
            {
                "id": "persistent-input",
                "subject": "input file",
                "expected_observation": "The stored bytes remain readable after the parser exits.",
            }
        ],
        "Post-process persistence.",
    )
    group["operational_failures"] = section(
        [
            {
                "id": "bad-json",
                "trigger": "Malformed JSON",
                "expected_observation": "ValueError is observed and the stored file is unchanged.",
            }
        ],
        "Actual parser refusal.",
    )
    group["required_real_components"] = section(
        [
            {
                "id": "parser",
                "description": "Python JSON parser",
                "boundary": "Actual parser and fresh local subprocess; no parser result is mocked.",
            }
        ],
        "Real local execution.",
    )
    group["permitted_simulated_boundaries"] = section(
        [],
        "No application dependency is simulated; only the external Architect response is fixture input.",
    )
    group["scenarios"] = [
        {
            "id": "read-persisted",
            "initial_state_ids": ["ready"],
            "transition_ids": ["parse"],
            "outcome_ids": ["unchanged", "expected-value", "persistent-input"],
            "failure_case_ids": [],
            "real_component_ids": ["parser"],
            "permitted_simulated_component_ids": [],
        },
        {
            "id": "reject-malformed",
            "initial_state_ids": ["ready"],
            "transition_ids": ["reject"],
            "outcome_ids": ["unchanged"],
            "failure_case_ids": ["bad-json"],
            "real_component_ids": ["parser"],
            "permitted_simulated_component_ids": [],
        },
    ]
    return report


def prepare_behavior(launched, platform):
    _, _, repo, _, env = launched
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    (repo / "tests").mkdir()
    (repo / "tests/test_rows.py").write_text(TEST_SOURCE)
    (repo / "rows.json").write_text('{"rows": [1, 2]}\n')
    (repo / "Makefile").write_text("all:\n\ttrue\n")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname="json-journey-fixture"\nversion="0.1.0"\nrequires-python=">=3.13"\n[tool.al.architecture]\nforbidden=["deliberately_forbidden_fixture_dependency"]\n'
    )
    succeed(invoke(["git", "add", "."], repo, env))
    succeed(invoke(["git", "commit", "-qm", "observable parser journeys"], repo, env))
    _, report = prepare(
        launched,
        platform,
        "Verify persisted JSON through a fresh parser process and reject malformed input without changing stored bytes.",
    )
    succeed(save_report(launched, platform, authored_json_report(report)))
    succeed(public_run(launched, platform, "planning", "admit"))
    plan_fixture_gates(launched, platform, ["Makefile", "pyproject.toml", "tests", "rows.json"])
    succeed(
        public_run(
            launched,
            platform,
            "evidence",
            "plan",
            "--area",
            "parser-journeys",
            "--tier",
            "broad",
            "--scope",
            ".",
            "--env",
            "PATH,HOME,PYTEST_DISABLE_PLUGIN_AUTOLOAD,PYTEST_ADDOPTS,PYTEST_PLUGINS",
            "--",
            "python3",
            "-m",
            "pytest",
            "-q",
        )
    )
    succeed(public_run(launched, platform, "evidence", "run", "--area", "parser-journeys"))
    return json.loads(succeed(public_run(launched, platform, "behavior", "packet")).stdout)


def observation(launched, platform, packet, scenario_id):
    st = state(launched, platform)
    evidence = next(
        entry["id"]
        for entry in reversed(st["evidence"]["entries"])
        if entry["area"] == "parser-journeys"
    )
    artifact = packet["obligations"]
    group = artifact["content"]["obligations"][0]
    scenario = next(s for s in group["scenarios"] if s["id"] == scenario_id)
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
    source = (launched[2] / "tests/test_rows.py").read_bytes()

    def source_line(name):
        if scenario_id == "read-persisted":
            return 12 if name in {"ready", "unchanged", "persistent-input"} else 11
        return 24 if name in {"ready", "unchanged"} else 23

    def rows(key, identifier, *, text=False, executed=True):
        return [
            {
                identifier: name,
                **(
                    {
                        "observation": "The concrete source assertion passed in the linked real execution."
                    }
                    if text
                    else {}
                ),
                **({"evidence_ids": [evidence]} if executed else {}),
                "artifact_refs": [],
            }
            for name in scenario[key]
        ]

    return {
        "schema_version": 1,
        "kind": "behavior-observation-v1",
        "obligation_id": group["id"],
        "obligation_revision": {
            "artifact": artifact["artifact"],
            "revision": artifact["content"]["revision"],
        },
        "scenario_id": scenario_id,
        "evidence_ids": [evidence],
        "binding": {
            "repository_identity": st["identity"],
            "session_id": st["session_id"],
            "run_id": st["run_id"],
            "verification_authority_hash": packet["verification_authority"]["hash"],
            "document_hash": packet["document_hash"],
            "candidate_fingerprint": packet["candidate_fingerprint"],
            "input_bindings": [packet["verification_authority"]["artifact"], artifact["artifact"]],
        },
        "starting_state": rows("initial_state_ids", "state_id", text=True, executed=False),
        "real_components": rows("real_component_ids", "component_id"),
        "simulated_dependencies": [],
        "exercised_transitions": rows("transition_ids", "transition_id"),
        "observed_outcomes": rows("outcome_ids", "outcome_id", text=True),
        "operational_failures": rows("failure_case_ids", "failure_case_id", text=True),
        "assertion_locations": [
            {
                "scenario_item_id": name,
                "path": "tests/test_rows.py",
                "line": source_line(name),
                "source_sha256": hashlib.sha256(source).hexdigest(),
            }
            for name in sorted(required)
        ],
        "artifact_refs": [],
        "limitations": [],
    }


def publish(launched, platform, value):
    path = launched[2].parent / "observation.json"
    path.write_text(json.dumps(value))
    return public_run(launched, platform, "behavior", "observe", "--file", str(path))
