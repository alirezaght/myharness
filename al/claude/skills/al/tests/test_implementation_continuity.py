"""Implementation advances through real guards without inventing verification proof."""
import copy
import json
from pathlib import Path

import pytest
import al_artifacts as A
import al_store as S
from execution_helpers import bound, register_task, report


def ok(cli, *args):
    result = cli(*args)
    assert result[0] == 0, result
    return result


def setup(cli, tmp_path, repo, monkeypatch):
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch)
    ok(cli, 'spec-gate', 'SPEC_READY')
    ok(cli, 'classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')
    assert register_task(cli, tmp_path, 'A', ['svc'])[0] == 0
    assert register_task(cli, tmp_path, 'B', ['web'], ['A'])[0] == 0
    assert register_task(cli, tmp_path, 'C', ['docs'])[0] == 0
    assert register_task(cli, tmp_path, 'D', ['consumer'], ['B'])[0] == 0
    ok(cli, 'phase', 'IMPLEMENTATION')
    return controller, eid, sid


def worker(cli, tmp_path, tid='A', aid='engineer', wid='write-A', files='svc/app.py'):
    assert report(cli, tmp_path, 'agent-register', {'id': aid, 'name': aid, 'role': 'al-engineer', 'task': tid})[0] == 0
    assert report(cli, tmp_path, 'agent-update', {'id': aid, 'status': 'Running'})[0] == 0
    ok(cli, 'integration', 'task-start', '--task', tid)
    ok(cli, 'writer', 'start', '--id', wid, '--role', 'al-engineer', '--files', files, '--task', tid, '--agent', aid)


