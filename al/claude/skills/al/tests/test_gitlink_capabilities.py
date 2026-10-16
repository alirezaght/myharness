"""Evidence declares pointer proof explicitly and never executes child checks opaquely."""
import json
import sys
import pytest
import al_ledger as L
import al_scope as S
import al_sources as Sources
from batch13_helpers import start, finished
from evidence_helpers import entries, ok_cmd
from gitlink_helpers import gitlink, commit_child, source_file, gate_plan
from hook_regression_helpers import invoke

PARENT=[{'path':'svc/vendor','capability':'parent-entry'}]


def test_public_parent_pointer_evidence_binds_staging_and_effective_head(cli,repo,tmp_path,gitlink,state_file):
    path,_,_=gitlink
    invoke(cli,'start','--spec','Verify explicitly declared parent pointer identities without child content claims.')
    args=('evidence','run','--area','pointer','--scope','svc','--sources-file',source_file(tmp_path),'--',*ok_cmd('1 passed'))
    invoke(cli,*args)
    first=entries(state_file)[-1]
    assert first['sources']==PARENT and L.assess(first)[0]=='VALID'
    # Child dirt is not parent-entry evidence, and cannot silently become child proof.
    (path/'file').write_text('dirty')
    assert L.assess(first)[0]=='VALID'
    commit_child(path,'B')
    assert L.assess(first)[0]=='STALE'
    invoke(cli,*args)
    assert len(entries(state_file))==2
    assert entries(state_file)[-1]['status']=='PASS'


@pytest.mark.parametrize('capability', [None,'child-content'])
def test_public_evidence_refuses_undeclared_or_child_content_before_launch(cli,tmp_path,gitlink,capability):
    invoke(cli,'start','--spec','Reject opaque execution when child content dependencies cannot be captured.')
    marker=tmp_path/'not-launched'
    args=['evidence','run','--area','ambiguous','--scope','svc']
    if capability:args+=['--sources-file',source_file(tmp_path,capability)]
    result=cli(*args,'--',*ok_cmd('1 passed',marker))
    assert result[0]!=0 and not marker.exists()
    assert 'capability' in str(result)


@pytest.mark.parametrize('scope,cwd', [(['svc/vendor/file'],'.'),(['svc'],'svc/vendor')])
def test_parent_entry_cannot_authorize_child_scope_or_cwd(cli,repo,tmp_path,gitlink,scope,cwd):
    assert L.check_scope(scope,PARENT,cwd)
    start(cli,repo)
    marker=tmp_path/'never'
    gate_plan(cli,tmp_path,ok_cmd('1 passed',marker),scope,PARENT,cwd)
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    entry,_=finished(cli,'INFRA',materialized=False)
    assert not marker.exists() and entry['sources']==PARENT


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('sources',[[],[{'path':'svc/vendor','capability':'child-content'}]])
def test_reserved_gate_capability_failure_finalizes_infra(cli,repo,tmp_path,gitlink,stage,sources):
    start(cli,repo);marker=tmp_path/'never'
    gate_plan(cli,tmp_path,ok_cmd('1 passed',marker),['svc'],sources)
    invoke(cli,'gates','run','--stage',stage,ok=False)
    _,log=finished(cli,'INFRA',materialized=False)
    assert not marker.exists() and b'capability' in log


@pytest.mark.parametrize('stage',['base','candidate'])
def test_disjoint_gate_can_run_with_dirty_child(cli,repo,tmp_path,gitlink,stage):
    path,_,_=gitlink
    start(cli,repo)
    (path/'file').write_text('uncaptured dirty work')
    marker=tmp_path/'ran'
    gate_plan(cli,tmp_path,ok_cmd('1 passed',marker),['svc/app.py'])
    invoke(cli,'gates','run','--stage',stage)
    finished(cli,'PASS')
    assert marker.read_text()=='x'


