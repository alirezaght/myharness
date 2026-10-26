"""Generated writer prompts at the public CLI boundary."""
import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest
import al_artifacts as A
from hook_regression_helpers import invoke
from test_al_usability import pending, state


@pytest.fixture
def repo(tmp_path, seed_repo):
    root = tmp_path / 'repository with spaces'
    shutil.copytree(seed_repo, root, symlinks=True)
    return root


def ready(cli, tmp_path, repo):
    pending(cli, tmp_path, repo)
    invoke(cli, 'writer', 'start', '--id', 'worker', '--role', 'al-builder', '--files',
           'svc/app.py', '--degraded', '--reason', 'Track proof repair')
    for area in ('unit', 'other'):
        invoke(cli, 'evidence', 'plan', '--area', area, '--tier', 'focused', '--scope',
               'svc/app.py', '--kind', 'check', '--', sys.executable, '-c', 'raise Exception("must not run")')
    body = tmp_path / 'body with spaces.md'
    body.write_bytes(b'ORIGINAL SPEC: retain exactly.\r\nBuilder owns svc/app.py.\n')
    return body


def test_public_handoff_injects_evidence_without_manual_block(cli, tmp_path, repo):
    body = ready(cli, tmp_path, repo)
    before = state(cli)
    _, prompt, _ = invoke(cli, 'writer', 'handoff', '--id', 'worker', '--body-file', str(body))
    assert prompt.startswith(body.read_bytes().decode())
    assert 'EVIDENCE' in prompt and '<planned-area>' not in prompt
    commands = [shlex.split(line) for line in prompt.splitlines() if line.startswith('alctl ')]
    assert [c[-1] for c in commands] == ['other', 'unit']
    for c in commands:
        assert c[c.index('--repository')+1] == str(repo)
        assert c[c.index('--session')+1] == before['session_id']
        assert c[c.index('--writer')+1] == 'worker'
        assert c[c.index('--platform')+1] in {'codex', 'claude'}
    after = state(cli)
    receipt = after['writers']['worker']['handoffs'][-1]
    assert A.read(receipt['artifact']).decode() == prompt
    assert receipt['areas'] == ['other', 'unit'] and len(receipt['plan_digest']) == 64
    assert after['evidence'] == before['evidence']
    assert after['writers']['worker']['state'] == 'ACTIVE'


def test_selection_repeat_generation_and_harness_route(cli, tmp_path, repo, state_file):
    body = ready(cli, tmp_path, repo)
    # Attach a valid local protocol fixture without starting services or checks.
    from al_events import initialise
    current = state(cli)
    initialise(current, {'execution_id': 'execution-test', 'task_id': 'task-test',
                         'approval': {'approval_id': 1, 'spec_version': 1,
                                      'content_hash': 'sha256:' + 'a' * 64,
                                      'revision': 'fixture', 'review_state_hash': 'sha256:' + 'b' * 64}}, body)
    state_file().write_text(json.dumps(current))
    _, prompt, _ = invoke(cli, 'writer', 'handoff', '--id', 'worker', '--body-file', str(body),
                          '--area', 'unit', '--area', 'unit')
    assert 'al_command ["evidence", "run", "--area", "unit"]' in prompt
    assert 'controller binds' in prompt
    receipt = state(cli)['writers']['worker']['handoffs'][-1]
    assert receipt['areas'] == ['unit']
    invoke(cli, 'writer', 'handoff', '--id', 'worker', '--body-file', str(body), '--area', 'unit')
    receipts = state(cli)['writers']['worker']['handoffs']
    assert receipts[-1]['artifact']['sha256'] == receipts[-2]['artifact']['sha256']


@pytest.mark.parametrize('case', ['unknown', 'finished', 'unresolved', 'role', 'paused', 'completed',
                                'phase', 'empty-plan', 'unknown-area', 'missing-body', 'empty-body',
                                'invalid-utf8', 'managed-begin', 'managed-end'])
def test_invalid_handoff_publishes_neither_prompt_nor_receipt(cli, tmp_path, repo, state_file, case):
    body = ready(cli, tmp_path, repo)
    current = state(cli)
    extra = []
    if case == 'unknown': extra = ['--id', 'absent']
    if case in {'finished', 'unresolved'}: current['writers']['worker']['state'] = case.upper()
    if case == 'role': current['writers']['worker']['role'] = 'al-architect'
    if case in {'paused', 'completed'}: current['status'] = case.upper()
    if case == 'phase': current['writers']['worker']['phase'] = 'IMPLEMENTATION'
    if case == 'empty-plan': current['evidence']['plan'] = {}
    if case == 'unknown-area': extra = ['--area', 'absent']
    if case == 'missing-body': body.unlink()
    if case == 'empty-body': body.write_text(' \n')
    if case == 'invalid-utf8': body.write_bytes(b'\xff')
    if case.startswith('managed-'):
        body.write_text('<!-- AL MANAGED WRITER EVIDENCE ' + ('BEGIN' if case.endswith('begin') else 'END') + ' -->')
    state_file().write_text(json.dumps(current))
    before = state_file().read_bytes()
    code, out, err = invoke(cli, 'writer', 'handoff', '--id', 'worker', '--body-file', str(body), *extra, ok=False)
    assert code != 0 and 'alctl run ' not in out
    assert state_file().read_bytes() == before


def test_storage_failure_cannot_publish_success(cli, tmp_path, repo, monkeypatch):
    body = ready(cli, tmp_path, repo)
    import al_writer_handoff as H
    def fail(*args):
        raise OSError('storage unavailable')
    monkeypatch.setattr(H.al_store, 'save', fail)
    with pytest.raises(OSError, match='storage unavailable'):
        cli('writer', 'handoff', '--id', 'worker', '--body-file', str(body))
