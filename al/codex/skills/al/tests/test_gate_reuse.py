"""Public stage runs preserve obligations while reusing only clean executions."""
import contextlib
import copy
import json
import sys
from pathlib import Path
import pytest
import al_checkouts as C
import al_gate_reuse as R
import al_gates as Gates
import al_ledger as L
import al_store
from batch13_helpers import start,manifest
from hook_regression_helpers import invoke


def two_gates(cli,tmp_path,argv,second=None,remote=False,anticipated=()):
    manifest(cli,tmp_path,argv,category='ci' if remote else 'contracts',remote=remote,anticipated=anticipated)
    st=json.loads(cli('show')[1]);m=Gates.manifest(st)
    first=next(g for g in m['gates'] if g['id']=='boundary')
    target=copy.deepcopy(first);target.update(id='duplicate')
    if second:target.update(second)
    m['gates'].append(target)
    file=tmp_path/'two-gates.json';file.write_text(json.dumps(m))
    invoke(cli,'gates','plan','--file',str(file))


def boundary_runs(state):
    return [run for run in state['gate_runs'] if run['gate'] in {'boundary','duplicate'}]


def evidence_for_gates(state, expected, stage):
    manifest = state['gate_manifests'][-1]['sha256']
    associations = {
        row['gate']: row for row in state['gate_runs']
        if row['gate'] in expected and row['stage'] == stage and row['manifest'] == manifest
    }
    assert set(associations) == set(expected)
    entries = {entry['id']: entry for entry in state['evidence']['entries']}
    return {gate: (associations[gate], entries[associations[gate]['evidence']])
            for gate in expected}


def counted_checkout(monkeypatch):
    real=C.materialize;roots=[]
    @contextlib.contextmanager
    def materialize(*args,**kwargs):
        with real(*args,**kwargs) as checkout:
            roots.append(str(checkout['root']));yield checkout
    monkeypatch.setattr(C,'materialize',materialize)
    return roots


def test_ignored_cache_requires_fresh_checkout_and_execution(cli,repo,tmp_path,monkeypatch):
    (repo/'.gitignore').write_text('cache/\n');start(cli,repo);marker=tmp_path/'executions'
    code='from pathlib import Path; assert not Path("cache").exists(); Path("cache").mkdir(); Path("cache/data").write_text("cache"); p=Path('+repr(str(marker))+'); p.write_text(p.read_text()+"x" if p.exists() else "x")'
    two_gates(cli,tmp_path,[sys.executable,'-c',code]);roots=counted_checkout(monkeypatch)
    invoke(cli,'gates','run','--stage','candidate')
    state=json.loads(cli('show')[1]);entries=state['evidence']['entries'][-2:]
    assert marker.read_text()=='xx' and len(set(roots))==2 and not (repo/'cache').exists()
    assert all(e['status']=='PASS' and not e.get('reuse') and L.entry_verdict(state,e)[0]=='VALID' for e in entries)


@pytest.mark.parametrize('ignored',[False,True])
def test_post_execution_generated_symlink_is_discarded_only_when_ignored(cli,repo,tmp_path,monkeypatch,ignored):
    (repo/'.gitignore').write_text('cache\n' if ignored else '')
    start(cli,repo);marker=tmp_path/'executed';outside=tmp_path/'outside';outside.mkdir()
    (outside/'sentinel').write_text('preserve')
    code='from pathlib import Path; assert not Path("cache").is_symlink(); Path("cache").symlink_to('+repr(str(outside))+'); p=Path('+repr(str(marker))+'); p.write_text(p.read_text()+"x" if p.exists() else "x")'
    two_gates(cli,tmp_path,[sys.executable,'-c',code]);roots=counted_checkout(monkeypatch)
    invoke(cli,'gates','run','--stage','candidate',ok=ignored)
    state=json.loads(cli('show')[1]);entries=state['evidence']['entries']
    assert marker.read_text()==('xx' if ignored else 'x')
    assert entries[-1]['status']==('PASS' if ignored else 'FAIL')
    assert len(boundary_runs(state))==(2 if ignored else 1) and not (repo/'cache').exists()
    assert len(set(roots))==(2 if ignored else 1) and not any(e.get('reuse') for e in entries)
    assert (outside/'sentinel').read_text()=='preserve'


