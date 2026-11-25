"""Public review obligations and conservative checkpoint resume behavior."""
import json
import sys
from pathlib import Path

import pytest
from hook_regression_helpers import begin, finish_review, invoke
import al_artifacts as A
import al_task as T

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


def test_spec_blocked_run_resumes_for_answer_and_requires_ready_gate(cli, state_file, tmp_path):
    invoke(
        cli,
        "start",
        "--spec",
        "Resolve the stated ambiguity, retain the original task, and verify the result.",
    )
    original = json.loads(state_file().read_text())
    package_ref = original['task_package'].copy()
    package = A.read_json(package_ref)
    original_task_hash = T.task_hash(original)

    code, out, err = cli(
        "spec-gate", "SPEC_BLOCKED", "--reason", "The acceptance criteria conflict."
    )
    assert code == 8, (out, err)
    blocked = json.loads(state_file().read_text())
    assert blocked['status'] == 'SPEC_BLOCKED' and blocked['spec_status'] == 'SPEC_BLOCKED'
    checkpoint_id = blocked.get('stopping_checkpoint')
    assert checkpoint_id
    assert f'Checkpoint: {checkpoint_id}' in out
    checkpoint_ref = next(ref for ref in blocked['checkpoints'] if ref['id'] == checkpoint_id)
    checkpoint = A.read_json(checkpoint_ref)
    assert checkpoint['prior_phase'] == 'PLANNING'
    assert checkpoint['task_hash'] == original_task_hash
    assert (
        'task amend' in checkpoint['resume_instructions']
        and 'SPEC_READY' in checkpoint['resume_instructions']
    )
    assert blocked['phase_occurrences'][-1]['ended'] is not None
    assert blocked['phase_occurrences'][-1]['completed'] is False
    assert blocked['completed_phases'] == original['completed_phases']

    before = state_file().read_bytes()
    invoke(cli, 'spec-gate', 'SPEC_READY', ok=False)
    invoke(cli, 'task', 'amend', '--file', str(tmp_path / 'answer.json'), ok=False)
    invoke(
        cli,
        'writer',
        'start',
        '--id',
        'blocked',
        '--role',
        'al-engineer',
        '--files',
        'svc/app.py',
        ok=False,
    )
    invoke(cli, 'phase', 'IMPLEMENTATION', ok=False)
    assert state_file().read_bytes() == before

    invoke(cli, 'resume', '--checkpoint', checkpoint_id)
    resumed = json.loads(state_file().read_text())
    assert resumed['status'] == 'RUNNING' and resumed['phase'] == 'PLANNING'
    assert resumed['spec_status'] == 'SPEC_BLOCKED'
    assert resumed['resumes'][-1]['checkpoint'] == checkpoint_id

    answer = tmp_path / 'answer.json'
    answer.write_text(json.dumps({
        'id': 'ANSWER-1',
        'source': 'user clarification',
        'text': 'The user clarifies the disputed acceptance behavior for this same task.',
    }))
    invoke(cli, 'task', 'amend', '--file', str(answer))
    amended = json.loads(state_file().read_text())
    assert amended['task_package'] == package_ref
    assert A.read_json(amended['task_package']) == package
    assert [item['id'] for item in amended['amendments']] == ['ANSWER-1']
    assert A.read_json(amended['amendments'][0]['artifact'])['text'] == (
        'The user clarifies the disputed acceptance behavior for this same task.'
    )
    assert T.task_hash(amended) != original_task_hash
    assert amended['spec_status'] is None and amended['phase'] == 'PLANNING'
    before = state_file().read_bytes()
    invoke(cli, 'phase', 'IMPLEMENTATION', ok=False)
    assert state_file().read_bytes() == before

    invoke(cli, 'spec-gate', 'SPEC_READY')
    ready = json.loads(state_file().read_text())
    assert ready['status'] == 'RUNNING' and ready['spec_status'] == 'SPEC_READY'
    assert ready['task_package'] == package_ref and ready['amendments'][0]['id'] == 'ANSWER-1'


@pytest.mark.parametrize('defect', ['missing', 'stale', 'foreign', 'corrupt'])
def test_spec_blocked_resume_refuses_stale_foreign_or_corrupt_checkpoint(
        cli, state_file, tmp_path, defect):
    invoke(
        cli,
        'start',
        '--spec',
        'Resume only the latest intact checkpoint for this blocked specification.',
    )
    if defect == 'stale':
        waiting = tmp_path / 'waiting.json'
        waiting.write_text(json.dumps({'reason': 'Awaiting external input.',
                                       'resume_instructions': 'Continue the same run.'}))
        _, out, _ = invoke(cli, 'waiting', '--file', str(waiting))
        stale_id = out.strip()
        invoke(cli, 'resume', '--checkpoint', stale_id)
    code, _, err = cli('spec-gate', 'SPEC_BLOCKED', '--reason', 'Clarification is required.')
    assert code == 8, err
    st = json.loads(state_file().read_text())
    latest_id = st['stopping_checkpoint']
    latest_ref = next(ref for ref in st['checkpoints'] if ref['id'] == latest_id)
    if defect == 'missing':
        attempted_id = 'missing-checkpoint-id'
    elif defect == 'stale':
        assert latest_id != stale_id
        attempted_id = stale_id
    elif defect == 'foreign':
        data = A.read_json(latest_ref)
        data['identity'] = dict(data['identity'], root='/foreign/repository')
        foreign = A.snapshot_bytes(
            st['session_id'], json.dumps(data).encode(), 'checkpoint', 'foreign checkpoint'
        )
        st['checkpoints'].append(foreign)
        st['stopping_checkpoint'] = foreign['id']
        state_file().write_text(json.dumps(st))
        attempted_id = foreign['id']
    else:
        Path(latest_ref['stored_path']).write_bytes(b'corrupt checkpoint')
        attempted_id = latest_id
    before = state_file().read_bytes()
    invoke(cli, 'resume', '--checkpoint', attempted_id, ok=False)
    assert state_file().read_bytes() == before


# A content edit inside planned paths returns to verification. Since 4bd481d gate currency
# compares the task's coverage-requirement projection: an edit that changes it (an unparsable
# source, an unknown import) makes the planned gates stale, so the resume replans from PLANNING.
@pytest.mark.parametrize(('body', 'expected'), [
    ('x = "changed while stopped"\n', 'VERIFICATION'),
    ('changed while stopped\n', 'PLANNING'),
    ('import unknown_new_dependency\nx = 2\n', 'PLANNING'),
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
    if expected == "PLANNING":
        assert resumed["spec_status"] is None
        assert "task coverage requirements changed; rediscover repository gates" in resumed["recovery_replans"][-1]["reasons"]
    else:
        assert not resumed.get("recovery_replans")