def test_pending_coverage_authored_dependency_and_final_guards(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = setup(cli, tmp_path, repo, monkeypatch)
    assert cli('integration', 'task-admit', '--task', 'B')[0] != 0
    worker(cli, tmp_path)
    (repo / 'svc/app.py').write_text('answer = 42\n')
    ok(cli, 'writer', 'finish', '--id', 'write-A', '--outcome', 'success')
    assert report(cli, tmp_path, 'agent-update', {'id': 'engineer', 'status': 'Completed'})[0] == 0
    request = tmp_path / 'authored.json'
    request.write_text(json.dumps({'schema_version': 1, 'task_id': 'A', 'output_refs': [{'kind': 'source', 'path': 'svc/app.py'}]}))
    ok(cli, 'integration', 'task-authored', '--task', 'A', '--file', str(request))
    st = S.load(sid)
    task = st['integration']['tasks']['A']
    assert task['status'] != 'Completed' and task['authored_output']['writer_ids'] == ['write-A']
    assert task['authored_output']['producer_invocation_ids'] == ['engineer']
    assert st['implementation_readiness']['obligations'] and st['proof_admission']['status'] == 'pending'
    from al_execution_outputs import current
    original_receipt = copy.deepcopy(task['authored_output'])
    worker(cli, tmp_path, tid='C', aid='independent', wid='docs-writer', files='docs/readme.md')
    (repo / 'docs/readme.md').write_text('Independent documentation output.\n')
    ok(cli, 'writer', 'finish', '--id', 'docs-writer', '--outcome', 'success')
    assert report(cli, tmp_path, 'agent-update', {'id': 'independent', 'status': 'Completed'})[0] == 0
    assert current(S.load(sid), 'A')
    assert S.load(sid)['integration']['tasks']['A']['authored_output'] == original_receipt
    ok(cli, 'integration', 'task-admit', '--task', 'B')
    worker(cli, tmp_path, tid='B', aid='consumer', wid='consumer-writer', files='web/app.js')
    assert report(cli, tmp_path, 'agent-register', {
        'id': 'rewrite', 'name': 'Fresh rewrite producer', 'role': 'al-engineer', 'task': 'A',
    })[0] == 0
    assert report(cli, tmp_path, 'agent-update', {'id': 'rewrite', 'status': 'Running'})[0] == 0
    ok(cli, 'integration', 'task-start', '--task', 'A')
    assert cli('writer', 'start', '--id', 'too-early', '--role', 'al-engineer',
               '--files', 'svc/app.py', '--task', 'A', '--agent', 'rewrite')[0] != 0
    assert 'authored_output' in S.load(sid)['integration']['tasks']['A']
    (repo / 'web/app.js').write_text('const consumer = 42;\n')
    ok(cli, 'writer', 'finish', '--id', 'consumer-writer', '--outcome', 'success')
    assert report(cli, tmp_path, 'agent-update', {'id': 'consumer', 'status': 'Completed'})[0] == 0
    request_b = tmp_path / 'authored-b.json'
    request_b.write_text(json.dumps({'schema_version': 1, 'task_id': 'B',
        'output_refs': [{'kind': 'source', 'path': 'web/app.js'}]}))
    ok(cli, 'integration', 'task-authored', '--task', 'B', '--file', str(request_b))
    assert current(S.load(sid), 'B')
    (repo / 'svc/app.py').write_text('external upstream drift = 43\n')
    assert not current(S.load(sid), 'B')
    (repo / 'svc/app.py').write_text('answer = 42\n')
    ok(cli, 'integration', 'task-admit', '--task', 'D')
    for args in [('integration', 'task-finish', '--task', 'A'), ('review-start',), ('approved',)]:
        assert cli(*args)[0] != 0
    # A producer rewrite invalidates its output and dependent admission before editing.
    ok(cli, 'writer', 'start', '--id', 'write-again', '--role', 'al-engineer',
       '--files', 'svc/app.py', '--task', 'A', '--agent', 'rewrite')
    final_state = S.load(sid)
    assert 'authored_output' not in final_state['integration']['tasks']['A']
    assert 'admission' not in final_state['integration']['tasks']['B']
    assert 'admission' not in final_state['integration']['tasks']['D']
    assert 'authored_output' not in final_state['integration']['tasks']['B']
    ok(cli, 'writer', 'finish', '--id', 'write-again', '--outcome', 'success')
    controller.conn.close()


def test_typed_decision_only_pauses_affected_tasks(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = setup(cli, tmp_path, repo, monkeypatch)
    data = {'id': 'choice', 'tasks': ['A'], 'kind': 'developer_engineering_decision',
            'question': 'Choose a persistence contract', 'context': 'Material engineering choice',
            'alternatives': ['one', 'two'], 'recommendation': 'one'}
    assert report(cli, tmp_path, 'blocker', data)[0] == 0
    assert cli('integration', 'task-admit', '--task', 'A')[0] != 0
    ok(cli, 'integration', 'task-admit', '--task', 'C')
    bad = data | {'id': 'tooling', 'kind': 'tool_unavailable'}
    before = S.state_path(sid).read_bytes()
    assert report(cli, tmp_path, 'blocker', bad)[0] != 0
    assert S.state_path(sid).read_bytes() == before
    controller.conn.close()


def test_issue_is_not_a_question_and_resolution_needs_fresh_evidence(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = setup(cli, tmp_path, repo, monkeypatch)
    diagnostic = tmp_path / 'diagnostic.md'
    diagnostic.write_text('Synthetic provider not available; repair with configured provider.')
    ref = json.loads(ok(cli, 'artifact', 'record', '--file', str(diagnostic), '--kind', 'operational-diagnostic')[1])
    issue = {'id': 'provider', 'revision': 1, 'cause': 'provider_unavailable', 'task_ids': ['A'],
             'paths': ['svc'], 'capability_or_check_ref': 'provider-health', 'diagnostic_ref': ref,
             'state': 'Repairing', 'next_action': 'Retry configured provider health check',
             'observed_binding': S.load(sid)['integration']['binding']}
    assert report(cli, tmp_path, 'issue-update', issue)[0] == 0
    ok(cli, 'integration', 'task-admit', '--task', 'A')
    st = S.load(sid)
    assert not st['integration']['blockers'] and st['operational_issues']['provider']['state'] == 'Repairing'
    invalid = issue | {'revision': 2, 'state': 'Resolved', 'resolution_ref': {'evidence_id': 'E999', 'diagnostic_sha256': ref['sha256']}}
    assert report(cli, tmp_path, 'issue-update', invalid)[0] != 0
    assert report(cli, tmp_path, 'issue-update', issue | {'revision': 2, 'cause': 'invented'})[0] != 0
    assert S.load(sid)['operational_issues']['provider'] == issue
    import sys
    ok(cli, 'evidence', 'plan', '--area', 'provider-health', '--scope', 'svc', '--',
       sys.executable, '-c', 'print("1 passed")')
    ok(cli, 'evidence', 'run', '--area', 'provider-health')
    entry = S.load(sid)['evidence']['entries'][-1]
    diagnostic.write_text('Fresh synthetic provider health observation after successful repair.')
    fresh = json.loads(ok(cli, 'artifact', 'record', '--file', str(diagnostic), '--kind', 'operational-diagnostic')[1])
    resolved = issue | {'revision': 2, 'state': 'Resolved', 'diagnostic_ref': fresh,
                        'resolution_ref': {'evidence_id': entry['id'], 'diagnostic_sha256': fresh['sha256']}}
    result = report(cli, tmp_path, 'issue-update', resolved)
    assert result[0] == 0, result
    assert S.load(sid)['operational_issues']['provider']['state'] == 'Resolved'
    assert not S.load(sid)['integration']['blockers']
    controller.conn.close()


def test_stale_output_uncertain_writer_and_foreign_receipt_refuse(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = setup(cli, tmp_path, repo, monkeypatch)
    worker(cli, tmp_path)
    (repo / 'svc/app.py').write_text('answer = 9\n')
    request = tmp_path / 'authored.json'
    request.write_text(json.dumps({'schema_version': 1, 'task_id': 'A', 'output_refs': [{'kind': 'source', 'path': 'svc/app.py'}]}))
    assert cli('integration', 'task-authored', '--task', 'A', '--file', str(request))[0] != 0
    ok(cli, 'writer', 'finish', '--id', 'write-A', '--outcome', 'failed')
    assert report(cli, tmp_path, 'agent-update', {'id': 'engineer', 'status': 'Failed'})[0] == 0
    assert cli('integration', 'task-authored', '--task', 'A', '--file', str(request))[0] != 0
    st = S.load(sid)
    from al_execution_outputs import _source
    with pytest.raises(SystemExit, match='protected'):
        _source('.env')
    with pytest.raises(SystemExit, match='specific'):
        _source('../escape')
    from al_new_origin import require_implementation_ready
    altered = copy.deepcopy(st)
    altered['run_id'] = 'foreign'
    with pytest.raises(SystemExit):
        require_implementation_ready(altered, paths=['svc/app.py'])
    controller.conn.close()


def test_recovery_marker_survives_real_phase_advancement(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch)
    ok(cli, 'spec-gate', 'SPEC_READY')
    ok(cli, 'classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')
    assert register_task(cli, tmp_path, 'A', ['svc', 'web', 'docs', '.gitignore', 'pyproject.toml'])[0] == 0
    st = S.load(sid)
    from al_recovery import checkpoint
    ref = checkpoint(st, sid, 'Synthetic retained recovery', 'Continue scoped implementation')
    original = {'checkpoint': ref['id'], 'phase': 'PLANNING', 'superseded_manifest': None,
                'reasons': ['coverage unknown'], 'replacement_manifest': None}
    st['gate_recovery'] = copy.deepcopy(original)
    S.save(sid, st)
    checkpoint_bytes = A.read(ref)
    ok(cli, 'phase', 'IMPLEMENTATION')
    current = S.load(sid)
    from al_gate_recovery import marker
    assert marker(current)['phase'] == 'PLANNING'
    assert current['gate_recovery']['checkpoint'] == original['checkpoint']
    assert A.read(ref) == checkpoint_bytes
    assert 'gate_recovery_history' not in current
    assert cli('review-start')[0] != 0
    from evidence_helpers import plan_na_gates
    from launcher_context import admit_planning
    plan_na_gates(cli, tmp_path, ('svc/app.py',))
    admit_planning(cli)
    from al_gate_recovery import ready
    repaired = S.load(sid)
    ready(repaired)
    assert marker(repaired)['checkpoint'] == ref['id']
    controller.conn.close()


def test_actual_incomplete_manifest_permits_scoped_authoring(cli, tmp_path, repo):
    from test_service_coverage import seed, start, discover, plan, put, unit
    seed(repo)
    unit(repo, 'services/b', check=False)
    put(repo, 'packages/shared/src/common.py', '__import__(unknown_name)\n')
    start(cli)
    _, manifest = discover(cli, tmp_path, ['packages/shared/src/common.py'])
    assert manifest['service_coverage']['assessment']['outcome'] == 'incomplete'
    plan(cli, repo, tmp_path, ['packages/shared/src/common.py'])
    ok(cli, 'phase', 'IMPLEMENTATION')
    ok(cli, 'writer', 'start', '--id', 'incomplete', '--role', 'al-engineer',
       '--files', 'packages/shared/src/common.py')
    (repo / 'packages/shared/src/common.py').write_text('answer = 42\n')
    ok(cli, 'writer', 'finish', '--id', 'incomplete', '--outcome', 'success')
    state = json.loads(ok(cli, 'show')[1])
    assert state['implementation_readiness']['obligations']
    assert not state['integration']['blockers'] if 'integration' in state else True
    assert cli('review-start')[0] != 0
    assert cli('approved')[0] != 0


def test_semantic_ambiguity_is_debt_but_corrupt_attribution_refuses(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = setup(cli, tmp_path, repo, monkeypatch)
    worker(cli, tmp_path)
    (repo / 'svc/app.py').write_text('answer = 12\n')
    ok(cli, 'writer', 'finish', '--id', 'write-A', '--outcome', 'success')
    (repo / 'svc/app.py').write_text('external = 13\n')
    from al_task_attribution import classify_changes
    from al_new_origin import require_implementation_ready
    current = S.load(sid)
    classified = classify_changes(current)
    assert 'svc/app.py' in classified['ambiguous']
    assert 'svc/app.py' in classified['discovery_paths']
    readiness = require_implementation_ready(current, paths=['svc/app.py'])
    assert any('attribution analysis incomplete' in v for v in readiness['obligations'])
    ref = current['task_attribution']['contributions'][0]
    Path(ref['stored_path']).write_text('corrupt synthetic attribution')
    with pytest.raises(SystemExit, match='altered'):
        require_implementation_ready(current, paths=['svc/app.py'])
    controller.conn.close()


def test_verified_completion_is_current_and_distinct_from_authored(cli, tmp_path, repo, monkeypatch):
    import sys
    from evidence_helpers import plan_na_gates
    from launcher_context import admit_planning
    from al_execution_outputs import current
    from al_execution_tasks import completion_gaps
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch)
    ok(cli, 'spec-gate', 'SPEC_READY')
    ok(cli, 'classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')
    assert register_task(cli, tmp_path, 'A', ['svc', 'web', 'docs', '.gitignore', 'pyproject.toml'])[0] == 0
    plan_na_gates(cli, tmp_path, ('svc/app.py',))
    admit_planning(cli)
    ok(cli, 'phase', 'IMPLEMENTATION')
    worker(cli, tmp_path)
    (repo / 'svc/app.py').write_text('answer = 42\n')
    ok(cli, 'writer', 'finish', '--id', 'write-A', '--outcome', 'success')
    assert report(cli, tmp_path, 'agent-update', {'id': 'engineer', 'status': 'Completed'})[0] == 0
    ok(cli, 'gates', 'run', '--stage', 'candidate')
    ok(cli, 'evidence', 'plan', '--area', 'unit', '--scope', 'svc', '--', sys.executable, '-c', 'print("1 passed")')
    ok(cli, 'evidence', 'run', '--area', 'unit')
    ok(cli, 'integration', 'task-finish', '--task', 'A')
    saved = S.load(sid)
    assert current(saved, 'A')
    assert saved['integration']['tasks']['A']['completion_binding']['outputs']
    (repo / 'svc/app.py').write_text('external_drift = 43\n')
    assert not current(saved, 'A')
    assert completion_gaps(saved)
    old_revision = saved['integration']['tasks']['A']['revision']
    old_completion = copy.deepcopy(saved['integration']['tasks']['A']['completion_binding'])
    ok(cli, 'integration', 'task-admit', '--task', 'A')
    reopened = S.load(sid)['integration']['tasks']['A']
    assert reopened['status'] == 'Waiting' and reopened['revision'] == old_revision + 1
    assert reopened['completion_history'][-1] == old_completion
    assert S.load(sid)['integration']['agents']['engineer']['status'] == 'Completed'
    assert report(cli, tmp_path, 'agent-update', {'id': 'engineer', 'status': 'Running'})[0] != 0
    controller.conn.close()