@pytest.mark.parametrize('code',[ 'from pathlib import Path; Path("svc/app.py").write_text("changed")', 'from pathlib import Path; Path("new").write_text("changed")', 'import subprocess; subprocess.run(["git","config","evil.setting","yes"],check=True)', 'import subprocess; subprocess.run(["git","update-index","--force-remove","svc/app.py"],check=True)', 'from pathlib import Path; Path("ordinary-empty-directory").mkdir()', 'import subprocess; subprocess.run(["git","update-index","--assume-unchanged","svc/app.py"],check=True)' ])
def test_contaminated_execution_never_reused(cli,repo,tmp_path,code):
    start(cli,repo);two_gates(cli,tmp_path,[sys.executable,'-c',code])
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    state=json.loads(cli('show')[1]);assert len(boundary_runs(state))==1
    assert state['evidence']['entries'][-1]['status']=='FAIL'


@pytest.mark.parametrize('field,value',[('argv',['true']),('cwd','svc'),('scope',['web']),('sources',[{'path':'child','kind':'pointer'}]),('env_names',['OTHER']),('execution_kind','remote')])
def test_execution_identity_includes_every_gate_dependency(field,value):
    g={'argv':['echo','ok'],'cwd':'.','scope':['svc'],'sources':[],'env_names':[],'execution_kind':'local'}
    st={'run_id':'run'};m={'revision':1,'base_oid':'base'};capture={'fingerprint':'candidate'}
    key=R.execution_key(st,m,'manifest',g,'candidate',capture,'env',300)
    other=copy.deepcopy(g);other[field]=value
    assert key!=R.execution_key(st,m,'manifest',other,'candidate',capture,'env',300)


@pytest.mark.parametrize('metadata',['index','config'])
def test_metadata_symlinks_are_refused_before_git_reads(cli,repo,tmp_path,metadata):
    start(cli,repo);source=repo/'.git'/metadata;before=source.read_bytes()
    code='from pathlib import Path; p=Path('+repr('.git/'+metadata)+'); p.unlink(); p.symlink_to('+repr(str(source))+')'
    two_gates(cli,tmp_path,[sys.executable,'-c',code])
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    st=json.loads(cli('show')[1]);assert len(boundary_runs(st))==1
    assert st['evidence']['entries'][-1]['status']=='INFRA' and source.read_bytes()==before


def test_different_declared_environment_prevents_execution_reuse(cli,repo,tmp_path):
    start(cli,repo);marker=tmp_path/'executions'
    code='from pathlib import Path; p=Path('+repr(str(marker))+'); p.write_text(p.read_text()+"x" if p.exists() else "x")'
    two_gates(cli,tmp_path,[sys.executable,'-c',code],second={'env_names':['AL_DISTINCT_TEST_ENV']})
    invoke(cli,'gates','run','--stage','candidate')
    st=json.loads(cli('show')[1]);assert marker.read_text()=='xx'
    assert all(not e.get('reuse') for e in st['evidence']['entries'][-2:])


@pytest.mark.parametrize('seam',['source','environment','manifest'])
def test_changed_inputs_between_gates_cannot_reuse(cli,repo,tmp_path,monkeypatch,seam):
    import al_evidence as E
    start(cli,repo);two_gates(cli,tmp_path,[sys.executable,'-c','pass'],second={'env_names':['AL_REUSE_CHANGE']})
    original=E._create;created=[]
    def create(*args,**kwargs):
        entry=original(*args,**kwargs);created.append(entry)
        if entry['area']=='gate-candidate-duplicate':
            if seam=='source':(repo/'svc/app.py').write_text('changed during invocation')
            elif seam=='environment':monkeypatch.setenv('AL_REUSE_CHANGE','changed')
            else:
                sid=args[0];st=al_store.load(sid);st['gate_manifests'][-1]['sha256']='changed';al_store.save(sid,st)
        return entry
    monkeypatch.setattr(E,'_create',create)
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    st=json.loads(cli('show')[1]);entry=st['evidence']['entries'][-1]
    assert entry['status']!='PASS' and entry['pid'] is None


