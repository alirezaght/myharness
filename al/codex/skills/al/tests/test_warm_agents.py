"""Warm agents by default (#30): recorded instances and the printed resume instruction."""
import shutil
import sys

import pytest
import al_agents
from hook_regression_helpers import invoke
from test_al_usability import pending, state


@pytest.fixture
def repo(tmp_path, seed_repo):
    root = tmp_path / 'repository'
    shutil.copytree(seed_repo, root, symlinks=True)
    return root


def test_handoff_records_the_agent_and_prints_how_to_resume_it(cli, tmp_path, repo):
    pending(cli, tmp_path, repo)
    invoke(cli, 'writer', 'start', '--id', 'worker', '--role', 'al-builder', '--files',
           'svc/app.py', '--degraded', '--reason', 'Track proof repair')
    invoke(cli, 'evidence', 'plan', '--area', 'unit', '--tier', 'focused', '--scope',
           'svc/app.py', '--kind', 'check', '--', sys.executable, '-c', 'pass')
    body = tmp_path / 'body.md'
    body.write_text('ORIGINAL SPEC: retain exactly.\n')
    _, prompt, err = invoke(cli, 'writer', 'handoff', '--id', 'worker', '--body-file', str(body), '--agent-id', 'agent-b1')
    assert prompt.startswith('ORIGINAL SPEC') and 'resume' not in prompt  # the prompt stays intact
    assert 'agent-b1' in err and al_agents.resume_call('agent-b1') in err
    rows = state(cli)['agent_instances']
    assert rows[0]['role'] == 'al-builder' and rows[0]['id'] == 'agent-b1' and rows[0]['writer'] == 'worker'

    _, out, _ = invoke(cli, 'agent', 'resume', '--role', 'al-builder')
    assert al_agents.resume_call('agent-b1') in out and 'exception' in out
    # Re-using the same agent counts a use; a different one without a reason still records, with a warning.
    invoke(cli, 'agent', 'record', '--role', 'al-builder', '--id', 'agent-b1', '--purpose', 'correction')
    _, _, err = invoke(cli, 'agent', 'record', '--role', 'al-builder', '--id', 'agent-b2')
    assert 'warning' in err and 'agent-b1' in err
    invoke(cli, 'agent', 'record', '--role', 'al-architect', '--id', 'arch-2', '--fresh-reason', 'final independent review')
    rows = state(cli)['agent_instances']
    assert rows[0]['uses'] == 2 and rows[-1]['fresh_reason'] == 'final independent review'
    _, out, _ = invoke(cli, 'agent', 'list')
    assert 'arch-2' in out and 'fresh: final independent review' in out


def test_resume_call_matches_the_host():
    assert al_agents.resume_call('a1', 'claude') == 'Send' + 'Message(to="a1", message=<prompt>)'
    assert al_agents.resume_call('a1', 'codex').startswith('followup_task(id="a1"')
