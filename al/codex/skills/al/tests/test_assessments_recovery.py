"""Public review obligations and conservative checkpoint resume behavior."""
import json
import sys
from pathlib import Path

import pytest
from hook_regression_helpers import begin, finish_review, invoke

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import al_assessments


def test_rollout_trigger_requires_each_operational_dimension():
    candidate = {"files": [{"path": "infra/main.tf"}], "changed_paths": ["infra/main.tf"], "diff": ""}
    dimensions = ("expand_contract", "deployment_order", "aggregator_environment", "consumer_deployment", "dlq_redrive")
    assessment = {"applicable": True, "reason": "Terraform changes require rollout review."}
    assessment.update({key: {"status": "NOT_APPLICABLE", "reason": "No such dependency in this change."} for key in dimensions})
    report = {"assessments": {"rollout": assessment, "ui": {"applicable": False, "reason": "No rendered interface."}}}
    al_assessments.validate(report, candidate)
    del assessment["consumer_deployment"]
    with pytest.raises(SystemExit, match="missing rollout dimension consumer_deployment"):
        al_assessments.validate(report, candidate)


def test_rendered_ui_trigger_requires_observations_and_artifact():
    candidate = {"files": [{"path": "apps/web/page.astro"}], "changed_paths": ["apps/web/page.astro"], "diff": ""}
    report = {"assessments": {"rollout": {"applicable": False, "reason": "No rollout surface."},
                              "ui": {"applicable": True, "reason": "Rendered page changed."}}}
    with pytest.raises(SystemExit, match="rendered UI metadata/checks missing"):
        al_assessments.validate(report, candidate)


def test_waiting_checkpoint_resumes_unchanged_candidate(cli, state_file, tmp_path):
    cli("start", "--spec", "Resume the same reviewed candidate after an external wait.")
    payload = tmp_path / "wait.json"
    payload.write_text(json.dumps({"reason": "Awaiting a review input.", "resume_instructions": "Continue verification."}))
    code, out, _ = cli("waiting", "--file", str(payload))
    assert code == 0
    checkpoint = out.strip()
    stopped = json.loads(state_file().read_text())
    assert stopped["status"] == "WAITING_FOR_USER"
    assert stopped["stopping_checkpoint"] == checkpoint
    assert cli("resume", "--checkpoint", checkpoint)[0] == 0
    resumed = json.loads(state_file().read_text())
    assert resumed["status"] == "RUNNING"
    assert resumed["resumes"][-1]["changed"] is False


# A content edit inside planned paths returns to verification; new obligations it implies
# (an unparsable source, an unknown import) are caught by the review-start inventory.
@pytest.mark.parametrize(('body', 'expected'), [
    ('x = "changed while stopped"\n', 'VERIFICATION'),
    ('changed while stopped\n', 'VERIFICATION'),
    ('import unknown_new_dependency\nx = 2\n', 'VERIFICATION'),
])
def test_changed_candidate_resume_invalidates_review_and_returns_to_verification(cli, state_file, tmp_path, repo, body, expected):
    begin(cli, tmp_path, repo, "Resume into verification if the reviewed candidate changes while stopped.")
    finish_review(cli, tmp_path, invocation="recovery")
    invoke(cli, "phase", "FINAL_VERIFICATION")
    payload = tmp_path / "wait.json"
    payload.write_text(json.dumps({"reason": "Awaiting a review input.", "resume_instructions": "Reverify the changed candidate."}))
    _, out, _ = invoke(cli, "interrupt", "--file", str(payload))
    (repo / "svc" / "app.py").write_text(body)
    invoke(cli, "resume", "--checkpoint", out.strip())
    resumed = json.loads(state_file().read_text())
    assert resumed["phase"] == expected
    assert resumed["review"] is None
    assert resumed["resumes"][-1]["changed"] is True