def test_remote_obligations_execute_independently(cli,repo,tmp_path,monkeypatch):
    import al_remote_gates
    from batch13_helpers import stub,body
    st=start(cli,repo);stub(tmp_path,monkeypatch,json.dumps(body(st)).encode())
    two_gates(cli,tmp_path,['glab','api','projects/42/jobs/9'],remote=True)
    real=al_remote_gates.execute_remote;calls=[]
    def execute(*args,**kwargs):
        calls.append(True);return real(*args,**kwargs)
    monkeypatch.setattr(al_remote_gates,'execute_remote',execute)
    roots=counted_checkout(monkeypatch);invoke(cli,'gates','run','--stage','base')
    st=json.loads(cli('show')[1]);results=evidence_for_gates(st,{'boundary','duplicate'},'base')
    assert len(calls)==2 and len(roots)==1
    assert all(run['evidence']==entry['id'] and entry['status']=='PASS' and not entry.get('reuse')
               for run,entry in results.values())


def test_execution_identity_binds_stage_manifest_candidate_environment_and_timeout():
    g={'argv':['true'],'cwd':'.','scope':['svc'],'sources':[],'env_names':[],'execution_kind':'local'}
    args=[{'run_id':'run'},{'revision':1,'base_oid':'base'},'manifest',g,'candidate',{'fingerprint':'candidate'},'env',300]
    key=R.execution_key(*args)
    for position,value in [(0,{'run_id':'other'}),(1,{'revision':2,'base_oid':'base'}),(1,{'revision':1,'base_oid':'other'}),(2,'other'),(4,'base'),(5,{'fingerprint':'other'}),(6,'other'),(7,301)]:
        changed=copy.deepcopy(args);changed[position]=value
        assert R.execution_key(*changed)!=key


def test_failed_reservation_cannot_populate_execution_cache(cli,repo,tmp_path,monkeypatch):
    start(cli,repo);two_gates(cli,tmp_path,[sys.executable,'-c','pass'])
    original=Gates.Owner.finish
    def finish(owner,persist):
        sid=al_store.CURRENT.read_text().strip();st=al_store.load(sid)
        st['evidence']['entries'][-1]['reservation']='replaced';al_store.save(sid,st)
        return original(owner,persist)
    monkeypatch.setattr(Gates.Owner,'finish',finish)
    code,_,_=invoke(cli,'gates','run','--stage','candidate',ok=False)
    assert 'reservation changed' in str(code)
    st=json.loads(cli('show')[1])
    assert len(st['evidence']['entries'])==1 and not st.get('gate_runs')
    assert st['evidence']['entries'][0]['status']!='PASS'


def test_between_gate_metadata_drift_is_checked_before_any_scratch_git_read(cli,repo,tmp_path,monkeypatch):
    start(cli,repo);two_gates(cli,tmp_path,[sys.executable,'-c','pass']);roots=counted_checkout(monkeypatch)
    real_save=al_store.save;real_rows=Gates.S.source_rows;fired=[]
    def save(sid,st):
        result=real_save(sid,st)
        if st['evidence']['entries'] and st['evidence']['entries'][-1]['area']=='gate-candidate-boundary' and st['evidence']['entries'][-1]['status']=='PASS' and not fired:
            fired.append(True);config=Path(roots[0])/'.git/config';config.unlink();config.symlink_to(repo/'.git/config')
        return result
    def rows(root=None,*args,**kwargs):
        if root is not None and (Path(root)/'.git/config').is_symlink():
            pytest.fail('Git source inspection ran before metadata rejection')
        return real_rows(root,*args,**kwargs)
    monkeypatch.setattr(al_store,'save',save);monkeypatch.setattr(Gates.S,'source_rows',rows)
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    st=json.loads(cli('show')[1]);assert fired and len(boundary_runs(st))==2
    assert st['evidence']['entries'][-1]['status']=='INFRA'


