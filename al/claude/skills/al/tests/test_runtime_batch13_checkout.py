"""Public materialization rejects unsafe links before any gate can launch."""
import json
import os
from pathlib import Path
import sys
import pytest
import al_checkout_paths as P
import al_checkouts as C
from hook_regression_helpers import invoke
from batch13_helpers import start,manifest,finished,stub,body,assert_incomplete_admission


def command(marker,target='link'):
    return [sys.executable,'-c','from pathlib import Path; Path('+repr(str(marker))+').write_text("launched"); Path('+repr(target)+').write_text("changed")']


def links(repo,tmp,kind):
    outside=tmp/'outside';outside.write_text('preserve')
    if kind=='absolute':(repo/'link').symlink_to(outside)
    elif kind=='relative':(repo/'link').symlink_to('../outside')
    elif kind=='chain':
        (repo/'link').symlink_to('second');(repo/'second').symlink_to(outside)
    elif kind=='directory':
        directory=tmp/'external-dir';directory.mkdir();(repo/'link').symlink_to(directory,target_is_directory=True)
    elif kind=='intermediate':
        (repo/'inner').mkdir();(repo/'inner/dir').symlink_to(tmp,target_is_directory=True);(repo/'link').symlink_to('inner/dir/outside')
    elif kind=='dangling':(repo/'link').symlink_to('missing')
    elif kind=='cycle':
        (repo/'link').symlink_to('second');(repo/'second').symlink_to('link')
    elif kind=='git':(repo/'link').symlink_to('.git/config')
    return outside


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('kind',['absolute','relative','chain','directory','intermediate','dangling','cycle','git'])
def test_unsafe_initial_links_finalize_setup_infra_without_execution(cli,repo,tmp_path,stage,kind):
    outside=links(repo,tmp_path,kind);start(cli,repo);marker=tmp_path/'launched'
    manifest(cli,tmp_path,command(marker));invoke(cli,'gates','run','--stage',stage,'--id','boundary',ok=False)
    entry,log=finished(cli,'INFRA',materialized=False)
    assert entry['exit']==126 and b'materialization rejected' in log and not marker.exists()
    assert outside.read_text()=='preserve' and (repo/'svc/app.py').read_text()=='x = 1\n'


def test_unsafe_base_is_rejected_even_when_candidate_overlay_deletes_link(cli,repo,tmp_path):
    outside=links(repo,tmp_path,'absolute');start(cli,repo);(repo/'link').unlink();marker=tmp_path/'launched'
    manifest(cli,tmp_path,command(marker,'svc/app.py'));invoke(cli,'gates','run','--stage','candidate','--id','boundary',ok=False)
    finished(cli,'INFRA',materialized=False);assert not marker.exists() and outside.read_text()=='preserve'


