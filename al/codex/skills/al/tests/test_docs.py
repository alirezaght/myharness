#!/usr/bin/env python3
"""Keep the operational docs aligned with the guarded CLI and review packets."""
import re
import subprocess
import sys
from pathlib import Path

AL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AL / "tests"))
from test_resources import role_dir
ENTRY = (AL / "SKILL.md").read_text()
WORKFLOW = (AL / "reference" / "workflow.md").read_text()
SKILL = ENTRY + "\n" + WORKFLOW
REVIEW = (AL / "reference" / "review.md").read_text()
HANDOFFS = (AL / "reference" / "handoffs.md").read_text()
REPOSITORY_POLICY = (AL / "reference" / "repository-policy.md").read_text()


def test_review_docs_require_clean_fresh_context_and_separate_comparison():
    for needle in ("context-isolated", "excludes the plan, pseudocode", "different packet",
                   "invocation ID", "full selected diff"):
        assert needle in REVIEW, needle
    assert "two context-isolated invocations" in HANDOFFS
    assert "distinct ID" in HANDOFFS
    assert "one optional notes pass" in SKILL.lower()


def test_comparison_protocol_names_typed_inputs_and_inspection_hashes():
    roles = role_dir(__file__)
    architect = (roles / "al-architect.toml").read_text()
    for text in (SKILL, REVIEW, HANDOFFS, architect):
        assert "review-compare-start --plan" in text
        assert "inspected_artifacts" in text
    assert "--pseudocode" in architect
    assert "Only a verified `fixed`" in REVIEW


def test_distribution_docs_name_validated_ownership_result():
    distribution = (AL / "reference" / "distribution.md").read_text()
    assert "result_owned" in distribution
    assert "Identical unmanaged files remain unowned" in distribution


def test_workflow_docs_cover_sources_writers_gates_and_assessments():
    text = " ".join(SKILL.lower().split())
    for needle in ("task amend", "writer start", "writer finish", "gates discover",
                   "NOT_APPLICABLE", "candidate gates", "rollout-triggering changes",
                   "hashed screenshots", "interrupt", "resume --checkpoint", "escape record",
                   "user-reported", "review leads"):
        assert needle.lower() in text, needle
    assert "--target <rev>" in SKILL


def test_every_documented_state_command_exists():
    out = subprocess.run([sys.executable, str(AL / "scripts" / "al_state.py"), "--help"],
                         capture_output=True, text=True).stdout
    for cmd in set(re.findall(r"\$STATE (?:--session \S+ )?([a-z][a-z-]+)", SKILL)):
        assert cmd in out, f"SKILL.md uses `$STATE {cmd}`, which al_state.py does not provide"


def test_guarded_workflow_refuses_count_only_and_prose_approval():
    for needle in ("count-only", "old report", "structured JSON"):
        assert needle in SKILL, needle
    assert "never records approval" in REVIEW


def test_repository_policy_preserves_spec_authority_and_review_contexts():
    for needle in ("complete original user", "explicit amendment", "no-ticket",
                   "fresh Architect", "comparison Architect", "separate context",
                   "brainstorming", "systematic debugging"):
        assert needle.lower() in REPOSITORY_POLICY.lower(), needle


def test_rework6_protocol_covers_report_ownership_recovery_and_git_options():
    for needle in ("any finding `resolution` key", "UNSATISFIED", "same-report",
                   "explicit matching `prior_resolutions`", "stage: compare", "gate_recovery",
                   "PLAN_CONFLICT", "--config-env", "outside quotes"):
        assert needle in REVIEW, needle
    import json
    report_schema = json.loads((AL / "reference/schemas/architect-report.json").read_text())
    receipt_schema = json.loads((AL / "reference/schemas/resolution-validation.json").read_text())
    assert receipt_schema["properties"]["schema_version"]["const"] == 2
    assert receipt_schema["properties"]["stage"]["const"] == "compare"
    fresh_rule = next(rule["then"] for rule in report_schema["allOf"] if rule["if"]["properties"]["stage"]["const"]=="fresh")
    assert fresh_rule["not"] == {"required":["prior_resolutions"]}
    assert report_schema["properties"]["schema_version"]["const"] == 2
    assert "resolution" in str(fresh_rule["properties"]["findings"])


def test_rework11_recovery_shell_and_distribution_contracts_are_documented():
    distribution = " ".join((AL / "reference" / "distribution.md").read_text().split())
    for text in ("gate_recovery_supersessions", "SUPERSEDED_BY_AMENDMENT",
                 "old and new checkpoint references", "actual startup selector",
                 "abbreviations are refused"):
        assert text in REVIEW or text in SKILL, text
    for text in ("Existing registered", "validated adoptions are additive",
                 "does not relinquish previously registered files"):
        assert text in distribution, text


def test_rework17_shell_selector_and_native_publication_contracts_are_documented():
    for text in ("sh|bash|dash|zsh -c SCRIPT [ARGUMENTS...]",
                 "repeated selectors", "never searches later arguments"):
        assert text in REVIEW or text in SKILL, text
    for text in ("does not load or resume the old run", "different working directory",
                 "restores the previous pointer"):
        assert text in HANDOFFS or text in REVIEW, text