@pytest.mark.parametrize('seam',['artifact-before-launch','rule-before-launch','artifact-before-finalize'])
def test_manifest_artifact_and_rule_revalidated_at_execution_boundaries(cli,repo,tmp_path,monkeypatch,seam):
    start(cli,repo);marker=tmp_path/'launched'
    two_gates(cli,tmp_path,[sys.executable,'-c','from pathlib import Path; Path('+repr(str(marker))+').write_text("launched")'])
    st=json.loads(cli('show')[1]);artifact=Path(st['gate_manifests'][-1]['stored_path']);fired=[]
    def damage():
        if not fired:
            fired.append(True)
            if seam=='rule-before-launch':(repo/'Makefile').write_text('all:\n\tfalse\n')
            else:artifact.write_text('{}')
    if seam.endswith('before-launch'):
        real=C.materialize
        @contextlib.contextmanager
        def materialize(*args,**kwargs):
            with real(*args,**kwargs) as checkout:
                damage();yield checkout
        monkeypatch.setattr(C,'materialize',materialize)
    else:
        real=Gates.Owner.finish
        def finish(owner,persist):damage();return real(owner,persist)
        monkeypatch.setattr(Gates.Owner,'finish',finish)
    invoke(cli,'gates','run','--stage','candidate','--id','boundary',ok=False)
    st=json.loads(cli('show')[1]);entry=st['evidence']['entries'][-1]
    assert fired and entry['status']!='PASS' and boundary_runs(st)
    assert marker.exists()==seam.endswith('before-finalize')


@pytest.mark.parametrize('target_exists',[False,True])
def test_ignored_virtualenv_symlinks_discard_checkout_without_following_targets(cli,repo,tmp_path,monkeypatch,target_exists):
    (repo/'.gitignore').write_text('.venv/\n');start(cli,repo)
    target=tmp_path/'external-python';marker=tmp_path/'executions'
    if target_exists:target.write_bytes(b'external interpreter bytes')
    code='from pathlib import Path; assert not Path(".venv").exists(); Path(".venv/bin").mkdir(parents=True); Path(".venv/bin/python").symlink_to('+repr(str(target))+'); p=Path('+repr(str(marker))+'); p.write_text(p.read_text()+"x" if p.exists() else "x")'
    two_gates(cli,tmp_path,[sys.executable,'-c',code]);roots=counted_checkout(monkeypatch)
    real_read=Path.read_bytes;reads=[]
    def read(path,*args,**kwargs):
        if path==target:
            reads.append(str(path));pytest.fail('post-execution inspection followed an ignored symlink')
        return real_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_bytes',read)
    invoke(cli,'gates','run','--stage','candidate')
    state=json.loads(cli('show')[1]);entries=state['evidence']['entries'][-2:]
    assert marker.read_text()=='xx' and len(set(roots))==2 and not (repo/'.venv').exists()
    assert len(boundary_runs(state))==2 and len({e['id'] for e in entries})==2
    assert all(e['status']=='PASS' and e['pid'] and not e.get('reuse') and L.entry_verdict(state,e)[0]=='VALID' for e in entries)
    assert not reads
    if target_exists:assert real_read(target)==b'external interpreter bytes'
    else:assert not target.exists()


def test_post_execution_generated_directory_symlink_never_runs_worktree_git(cli,repo,tmp_path,monkeypatch):
    outside=tmp_path/'outside';outside.mkdir();(outside/'app.py').write_text('preserve external bytes')
    start(cli,repo)
    code='import shutil; from pathlib import Path; shutil.rmtree("svc"); Path("svc").symlink_to('+repr(str(outside))+',target_is_directory=True)'
    two_gates(cli,tmp_path,[sys.executable,'-c',code]);real=C.run
    def run(root,*args):
        if args and args[0] in {'diff','status'} and (root/'svc').is_symlink():
            pytest.fail('post-execution Git could traverse a generated directory symlink')
        return real(root,*args)
    monkeypatch.setattr(C,'run',run)
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    st=json.loads(cli('show')[1])
    assert st['evidence']['entries'][-1]['status']=='FAIL' and len(boundary_runs(st))==1
    assert (outside/'app.py').read_text()=='preserve external bytes'