def test_new_unsafe_candidate_link_is_rejected_before_staging_or_execution(cli,repo,tmp_path,monkeypatch):
    start(cli,repo);outside=tmp_path/'outside';outside.write_text('preserve');(repo/'link').symlink_to(outside)
    marker=tmp_path/'launched';manifest(cli,tmp_path,command(marker));original=C.run;staged=[]
    def observe(root,*args):
        if args and args[0]=='add':staged.append(root)
        return original(root,*args)
    monkeypatch.setattr(C,'run',observe)
    invoke(cli,'gates','run','--stage','candidate','--id','boundary',ok=False);finished(cli,'INFRA',materialized=False)
    assert staged==[] and not marker.exists() and outside.read_text()=='preserve'


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('remote',[False,True])
@pytest.mark.parametrize('cwd',['.git','missing','svc/app.py'])
def test_gate_cwd_rejects_metadata_missing_or_file_before_either_branch(cli,repo,tmp_path,monkeypatch,state_file,stage,remote,cwd):
    data=body(start(cli,repo));marker=tmp_path/'launched'
    if remote:stub(tmp_path,monkeypatch,json.dumps(data).encode(),marker)
    argv=['glab','api','projects/42/jobs/9'] if remote else command(marker,'svc/app.py')
    manifest(cli,tmp_path,argv,category='ci' if remote else 'contracts',remote=remote,cwd=cwd)
    before=state_file().read_bytes()
    code,out,err=invoke(cli,'gates','run','--stage',stage,'--id','boundary',ok=False)
    if cwd=='.git':finished(cli,'INFRA',materialized=False)
    else:
        assert 'check cwd does not exist' in str(code)+out+err
        assert state_file().read_bytes()==before
    assert not marker.exists()


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('shape',['file','directory','chain','parent'])
@pytest.mark.parametrize('generate',[False,True])
def test_contained_links_are_supported_and_generated_changes_still_fail(cli,repo,tmp_path,stage,shape,generate):
    (repo/'safe').mkdir();(repo/'safe/target').write_text('original')
    if shape=='directory':
        (repo/'link').symlink_to('safe',target_is_directory=True);target='link/target';cwd='link'
    elif shape=='chain':
        (repo/'link').symlink_to('second');(repo/'second').symlink_to('safe/target');target='link';cwd='.'
    elif shape=='parent':
        (repo/'safe/link').symlink_to('../safe/target');target='safe/link';cwd='.'
    else:(repo/'link').symlink_to('safe/target');target='link';cwd='.'
    start(cli,repo)
    if shape=='directory':target='target'
    expression='write_text("changed")' if generate else 'read_text()=="original"'
    code='from pathlib import Path; '+('' if generate else 'assert ')+'Path('+repr(target)+').'+expression
    manifest(cli,tmp_path,[sys.executable,'-c',code],cwd=cwd)
    invoke(cli,'gates','run','--stage',stage,'--id','boundary',ok=not generate);finished(cli,'FAIL' if generate else 'PASS')
    assert (repo/'safe/target').read_text()=='original'
    if shape=='directory':assert_incomplete_admission(cli)


@pytest.mark.parametrize('kind',['absolute','relative','chain'])
def test_overlay_parent_guard_prevents_following_links_for_writes(tmp_path,kind):
    root=tmp_path/'checkout';root.mkdir();outside=tmp_path/'outside';outside.mkdir();marker=outside/'marker';marker.write_text('preserve')
    if kind=='absolute':(root/'parent').symlink_to(outside,target_is_directory=True)
    elif kind=='relative':(root/'parent').symlink_to('../outside',target_is_directory=True)
    else:
        (root/'parent').symlink_to('second',target_is_directory=True);(root/'second').symlink_to(outside,target_is_directory=True)
    with pytest.raises(P.MaterializationError):P.overlay(root,root/'parent/marker').write_text('changed')
    assert marker.read_text()=='preserve'


def test_resolution_rejects_unsupported_targets_and_excessive_chains(tmp_path):
    root=tmp_path/'checkout';root.mkdir();os.mkfifo(root/'fifo');(root/'link').symlink_to('fifo')
    with pytest.raises(P.MaterializationError,match='unsupported'):P.resolve(root,root/'link')
    (root/'target').write_text('safe')
    for i in range(66):(root/('link'+str(i))).symlink_to('target' if i==65 else 'link'+str(i+1))
    with pytest.raises(P.MaterializationError,match='excessive'):P.resolve(root,root/'link0')


def test_admitted_source_directory_absent_from_base_finalizes_infra(cli,repo,tmp_path):
    start(cli,repo);(repo/'newdir').mkdir();(repo/'newdir/file').write_text('new')
    marker=tmp_path/'launched';manifest(cli,tmp_path,command(marker),cwd='newdir')
    invoke(cli,'gates','run','--stage','base','--id','boundary',ok=False);finished(cli,'INFRA',materialized=False)
    assert not marker.exists()


def test_final_boundary_revalidates_links_after_representation_inspection(cli,repo,tmp_path,monkeypatch):
    start(cli,repo);outside=tmp_path/'outside';outside.write_text('preserve');marker=tmp_path/'launched'
    manifest(cli,tmp_path,command(marker,'svc/app.py'));original=C.S.records
    def inspect(base,root=None):
        rows=original(base,root=root)
        if root is not None:(root/'late-link').symlink_to(outside)
        return rows
    monkeypatch.setattr(C.S,'records',inspect)
    invoke(cli,'gates','run','--stage','candidate','--id','boundary',ok=False);finished(cli,'INFRA',materialized=False)
    assert not marker.exists() and outside.read_text()=='preserve'
