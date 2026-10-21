"""Fixture observations read fresh committed artifacts without replacing CLI guards."""
import json
import pytest
import al_store
from hook_regression_helpers import invoke


def snapshot():
    root=al_store.RUNTIME_DIR
    return {str(p.relative_to(root)):(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def start(cli, session=None):
    args=('--session',session) if session else ()
    invoke(cli,*args,'start','--spec','Observe actual committed fixture state without changing runtime files.')
    return cli.state(session=session)


def test_fresh_commits_and_returned_object_mutation_cannot_reuse_observation(cli):
    first=start(cli)
    first['completed_phases'].append('forged')
    assert 'forged' not in cli.state()['completed_phases']
    invoke(cli,'spec-gate','SPEC_READY')
    current=cli.state()
    assert current['spec_status']=='SPEC_READY' and first['spec_status'] is None
    assert current['run_id']==first['run_id']


def test_observation_missing_state_and_pointer_never_creates_files(cli):
    before=snapshot()
    with pytest.raises(ValueError):cli.state()
    assert snapshot()==before
    state=start(cli)
    al_store.CURRENT.unlink(missing_ok=True)
    before=snapshot()
    if cli.fixed_session:
        assert cli.state()['session_id']==state['session_id']
    else:
        with pytest.raises(ValueError,match='current pointer'):cli.state()
    assert snapshot()==before
    assert cli.state(session=state['session_id'])['run_id']==state['run_id']
    path=al_store.state_path(state['session_id']);path.unlink()
    before=snapshot()
    with pytest.raises(ValueError,match='committed state'):cli.state(session=state['session_id'])
    assert snapshot()==before


def test_explicit_sessions_keep_platform_defaults_and_exact_started_runs(cli):
    first=start(cli);other=start(cli,'explicit-observer-session')
    assert first['session_id']!=other['session_id'] and first['run_id']!=other['run_id']
    assert cli.state(session=first['session_id'])['run_id']==first['run_id']
    assert cli.state(session=other['session_id'])['run_id']==other['run_id']
    assert cli.state()['session_id']==(cli.fixed_session or other['session_id'])
    with pytest.raises(ValueError):cli.state(session='missing-session')


@pytest.mark.parametrize('damage',['json','run','session','repository','duplicate'])
def test_corrupt_or_foreign_committed_observation_refuses_without_writes(cli,damage):
    state=start(cli);path=al_store.state_path(state['session_id'])
    if damage=='json':path.write_text('{')
    elif damage=='duplicate':path.write_text('{"session_id":"one","session_id":"two"}')
    else:
        if damage=='run':state['run_id']='foreign-run'
        elif damage=='session':state['session_id']='foreign-session'
        else:state['identity']['root']='/foreign/repository'
        path.write_text(json.dumps(state))
    before=snapshot()
    with pytest.raises(ValueError):cli.state(session=state['session_id'] if damage!='session' else None)
    assert snapshot()==before


def test_actual_public_show_still_refuses_changed_cwd_identity(cli,repo,monkeypatch):
    state=start(cli);path=al_store.state_path(state['session_id']);before=path.read_bytes()
    child=repo/'nested-cwd';child.mkdir();monkeypatch.chdir(child)
    code,out,err=invoke(cli,'show',ok=False)
    assert 'pinned repository/cwd mismatch' in str(code)+out+err
    assert path.read_bytes()==before
