"""Agent communication (#30): progress checkpoints, early questions, structured handoff records."""
import json
import shutil
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from hook_regression_helpers import invoke
from test_al_usability import pending, state

SCHEMA = Path(__file__).resolve().parents[1] / 'reference/schemas/agent-handoff-record.json'


@pytest.fixture
def repo(tmp_path, seed_repo):
    root = tmp_path / 'repository'
    shutil.copytree(seed_repo, root, symlinks=True)
    return root


def _record(evidence_id):
    return {'schema_version': 1, 'kind': 'agent-handoff-record-v1', 'role': 'al-engineer', 'agent_id': 'eng-1',
            'decisions': [{'decision': 'Keep the retry in the client', 'reason': 'The server contract is fixed'}],
            'assumptions': ['The queue is at-least-once'], 'files_touched': ['svc/app.py'],
            'verified': [{'claim': 'Unit tests pass', 'evidence_ids': [evidence_id]}],
            'unverified': [{'claim': 'Load behaviour', 'reason': 'No load environment'}],
            'open_questions': ['Should retries be capped at 5?'], 'risks': ['Duplicate delivery under failover']}


def test_progress_and_questions_show_in_status_line_and_inspect(cli, tmp_path, repo):
    pending(cli, tmp_path, repo)
    invoke(cli, 'progress', '--note', 'core parser done', '--done', '2', '--total', '5', '--role', 'al-engineer')
    invoke(cli, 'ask', '--blocking', '--question', 'Cap retries at 5?', '--role', 'al-engineer', '--agent', 'eng-1')
    invoke(cli, 'ask', '--non-blocking', '--question', 'Rename the helper?')
    _, line, _ = invoke(cli, 'show', '--line')
    assert 'al-engineer: core parser done (2/5)' in line and '2 open question(s) (1 blocking)' in line
    st = state(cli)
    from al_inspection import inspect_session
    report = inspect_session(st['session_id'], expected_run=st['run_id'], expected_repo=str(repo), sections=['summary'])
    summary = report['sections']['summary']
    assert summary['progress']['note'] == 'core parser done' and len(summary['open_questions']) == 2
    _, out, _ = invoke(cli, 'ask', '--answer', 'Q1', '--text', 'Yes, cap at 5.')
    assert 'eng-1' in out
    invoke(cli, 'ask', '--answer', 'Q1', '--text', 'again', ok=False)
    _, line, _ = invoke(cli, 'show', '--line')
    assert '1 open question(s)' in line and 'blocking' not in line and '1 answer(s) to forward' in line
    # An answer (typed here, or from the Harness dashboard, #31) goes back to the agent that asked.
    _, out, _ = invoke(cli, 'next')
    assert 'forward the answer to Q1 (Yes, cap at 5.) to its waiting agent' in out and 'eng-1' in out
    invoke(cli, 'ask', '--forwarded', 'Q2', ok=False)
    invoke(cli, 'ask', '--forwarded', 'Q1')
    _, out, _ = invoke(cli, 'next')
    assert 'forward the answer' not in out
    invoke(cli, 'progress', '--note', 'bad', '--done', '6', '--total', '5', ok=False)
    invoke(cli, 'ask', '--question', 'neither kind', ok=False)


def test_handoff_record_is_schema_validated_and_reaches_the_next_writer(cli, tmp_path, repo):
    pending(cli, tmp_path, repo)
    invoke(cli, 'writer', 'start', '--id', 'w1', '--role', 'al-engineer', '--files', 'svc/app.py', '--degraded', '--reason', 'proof repair')
    invoke(cli, 'evidence', 'plan', '--area', 'unit', '--tier', 'focused', '--scope', 'svc/app.py', '--kind', 'check',
           '--', sys.executable, '-c', 'print("1 passed")')
    invoke(cli, 'evidence', 'run', '--area', 'unit', '--writer', 'w1')
    evidence_id = state(cli)['evidence']['entries'][-1]['id']
    record = _record(evidence_id)
    assert not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(record))
    path = tmp_path / 'record.json'
    for bad in ({**record, 'extra': 1}, {**record, 'verified': [{'claim': 'x', 'evidence_ids': ['E999']}]},
                {**record, 'files_touched': ['../outside']}, {**record, 'kind': 'summary'}):
        path.write_text(json.dumps(bad))
        invoke(cli, 'handoff-record', '--file', str(path), '--writer', 'w1', ok=False)
    path.write_text(json.dumps(record))
    _, out, _ = invoke(cli, 'handoff-record', '--file', str(path), '--writer', 'w1')
    assert '1 verified, 1 unverified' in out
    stored = state(cli)['handoff_records'][-1]
    assert json.loads(Path(stored['artifact']['stored_path']).read_bytes()) == record
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    invoke(cli, 'writer', 'start', '--id', 'w2', '--role', 'al-builder', '--files', 'svc/app.py', '--degraded', '--reason', 'proof repair')
    body = tmp_path / 'body.md'
    body.write_text('ORIGINAL SPEC: retain exactly.\n')
    _, prompt, _ = invoke(cli, 'writer', 'handoff', '--id', 'w2', '--body-file', str(body))
    assert 'PRIOR AGENT HANDOFF RECORDS' in prompt and stored['artifact']['sha256'] in prompt
    assert 'Duplicate delivery under failover' in prompt


def test_token_usage_is_tracked_per_role(cli, tmp_path, repo):
    pending(cli, tmp_path, repo)
    invoke(cli, 'agent', 'usage', '--role', 'al-architect', '--output-tokens', '1200', '--input-tokens', '50000')
    invoke(cli, 'agent', 'usage', '--role', 'al-architect', '--output-tokens', '300')
    invoke(cli, 'agent', 'usage', '--role', 'al-architect', '--output-tokens', '-1', ok=False)
    assert state(cli)['output_tokens']['al-architect'] == {'output': 1500, 'input': 50000, 'invocations': 2}