def test_gate_materializes_effective_pointer_and_records_capabilities(cli,repo,tmp_path,gitlink,state_file):
    path,_,_=gitlink
    start(cli,repo)
    b=commit_child(path,'B')
    from evidence_helpers import git
    git(repo,'add','svc/vendor')
    c=commit_child(path,'C')
    command=[sys.executable,'-c','import subprocess; assert subprocess.check_output(["git","ls-files","--stage","svc/vendor"]).decode().startswith("160000 '+c+' ")']
    gate_plan(cli,tmp_path,command,['svc'],PARENT)
    invoke(cli,'gates','run','--stage','candidate')
    finished(cli,'PASS')
    st=json.loads(state_file().read_text())
    assert st['gate_runs'][-1]['sources']==PARENT
    assert S.git_bytes('ls-files','--stage','svc/vendor').decode().startswith('160000 '+b+' ')


@pytest.mark.parametrize('bad',[{},[{}],[{'path':'../child','capability':'parent-entry'}],[{'path':'svc/vendor','capability':'unknown'}],[{'path':'svc/vendor','capability':[]}],PARENT+PARENT])
def test_invalid_source_arrays_are_refused_without_execution(cli,tmp_path,gitlink,bad):
    invoke(cli,'start','--spec','Validate explicit capability declarations before executing commands.')
    file=tmp_path/'bad.json';file.write_text(json.dumps(bad));marker=tmp_path/'never'
    result=cli('evidence','run','--area','bad','--scope','svc','--sources-file',str(file),'--',*ok_cmd('1 passed',marker))
    assert result[0]!=0 and not marker.exists()


def test_legacy_ordinary_evidence_remains_valid_but_overlapping_legacy_is_unverifiable(cli,repo,tmp_path,gitlink,state_file):
    invoke(cli,'start','--spec','Keep ordinary evidence compatible while refusing ambiguous legacy submodule evidence.')
    invoke(cli,'evidence','run','--area','ordinary','--scope','web','--',*ok_cmd('1 passed'))
    entry=entries(state_file)[-1]
    entry.pop('sources')
    assert L.assess(entry)[0]=='VALID'
    entry['scope']=['svc']
    assert L.assess(entry)[0]=='UNVERIFIABLE'
    assert not L.same_command(dict(entry,sources=PARENT),dict(entry,sources=[]))


def test_parent_pointer_fingerprint_does_not_hash_child_availability(repo,gitlink):
    from gitlink_helpers import remove_child
    path,_,_=gitlink
    before=L.scope_fingerprint(['svc'],sources=PARENT)
    remove_child(path,empty=True)
    assert L.scope_fingerprint(['svc'],sources=PARENT)==before
    with pytest.raises(L.FingerprintError,match='capability'):
        L.scope_fingerprint(['svc'])
    with pytest.raises(L.FingerprintError,match='inside submodule'):
        L.scope_fingerprint(['svc/vendor/file'],sources=PARENT)


def test_gate_detects_source_change_outside_its_declared_scope(cli,repo,tmp_path,gitlink):
    start(cli,repo)
    source=repo/'web/app.js'
    command=[sys.executable,'-c','from pathlib import Path; Path('+repr(str(source))+').write_text("source moved")']
    gate_plan(cli,tmp_path,command,['svc/app.py'])
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    finished(cli,'TORN')
    assert source.read_text()=='source moved'


def test_explicit_gitlink_scope_and_irrelevant_declarations(repo,gitlink):
    assert L.check_scope(['svc/vendor'],PARENT)==[]
    assert L.scope_fingerprint(['svc/vendor'],sources=PARENT)
    irrelevant=[{'path':'svc/app.py','capability':'parent-entry'}]
    assert any('not a submodule' in gap for gap in Sources.capability_gaps(['svc'],irrelevant,[{'path':'svc/app.py','worktree':{'type':'file'}}],root=repo))


def test_discovery_requires_capability_and_preserves_reviewed_declarations(cli,repo,tmp_path,gitlink):
    import al_gate_discovery as D
    path,_,_=gitlink
    start(cli,repo);commit_child(path,'B')
    st=json.loads(cli('show')[1]);manifest=D.discover(st,['svc'])
    gate=next(g for g in manifest['gates'] if g['id']=='contracts')
    assert gate['applicability']=='UNRESOLVED' and 'explicit source capability' in gate['reason']
    gate['sources']=PARENT
    file=tmp_path/'preserved.json';file.write_text(json.dumps(manifest))
    invoke(cli,'gates','plan','--file',str(file))
    updated=D.discover(json.loads(cli('show')[1]),['svc'])
    assert next(g for g in updated['gates'] if g['id']=='contracts')['sources']==PARENT


