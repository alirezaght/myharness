"""Real managed review phases with external reviewer responses as fixture inputs."""

import json

from al.tests.behavior_observation_helpers import observation, prepare_behavior, publish
from al.tests.editorial_receipt_helpers import state, state_path, succeed
from al.tests.runtime_run_helpers import public_run


def ready_for_review(launched, platform):
    packet = prepare_behavior(launched, platform)
    succeed(public_run(launched, platform, "gates", "run", "--stage", "base"))
    succeed(public_run(launched, platform, "phase", "IMPLEMENTATION"))
    succeed(public_run(launched, platform, "phase", "VERIFICATION"))
    succeed(public_run(launched, platform, "gates", "run", "--stage", "candidate"))
    before = state_path(launched, platform).read_bytes()
    refused = public_run(launched, platform, "review-start")
    assert refused.returncode != 0
    assert "behavior scenario" in refused.stdout + refused.stderr
    assert state_path(launched, platform).read_bytes() == before
    for scenario in ("read-persisted", "reject-malformed"):
        succeed(publish(launched, platform, observation(launched, platform, packet, scenario)))
    succeed(public_run(launched, platform, "review-start"))
    return json.loads(
        succeed(public_run(launched, platform, "review-packet", "--stage", "fresh")).stdout
    )


def report_for(launched, platform, issued, stage, invocation):
    value = issued["content"]
    attempt = value["attempt"]
    evidence = next(
        entry["id"]
        for entry in reversed(state(launched, platform)["evidence"]["entries"])
        if entry["area"] == "parser-journeys"
    )
    criteria = value["behavior"]["obligations"]["content"]["criteria_registry"]
    result = {
        "schema_version": 2,
        "kind": "architect-review-report-v2",
        "review_id": value["review_id"],
        "stage": stage,
        "reviewer_role": "al-architect",
        "model": attempt["model"],
        "invocation_id": invocation,
        "attempt_id": attempt["attempt_id"],
        "effort": attempt["effort"],
        "policy_digest": attempt["policy_digest"],
        "candidate_hash": value["candidate_hash"],
        "task_hash": value["task_hash"],
        "document_hash": value["document_hash"],
        "verification_authority_hash": value["verification_authority"]["hash"],
        "packet_hash": issued["packet"]["sha256"],
        "inspected_artifacts": [issued["packet"]["sha256"]],
        "criteria": [
            {
                "id": criterion["id"],
                "status": "SATISFIED",
                "rationale": "Real parser journey assertions and both current scenario observations were inspected.",
                "locations": ["tests/test_rows.py:11", "tests/test_rows.py:23"],
                "evidence": [evidence],
            }
            for criterion in criteria
        ],
        "findings": [],
        "status": "APPROVED",
    }
    if stage == "fresh":
        result["counterexample_search"] = [
            {
                "obligation_ids": ["json-journey"],
                "scenario": "A stale in-memory return or accepted malformed JSON would contradict the declared journey.",
                "inspected_locations": [
                    "tests/test_rows.py:10",
                    "tests/test_rows.py:11",
                    "tests/test_rows.py:23",
                    "tests/test_rows.py:24",
                ],
                "evidence": [evidence],
                "result": "none_found",
                "rationale": "The executable source checks a fresh parser result, malformed-input rejection and unchanged persisted bytes.",
            }
        ]
    else:
        result.update(
            prior_resolutions=[],
            plan_defects=[],
            assessments={
                "rollout": {
                    "applicable": False,
                    "reason": "No deployment or migration behavior exists in this local parser fixture.",
                },
                "ui": {
                    "applicable": False,
                    "reason": "This parser fixture has no rendered interface.",
                },
            },
        )
        result["inspected_artifacts"].append(
            value["approved_inputs"]["references"]["plan"]["sha256"]
        )
    return result


def save_review(launched, platform, report):
    path = launched[2].parent / ("review-" + report["stage"] + ".json")
    path.write_text(json.dumps(report))
    return public_run(
        launched,
        platform,
        "review-fresh" if report["stage"] == "fresh" else "review-result",
        "--file",
        str(path),
    )


def comparison_packet(launched, platform):
    plan = launched[2].parent / "comparison-only-plan.md"
    plan.write_text(
        "Approved comparison-only outline: execute real parser journeys and preserve malformed-input refusal.\n"
    )
    succeed(
        public_run(
            launched,
            platform,
            "review-compare-start",
            "--plan",
            str(plan),
            "--approval-source",
            "Disposable supervisor-designated approved fixture outline",
        )
    )
    return json.loads(
        succeed(public_run(launched, platform, "review-packet", "--stage", "compare")).stdout
    )
