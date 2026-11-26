"""Isolation shares effective-parent overlay while preserving ordinary staging."""
import json
import pytest
import al_scope as S
from evidence_helpers import git
from gitlink_helpers import gitlink,commit_child
from hook_regression_helpers import invoke


def test_isolation_uses_effective_pointer_without_copying_child_and_preserves_ordinary_index(cli,repo,tmp_path,gitlink,monkeypatch):
    import al_harness_process
    import al_process_observation
    launched=[]
    original=al_harness_process.popen
    def observed(argv,**kwargs):
        reference=kwargs['process_observation']
        proc=original(argv,**kwargs)
        launched.append((reference,proc.pid))
        return proc
    path,_,_=gitlink
    invoke(cli,'start','--spec','Isolate effective parent entries without recreating child contents or ordinary staging.')
    b=commit_child(path,'B');git(repo,'add','svc/vendor');c=commit_child(path,'C')
    (repo/'svc/app.py').write_text('ordinary unstaged')
    destination=tmp_path/'isolation'
    monkeypatch.setattr(al_harness_process,'popen',observed)
    invoke(cli,'isolate','--authorized','--path',str(destination))
    assert S.git_bytes('ls-files','--stage','svc/vendor',root=destination).decode().startswith('160000 '+c+' ')
    assert not list((destination/'svc/vendor').iterdir())
    assert (destination/'svc/app.py').read_text()=='ordinary unstaged'
    assert S.git_bytes('show',':svc/app.py',root=destination)==b'x = 1\n'
    assert S.git_bytes('ls-files','--stage','svc/vendor').decode().startswith('160000 '+b+' ')
    entry=cli.state()['evidence']['entries'][-1]
    assert len(launched)==1 and launched[0]==(entry['process_observation'],entry['pid'])
    reference=entry['process_observation']
    assert reference['mode']=='local' and reference['domain']==al_process_observation.domain()
    assert reference['owner']['pid']==entry['owner_pid']
    assert len(reference['children'])==1
    if reference['children'][0] is not None:
        assert reference['children'][0]['pid']==entry['pid']


@pytest.mark.parametrize('kind',['dirty','inspection-failed'])
def test_uncaptured_child_isolation_refuses_before_destination_creation(cli,repo,tmp_path,gitlink,kind):
    path,_,_=gitlink
    invoke(cli,'start','--spec','Reject isolation before creation when child work cannot be captured.')
    if kind=='dirty':(path/'file').write_text('dirty')
    else:(path/'.git').write_text('gitdir: missing\n')
    destination=tmp_path/'never'
    result=cli('isolate','--authorized','--path',str(destination))
    assert result[0]!=0 and 'uncaptured child' in str(result) and not destination.exists()


@pytest.mark.parametrize('inspection_error',[PermissionError,OSError])
def test_postspawn_identity_inspection_failure_keeps_real_isolation_child_owned(cli,repo,tmp_path,monkeypatch,inspection_error):
    import al_harness_process
    import al_process_observation
    invoke(cli,'start','--spec','Keep ownership and wait when child identity inspection is unavailable.')
    (repo/'svc/app.py').write_text('preserved candidate overlay\n')
    destination=tmp_path/'unknown-child-observation'
    launched=[]
    original_popen=al_harness_process.popen
    original_identity=al_process_observation.identity
    private='never-publish-identity-inspection-payload'
    def launch(argv,**kwargs):
        proc=original_popen(argv,**kwargs)
        launched.append(proc)
        return proc
    def inspect(pid):
        if any(proc.pid==pid for proc in launched):
            raise inspection_error(private)
        return original_identity(pid)
    monkeypatch.setattr(al_harness_process,'popen',launch)
    monkeypatch.setattr(al_process_observation,'identity',inspect)
    try:
        invoke(cli,'isolate','--authorized','--path',str(destination))
        assert len(launched)==1 and launched[0].poll()==0 and launched[0].returncode==0
        assert (destination/'svc/app.py').read_text()=='preserved candidate overlay\n'
        entry=cli.state()['evidence']['entries'][-1]
        assert entry['status']=='PASS' and entry['pid']==launched[0].pid
        assert entry['process_observation']['children']==[None]
        assert al_process_observation.observe(entry)['checks']=='unknown'
        assert private not in json.dumps(entry)
        with open(entry['log']) as stream:assert private not in stream.read()
    finally:
        for proc in launched:
            if proc.poll() is None:
                import al_evidence
                al_evidence._stop(proc)


@pytest.mark.parametrize('mode',['local','harness'])
def test_isolation_caller_forwards_allocated_reference_and_observes_child_unit(monkeypatch,repo,tmp_path,mode):
    """Routing unit only: allocation and launcher are stubs, not host-authority proof."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    import al_harness_process
    import al_isolation as isolation
    import al_process_observation
    state={'phase':'PLANNING','run_id':'run','baseline_head':'revision'}
    reference={'schema_version':1,'mode':mode,'unit-selector':'allocated-reference'}
    entry={'id':'E1','argv':['git','worktree','add'],'fp_before':'candidate',
           'process_observation':reference}
    capture={'files':[],'fingerprint':'candidate'}
    events=[]
    proc=SimpleNamespace(pid=12345,wait=lambda timeout:0)
    monkeypatch.setattr(isolation.al_store,'load',lambda sid:state)
    monkeypatch.setattr(isolation.al_store,'locked',lambda sid:nullcontext())
    monkeypatch.setattr(isolation.al_store,'save',lambda *args:None)
    monkeypatch.setattr(isolation.S,'capture_candidate',lambda st:capture)
    monkeypatch.setattr(isolation.S,'child_content_gaps',lambda value:[])
    monkeypatch.setattr(isolation.E,'_create',lambda *args,**kwargs:entry)
    monkeypatch.setattr(isolation.E,'_commit',lambda *args:events.append('commit'))
    monkeypatch.setattr(isolation.E,'_fingerprint',lambda value:'candidate')
    monkeypatch.setattr(isolation.L,'log_dir',lambda sid:tmp_path)
    monkeypatch.setattr(isolation.C,'overlay_candidate',lambda *args,**kwargs:None)
    def launch(argv,**kwargs):
        assert argv is entry['argv'] and kwargs['process_observation'] is reference
        assert kwargs['start_new_session'] is True and kwargs['cwd']==repo
        events.append('launch')
        return proc
    def observed(value,child):
        assert value is entry and child is proc and value['process_observation'] is reference
        events.append('observe-child')
    monkeypatch.setattr(al_harness_process,'popen',launch)
    monkeypatch.setattr(al_process_observation,'spawned',observed)
    isolation.cmd_isolate(SimpleNamespace(path=str(tmp_path/'unit-isolation'),authorized=True,timeout=5),'session')
    assert events[:3]==['launch','observe-child','commit']
    assert entry['pid']==proc.pid and entry['status']=='PASS'
    assert state['isolations'][0]['evidence']=='E1'