@pytest.mark.parametrize('stage',['base','candidate'])
def test_removed_gitlink_gate_uses_each_stage_parent_state(cli,repo,tmp_path,gitlink,stage):
    from evidence_helpers import git
    start(cli,repo)
    git(repo,'update-index','--force-remove','svc/vendor')
    expected='True' if stage=='base' else 'False'
    command=[sys.executable,'-c','import subprocess; assert bool(subprocess.check_output(["git","ls-files","--stage","svc/vendor"])) is '+expected]
    gate_plan(cli,tmp_path,command,['svc'],PARENT)
    invoke(cli,'gates','run','--stage',stage)
    finished(cli,'PASS')


@pytest.mark.parametrize('form',['alias','absolute','dotdot','outside-alias','outside-absolute'])
def test_canonical_cwd_child_and_outside_forms_never_launch(cli,repo,tmp_path,gitlink,form):
    path,_,_=gitlink
    outside=tmp_path/'outside';outside.mkdir()
    (repo/'alias').symlink_to('svc/vendor',target_is_directory=True)
    (repo/'outside-alias').symlink_to(outside,target_is_directory=True)
    cwd={'alias':'alias','absolute':str(path),'dotdot':'svc/../svc/vendor',
         'outside-alias':'outside-alias','outside-absolute':str(outside)}[form]
    invoke(cli,'start','--spec','Reject parent-only evidence in child and outside working directories before any launch.')
    marker=tmp_path/'never'
    result=cli('evidence','run','--area','cwd','--scope','svc','--sources-file',source_file(tmp_path),
               '--cwd',cwd,'--',*ok_cmd('1 passed',marker))
    assert result[0]!=0 and not marker.exists()


@pytest.mark.parametrize('cwd',['.','svc','safe-alias'])
def test_canonical_cwd_ordinary_relative_and_contained_alias_execute(cli,repo,tmp_path,gitlink,cwd):
    (repo/'safe-alias').symlink_to('svc',target_is_directory=True)
    invoke(cli,'start','--spec','Preserve ordinary relative and safe contained alias working directory behavior.')
    marker=tmp_path/'ran'
    body='from pathlib import Path; Path('+repr(str(marker))+').write_text(str(Path.cwd())); print("1 passed")'
    invoke(cli,'evidence','run','--area','ordinary-cwd','--scope','svc','--sources-file',source_file(tmp_path),
           '--cwd',cwd,'--',sys.executable,'-c',body)
    assert marker.read_text()==str(repo.resolve() if cwd=='.' else (repo/'svc').resolve())


@pytest.mark.parametrize('fresh',[False,True])
def test_canonical_cwd_planned_alias_retarget_cannot_reuse_or_launch(cli,repo,tmp_path,gitlink,state_file,fresh):
    ordinary=repo/'svc/ordinary';ordinary.mkdir();(ordinary/'input').write_text('parent input')
    alias=repo/'alias';alias.symlink_to('svc/ordinary',target_is_directory=True)
    invoke(cli,'start','--spec','Revalidate a planned working directory before cached reuse and command execution.')
    marker=tmp_path/'execution'
    invoke(cli,'evidence','plan','--area','planned-cwd','--tier','focused','--scope','svc',
           '--sources-file',source_file(tmp_path),'--cwd','alias','--',*ok_cmd('1 passed',marker))
    invoke(cli,'evidence','run','--area','planned-cwd')
    assert marker.exists();marker.unlink()
    alias.unlink();alias.symlink_to('svc/vendor',target_is_directory=True)
    result=cli('evidence','run','--area','planned-cwd',*(['--fresh'] if fresh else []))
    assert result[0]!=0 and not marker.exists() and 'reused' not in result[1]
    assert len(entries(state_file))==1
