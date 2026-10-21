"""Scratch repositories borrow objects, never source indexes or refs."""
import subprocess
import pytest
import al_checkouts as C
import al_scope as S
import al_store
from batch13_helpers import start


def test_materialization_uses_alternates_without_fetch(cli,repo,monkeypatch):
    st=start(cli,repo);original=C.run;calls=[]
    source_index=(repo/'.git/index').read_bytes();source_config=(repo/'.git/config').read_bytes()
    def run(root,*args):
        calls.append(args);return original(root,*args)
    monkeypatch.setattr(C,'run',run)
    with C.materialize(st,'candidate') as checkout:
        root=checkout['root']
        assert (root/'.git/objects/info/alternates').read_text().strip()==str(repo/'.git/objects')
        assert not list((root/'.git/objects').glob('pack/*.pack'))
        assert C.run(root,'rev-parse','HEAD').strip()==st['baseline_head'].encode()
        C.run(root,'config','scratch.only','yes')
        (root/'svc/app.py').write_text('scratch')
        C.run(root,'add','svc/app.py')
    assert not any(args[0]=='fetch' for args in calls)
    assert (repo/'.git/index').read_bytes()==source_index and (repo/'.git/config').read_bytes()==source_config
    assert (repo/'svc/app.py').read_text()=='x = 1\n'


def test_linked_worktree_with_spaces_uses_common_objects(cli,repo,tmp_path,monkeypatch):
    linked=tmp_path/'linked work tree'
    subprocess.run(['git','worktree','add','--detach',str(linked)],cwd=repo,check=True,capture_output=True)
    monkeypatch.chdir(linked);monkeypatch.setattr(al_store,'REPO_ROOT',linked)
    st=start(cli,linked)
    with C.materialize(st,'candidate') as checkout:
        assert (checkout['root']/'.git/objects/info/alternates').read_text().strip()==str(repo/'.git/objects')
        assert (checkout['root']/'svc/app.py').read_text()=='x = 1\n'


def test_missing_revision_is_materialization_error(cli,repo):
    st=start(cli,repo)
    with pytest.raises(C.MaterializationError):
        with C.materialize(st,'base',base='0'*40):pass


def test_sha256_repository_materializes_matching_object_format(cli,tmp_path,monkeypatch):
    root=tmp_path/'sha256 repo';root.mkdir()
    result=subprocess.run(['git','init','-q','--object-format=sha256'],cwd=root,capture_output=True)
    if result.returncode:pytest.skip('Git does not support SHA-256 repositories')
    (root/'binary').write_bytes(b'\0binary\xff')
    monkeypatch.chdir(root);monkeypatch.setattr(al_store,'REPO_ROOT',root)
    st=start(cli,root)
    with C.materialize(st,'candidate') as checkout:
        assert C.run(checkout['root'],'rev-parse','--show-object-format').strip()==b'sha256'
        assert (checkout['root']/'binary').read_bytes()==b'\0binary\xff'
