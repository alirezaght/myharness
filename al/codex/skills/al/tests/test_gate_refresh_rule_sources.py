"""Issue #32: a writer's edit to a sourced rule file refreshes the gates instead of making them INFRA.

A writer documents a unit's new behaviour in the unit's nested AGENTS.md. After `writer finish`
and the phase steps, gates run against a refreshed manifest; none ends INFRA with 'source rule
changed before launch'. When a refresh cannot run, `gates run` stops before any gate with one
message naming the changed sources and the recovery commands."""
import json
import subprocess
from hook_regression_helpers import invoke
from batch5_helpers import state
from launcher_context import admit_planning


def _commit(repo, rel, text):
    (repo/rel).write_text(text)
    for args in (('add', rel), ('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'nested rules')):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)


def _implementing(cli, tmp, repo, paths):
    _commit(repo, 'svc/AGENTS.md', 'Service instructions.\n')
    invoke(cli, 'start', '--spec', 'Change the service and document it in its AGENTS.md.')
    invoke(cli, 'spec-gate', 'SPEC_READY')
    invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    file = tmp/'paths.json'; file.write_text(json.dumps(paths))
    invoke(cli, 'gates', 'discover', '--paths-file', str(file))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    admit_planning(cli); invoke(cli, 'phase', 'IMPLEMENTATION')
    invoke(cli, 'writer', 'start', '--id', 'w1', '--role', 'al-engineer', '--files', ','.join(paths))


def _sourced(st):
    import al_artifacts as A
    return {r['path'] for r in A.read_json(st['gate_manifests'][-1])['rules']}


def _no_infra(out):
    text = ''.join(out[1:])
    assert 'source rule changed before launch' not in text
    assert 'INFRA' not in text


def test_nested_instruction_edit_refreshes_at_writer_finish_and_gates_run(cli, tmp_path, repo):
    _implementing(cli, tmp_path, repo, ['svc/app.py', 'svc/AGENTS.md'])
    before = state(cli); old = before['gate_manifests'][-1]['sha256']
    assert 'svc/AGENTS.md' in _sourced(before)
    (repo/'svc/app.py').write_text('x = 2\n')
    (repo/'svc/AGENTS.md').write_text('Service instructions.\nThe service now returns 2.\n')
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    after = state(cli)
    assert after['gate_manifests'][-1]['sha256'] != old
    assert any('svc/AGENTS.md' in r for r in after['gate_refreshes'][-1]['reasons'])
    invoke(cli, 'phase', 'FINALIZATION'); invoke(cli, 'phase', 'VERIFICATION')
    _no_infra(invoke(cli, 'gates', 'run', '--stage', 'base'))
    _no_infra(invoke(cli, 'gates', 'run', '--stage', 'candidate'))


def test_sourced_rule_outside_planned_file_entries_refreshes_at_phase_step(cli, tmp_path, repo):
    # The plan names only the code file; the unit's AGENTS.md is sourced by discovery, not planned.
    _implementing(cli, tmp_path, repo, ['svc/app.py'])
    before = state(cli); old = before['gate_manifests'][-1]['sha256']
    assert 'svc/AGENTS.md' in _sourced(before)
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    # Edited outside any writer: the next phase step refreshes it.
    (repo/'svc/AGENTS.md').write_text('Service instructions, revised.\n')
    invoke(cli, 'phase', 'FINALIZATION')
    assert state(cli)['gate_manifests'][-1]['sha256'] != old
    invoke(cli, 'phase', 'VERIFICATION')
    _no_infra(invoke(cli, 'gates', 'run', '--stage', 'candidate'))


def test_gates_run_refreshes_itself_before_materialising(cli, tmp_path, repo):
    _implementing(cli, tmp_path, repo, ['svc/app.py', 'svc/AGENTS.md'])
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    invoke(cli, 'phase', 'FINALIZATION'); invoke(cli, 'phase', 'VERIFICATION')
    old = state(cli)['gate_manifests'][-1]['sha256']
    (repo/'svc/AGENTS.md').write_text('Edited after the last step.\n')
    out = invoke(cli, 'gates', 'run', '--stage', 'candidate')
    _no_infra(out)
    assert 'gates refreshed' in ''.join(out[1:])
    assert state(cli)['gate_manifests'][-1]['sha256'] != old


def test_gates_run_refuses_once_with_recovery_when_refresh_cannot_run(cli, tmp_path, repo, monkeypatch):
    _implementing(cli, tmp_path, repo, ['svc/app.py', 'svc/AGENTS.md'])
    invoke(cli, 'writer', 'finish', '--id', 'w1', '--outcome', 'success')
    invoke(cli, 'phase', 'FINALIZATION'); invoke(cli, 'phase', 'VERIFICATION')
    before = state(cli)
    import al_gate_refresh
    monkeypatch.setattr(al_gate_refresh, 'refresh', lambda st: {'current': False, 'reasons': ['blocked'], 'paths': []})
    (repo/'svc/AGENTS.md').write_text('Edited after the last step.\n')
    out = ''.join(map(str, invoke(cli, 'gates', 'run', '--stage', 'candidate', ok=False)))
    assert 'svc/AGENTS.md' in out
    assert 'gates discover --extend current --paths-file' in out and 'gates plan' in out
    assert 'INFRA' not in out and out.count('source rule changed') <= 1
    after = state(cli)
    assert after['gate_manifests'] == before['gate_manifests']
    assert len(after['evidence']['entries']) == len(before['evidence']['entries'])
