import json

import pytest

import al_store
from execution_helpers import bound, report


def test_capabilities_without_run_and_atomic_outbox(cli, tmp_path, repo, monkeypatch):
    code, out, _ = cli('integration', 'capabilities')
    assert code == 0 and json.loads(out)['protocol'] == 'al-execution-v1'
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch)
    st = al_store.load(sid)
    assert [e['kind'] for e in st['integration']['outbox']] == ['run.started', 'phase.changed']
    assert not st['integration']['agents']  # planned phase roles are not actual invocations
    before = al_store.state_path(sid).read_bytes()
    original = al_store.os.replace
    def fail_replace(*args):
        raise OSError('fault before state replacement')
    monkeypatch.setattr(al_store.os, 'replace', fail_replace)
    st['spec_status'] = 'SPEC_READY'
    with pytest.raises(OSError):
        al_store.save(sid, st)
    assert al_store.state_path(sid).read_bytes() == before
    monkeypatch.setattr(al_store.os, 'replace', original)
    code, out, _ = cli('integration', 'events', '--after', '0', '--limit', '1')
    page = json.loads(out)
    assert code == 0 and page['has_more'] and len(page['events']) == 1
    controller.conn.close()


def test_reporting_cannot_manufacture_approval(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch)
    code, _, _ = report(cli, tmp_path, 'agent-update', {'id': 'fake', 'status': 'Completed', 'kind': 'implementation.result', 'approval': {}})
    assert code != 0
    assert al_store.load(sid)['status'] == 'RUNNING'
    controller.conn.close()


def test_target_harness_package_cannot_shadow_authority_bridge(cli,tmp_path,repo,monkeypatch):
    from execution_helpers import register_task
    controller,_,sid=bound(cli,tmp_path,repo,monkeypatch)
    assert register_task(cli,tmp_path,'A',['svc'])[0] == 0
    shadow=repo/'harness'; shadow.mkdir()
    (shadow/'__init__.py').write_text('')
    marker=tmp_path/'untrusted-authority-ran'
    (shadow/'execution_authority.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("untrusted target module ran")\nraise SystemExit("target module shadowed authority")\n')
    code,out,err=cli('integration','task-admit','--task','A')
    assert code == 0,(code,out,err)
    assert not marker.exists(), 'target repository replaced the trusted authority bridge'
    controller.conn.close()


def _planning_gate(cli, tmp_path, sid):
    from hook_regression_helpers import manifest_for
    assert cli('spec-gate', 'SPEC_READY')[0] == 0
    assert cli('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')[0] == 0
    manifest = manifest_for(al_store.load(sid), al_store.REPO_ROOT, ['svc/app.py'])
    path = tmp_path / 'bootstrap-gates.json'
    path.write_text(json.dumps(manifest))
    result = cli('gates', 'plan', '--file', str(path))
    assert result[0] == 0, result
    gate = next(g for g in manifest['gates'] if g['required'] and g['applicability'] == 'APPLICABLE')
    return gate


