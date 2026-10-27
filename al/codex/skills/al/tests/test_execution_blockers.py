import al_execution_tasks as T
import al_store
import pytest

from execution_helpers import bound, register_task, report
from launcher_context import admit_planning


def test_blocked_task_and_dependents_but_independent_admission(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch)
    for tid, files, deps in [('A', ['svc'], []), ('C', ['web'], ['A']), ('B', ['docs'], [])]:
        code, out, err = register_task(cli, tmp_path, tid, files, deps)
        assert code == 0, (code, out, err)
    code, _, _ = report(cli, tmp_path, 'blocker', {'id': 'design', 'tasks': ['A'], 'question': 'Choose architecture', 'context': 'Explicit product authority required', 'alternatives': ['one', 'two'], 'recommendation': 'one'})
    assert code == 0
    for tid in ('A', 'C'):
        code, _, _ = cli('integration', 'task-admit', '--task', tid)
        assert code != 0
    code, out, err = cli('integration', 'task-admit', '--task', 'B')
    assert code == 0, (code, out, err)
    st = al_store.load(sid)
    assert st['status'] == 'RUNNING' and st['integration']['tasks']['B']['status'] == 'Waiting'
    with pytest.raises(SystemExit, match='ambiguous'):
        T.scope_tasks(st, ['unknown'])
    with pytest.raises(SystemExit, match='blocked'):
        T.admit_check(st, {'scope': ['.']})
    # Independent B can finish through normal writer, check and task guards.
    from evidence_helpers import plan_na_gates
    import sys
    assert cli('spec-gate', 'SPEC_READY')[0] == 0
    assert cli('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')[0] == 0
    plan_na_gates(cli, tmp_path, ('docs/readme.md',))
    admit_planning(cli)
    assert cli('phase', 'IMPLEMENTATION')[0] == 0
    assert report(cli, tmp_path, 'agent-register', {'id':'worker-B','name':'B engineer','role':'al-engineer','task':'B'})[0] == 0
    assert report(cli, tmp_path, 'agent-update', {'id':'worker-B','status':'Running'})[0] == 0
    assert cli('integration','task-start','--task','B')[0] == 0
    code, out, err = cli('writer','start','--id','writer-B','--role','al-engineer','--files','docs/readme.md','--task','B','--agent','worker-B')
    assert code == 0, (code,out,err)
    assert cli('writer','finish','--id','writer-B','--outcome','success')[0] == 0
    assert cli('evidence','plan','--area','B-test','--tier','focused','--scope','docs','--',sys.executable,'-c',"from pathlib import Path; assert Path('docs/readme.md').is_file(); print('1 passed')")[0] == 0
    code,out,err=cli('evidence','run','--area','B-test')
    assert code == 0, (code,out,err)
    code,out,err=cli('integration','task-finish','--task','B')
    assert code == 0, (code,out,err)
    assert al_store.load(sid)['integration']['tasks']['B']['status'] == 'Completed'
    controller.conn.close()


def test_graph_and_task_identity_cannot_be_rebound(cli, tmp_path, repo, monkeypatch):
    controller, _, _ = bound(cli, tmp_path, repo, monkeypatch)
    assert register_task(cli, tmp_path, 'A', ['svc'])[0] == 0
    assert register_task(cli, tmp_path, 'A', ['web'])[0] != 0
    assert register_task(cli, tmp_path, 'B', ['svc/app.py'])[0] != 0
    assert register_task(cli, tmp_path, 'C', ['web'], ['missing'])[0] != 0
    assert register_task(cli, tmp_path, 'C', ['web'], ['C'])[0] != 0
    controller.conn.close()


def test_preflight_runs_in_a_bound_run_but_other_scopeless_checks_stay_unmapped(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch)
    assert register_task(cli, tmp_path, 'A', ['svc'])[0] == 0
    # Tool availability touches no repository path, so no execution task has to own it.
    code, out, err = cli('preflight', '--name', 'documentation-tools', '--check', 'cmd:git')
    assert code == 0, (code, out, err)
    entry = al_store.load(sid)['evidence']['entries'][-1]
    assert entry['kind'] == 'preflight' and entry['status'] == 'PASS' and entry['scope'] == []
    with pytest.raises(SystemExit, match='ambiguous'):
        T.admit_check(al_store.load(sid), {'kind': 'check', 'scope': []})
    controller.conn.close()


@pytest.mark.parametrize('action',['blocker','task-register','agent-register','agent-update','task-progress','graph-update','handoff'])
def test_missing_integration_fields_have_structured_recovery_without_mutation(cli,tmp_path,repo,monkeypatch,action):
    import json
    controller,_,sid=bound(cli,tmp_path,repo,monkeypatch)
    path=al_store.state_path(sid)
    before=path.read_bytes()
    code,out,err=report(cli,tmp_path,action,{})
    message=str(code)+out+err
    assert code!=0 and 'input_schema' in message and 'missing' in message
    assert 'KeyError' not in message and 'Traceback' not in message
    assert path.read_bytes()==before
    controller.conn.close()