def test_ci_context_contract_is_run_bound_and_non_executing():
    import json
    gate_schema = json.loads((AL / "reference/schemas/gate-manifest.json").read_text())
    context_schema = json.loads((AL / "reference/schemas/ci-context-artifact.json").read_text())
    example = json.loads((AL / "reference/examples/ci-context-artifact.json").read_text())
    docs = (AL / "reference/gates.md").read_text()
    roles = [role_dir(__file__) / "al-architect.toml",
             role_dir(__file__) / "al-architect-fallback.toml"]
    assert all("CI profile selection" in role.read_text() for role in roles)
    declaration = gate_schema["properties"]["ci_context"]
    assert declaration["$ref"] == "ci-context-artifact.json#/$defs/declaration"
    assert context_schema["$defs"]["declaration"]["properties"]["profile"]["type"] == "string"
    assert "enum" not in context_schema["$defs"]["declaration"]["properties"]["profile"]
    assert context_schema["properties"]["task_hash"]["pattern"] == "^[0-9a-f]{64}$"
    assert context_schema["$defs"]["declaration"]["properties"]["source"]["properties"]["captured_at"]["type"] == "number"
    assert context_schema["properties"]["profile"]["type"] == "string"
    assert set(example) == {"schema_version", "profile", "session_id", "run_id", "task_hash", "selection"}
    assert example["profile"] == "merge_request_quality"
    for requirement in ("source.job", "UNRESOLVED", "ambient process environment",
                        "supervisor selects the profile", "Architect is read-only", "snapshot_bytes", "gates discover",
                        "gates plan", "does not create CI jobs", "Every other variable remains unknown",
                        "successful-status provenance", "replacement manifest"):
        assert requirement in docs, requirement

def test_replay_policy_contract_and_result_schema_match_the_fixture():
    import json
    replay_docs = (AL / "reference" / "replay.md").read_text()
    fixture = json.loads((AL / "reference" / "replay" / "harmless-searches.json").read_text())
    fixture_schema = json.loads((AL / "reference" / "schemas" / "replay-fixture.json").read_text())
    result_schema = json.loads((AL / "reference" / "schemas" / "replay-result.json").read_text())
    example = json.loads((AL / "reference" / "examples" / "replay-result.json").read_text())
    assert set(fixture["commands"]) == {"readonly", "shared"}
    assert [len(fixture["commands"][policy]) for policy in ("readonly", "shared")] == [7, 7]
    assert fixture_schema["properties"]["commands"]["additionalProperties"] is False
    assert set(result_schema["properties"]["cases"]["items"]["properties"]) >= {
        "candidate", "baseline", "baseline_comparison", "safety_regression"}
    assert result_schema["properties"]["cases"]["items"]["properties"]["candidate"]["properties"]["outcomes"]["items"]["oneOf"][1]["properties"]["kind"]["const"] == "policy"
    assert example["cases"][0]["candidate"]["counts"]["policy"]["matched"] == 1
    for requirement in ("never executes the command string", "selected package",
                        "safety_regressions", "never substituted", "unavailable"):
        assert requirement in replay_docs, requirement


def test_migration_script_guard_documentation_names_exact_exception():
    parallelism = (AL / "reference" / "parallelism.md").read_text()
    for required in ("migrate", "migration", "migrations", "colon-delimited",
                     "exact `migration:verify`", "`--dry-run`", "do not inherit"):
        assert required in parallelism, required


def test_service_coverage_policy_and_schemas_match_admission_contract():
    import json
    coverage = (AL / "reference" / "service-coverage.md").read_text()
    assessment = json.loads((AL / "reference/schemas/service-coverage.json").read_text())
    facts = json.loads((AL / "reference/schemas/repository-facts.json").read_text())
    manifest = json.loads((AL / "reference/schemas/gate-manifest.json").read_text())
    example = json.loads((AL / "reference/examples/service-coverage.json").read_text())
    for outcome in ("identified", "exhaustive", "incomplete"):
        assert f"`{outcome}`" in coverage
        assert outcome in str(assessment["$defs"]["assessment"]["properties"]["outcome"]["enum"])
    for term in ("dynamic import", "service enumeration", "check discovery", "opaque", "architecture_check.py",
                 "real tool", "execution_signature", "current evidence ledger"):
        assert term in coverage
    assert {"dependencies", "enumeration", "checks", "scan_inputs", "unsupported_boundaries"} <= set(facts["required"])
    assert "service_coverage" in manifest["properties"]
    assert {"obligation_ids", "execution_signature"} <= set(manifest["properties"]["gates"]["items"]["properties"])
    assert example["assessment"]["outcome"] == "exhaustive"
    assert example["assessment"]["enumeration"]["status"] == "complete"
    assert example["assessment"]["check_discovery"]["status"] == "complete"
    checks = example["assessment"]["required_bindings"]
    assert {tuple(check["units"]) for check in checks} == {
        ("services/a",),
        ("services/b",),
    }
    assert set(example["assessment"]["check_discovery"]["obligation_ids"]) == {
        check["id"] for check in checks
    }
    for check in checks:
        assert check["runner_resource"]["name"] == "al_architecture_check.py"
        assert check["runner_resource"]["version"] == 1
        assert check["argv"][1] == "-I"
        assert check["argv"][2].endswith("/scripts/al_architecture_check.py")
        assert check["argv"][3:] == ["--unit", check["units"][0]]
    assert {row["obligation_id"] for row in example["bindings"]} == {
        check["id"] for check in checks
    }



def test_writer_handoffs_are_generated_for_all_dispatches():
    for text in (SKILL, HANDOFFS):
        assert 'writer handoff --id' in text
        assert 'verbatim' in text and 'finalization' in text and 'follow-up' in text
        assert 'al_command' in text and 'controller' in text
    assert 'Always include the EVIDENCE block printed by' not in HANDOFFS
    assert '<planned-area>' not in HANDOFFS