@pytest.mark.parametrize('bind_source', [False, True])
def test_pre_task_base_gate_is_real_but_candidate_and_label_spoof_are_refused(cli, tmp_path, repo, monkeypatch, bind_source):
    from execution_helpers import register_task
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch, bind_source=bind_source)
    from harness.execution_store import get_execution
    assert bool(get_execution(controller.conn, eid)['al_run_id']) == bind_source
    gate = _planning_gate(cli, tmp_path, sid)
    state = al_store.load(sid)
    assert state['phase'] == 'PLANNING' and state['integration']['tasks'] == {}
    before = al_store.state_path(sid).read_bytes()
    assert cli('gates', 'run', '--stage', 'candidate')[0] != 0
    result = cli('evidence', 'run', '--area', 'gate-base-' + gate['id'], '--kind', 'check',
                 '--scope', ','.join(gate['scope']), '--', *gate['argv'])
    assert result[0] != 0, result
    assert al_store.state_path(sid).read_bytes() == before
    result = cli('gates', 'run', '--stage', 'base')
    assert result[0] == 0, result
    state = al_store.load(sid)
    assert state['integration']['tasks'] == {} and state['integration']['graph_revision'] == 0
    assert bool(get_execution(controller.conn, eid)['al_run_id']) == bind_source
    # A cached real gate result cannot be reused through a label-only public call.
    assert cli('evidence', 'run', '--area', 'gate-base-' + gate['id'], '--kind', 'check', '--scope', ','.join(gate['scope']), '--', *gate['argv'])[0] != 0
    assert state['gate_runs'] and all(e['status'] == 'PASS' and e['gate_stage'] == 'base' for e in state['evidence']['entries'])
    assert cli('phase', 'IMPLEMENTATION')[0] == 0
    before = al_store.state_path(sid).read_bytes()
    assert cli('gates', 'run', '--stage', 'base')[0] != 0
    assert al_store.state_path(sid).read_bytes() == before
    assert register_task(cli, tmp_path, 'A', ['svc'])[0] == 0
    assert cli('evidence', 'run', '--area', 'unmapped', '--kind', 'check', '--scope', 'docs', '--', 'python3', '-V')[0] != 0
    assert report(cli, tmp_path, 'blocker', {'id': 'design', 'tasks': ['A'], 'question': 'Choose architecture', 'context': 'Explicit authority required', 'alternatives': ['one', 'two'], 'recommendation': 'one'})[0] == 0
    before = al_store.state_path(sid).read_bytes()
    assert cli('gates', 'run', '--stage', 'base')[0] != 0
    assert al_store.state_path(sid).read_bytes() == before
    controller.conn.close()


@pytest.mark.parametrize('change', ['terminal', 'sealed-bytes'])
def test_pre_task_base_gate_rechecks_exact_harness_authority(cli, tmp_path, repo, monkeypatch, change):
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch, bind_source=False)
    _planning_gate(cli, tmp_path, sid)
    from harness.execution_store import get_execution
    assert get_execution(controller.conn, eid)['status'] != 'Completed'
    if change == 'terminal':
        controller.conn.execute("UPDATE executions SET status='Completed' WHERE id=?", (eid,))
    else:
        from pathlib import Path
        approval = get_execution(controller.conn, eid)['binding']['approval_id']
        path = Path(controller.conn.execute('SELECT path FROM approvals WHERE id=?', (approval,)).fetchone()['path'])
        path.write_bytes(path.read_bytes() + b'\nUnapproved changed requirement.\n')
    before = al_store.state_path(sid).read_bytes()
    result = cli('gates', 'run', '--stage', 'base')
    assert result[0] != 0 and 'authority' in str(result), result
    assert al_store.state_path(sid).read_bytes() == before
    controller.conn.close()


def test_pre_worker_bootstrap_rejects_foreign_binding_and_dispatch_spec(cli, tmp_path, repo, monkeypatch):
    import copy
    import al_execution_tasks as tasks
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch, bind_source=False)
    gate = _planning_gate(cli, tmp_path, sid)
    state = al_store.load(sid)
    context = {'stage': 'base', 'manifest': state['gate_manifests'][-1]['sha256'], 'gate': gate['id']}
    spec = {'area': 'gate-base-' + gate['id'], 'kind': 'check', 'tier': 'focused', 'argv': gate['argv'],
            'cwd': gate['cwd'], 'scope': gate['scope'], 'env': gate['env_names'], 'sources': gate.get('sources', [])}
    before = al_store.state_path(sid).read_bytes()
    foreign = copy.deepcopy(state)
    foreign['integration']['binding']['approval_id'] += 1
    with pytest.raises(SystemExit, match='authority'):
        tasks.admit_check(foreign, spec, gate_context=context)
    for key, value in [('argv', ['python3', '-V']), ('scope', ['docs']), ('env', ['UNBOUND'])]:
        with pytest.raises(SystemExit, match='unmapped'):
            tasks.admit_check(state, dict(spec, **{key: value}), gate_context=context)
    for key, value in [('stage', 'candidate'), ('manifest', 'foreign')]:
        with pytest.raises(SystemExit, match='unmapped'):
            tasks.admit_check(state, spec, gate_context=dict(context, **{key: value}))
    historical = copy.deepcopy(state)
    historical['integration']['graph_revision'] = 1
    with pytest.raises(SystemExit, match='unmapped'):
        tasks.admit_check(historical, spec, gate_context=context)
    assert al_store.state_path(sid).read_bytes() == before
    controller.conn.close()
