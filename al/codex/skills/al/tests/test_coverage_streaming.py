"""Streaming capture bounds retention without weakening exact coverage evidence."""
import copy
import gc
import hashlib
import os
import weakref

import pytest

import al_python_imports as imports
import al_repository_facts as facts
from conftest import _git
from test_service_coverage import put, seed


def base_oid(repo):
    import subprocess
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo).decode().strip()


def source_pool(repo,count=30):
    seed(repo)
    for i in range(count):
        put(repo,f'packages/shared/src/mod{i:03}.py',f'VALUE={i}\n'+('# streaming input\n'*75))
    _git(repo,'add','.');_git(repo,'commit','-qm','streamed source fixture')
    return base_oid(repo)


def capture(repo,base):
    return facts.collect(repo,base_oid=base,paths=['packages/shared/src/common.py'],opaque=[])


def test_current_and_base_exceed_retention_without_losing_any_source(repo,monkeypatch):
    base=source_pool(repo)
    monkeypatch.setitem(facts.LIMITS,'bytes',4096)
    monkeypatch.setitem(facts.LIMITS,'file_bytes',2048)
    import al_git_cache
    actual=al_git_cache.blobs;batches=[]
    def observed(root,oids,**limits):
        result=actual(root,oids,**limits)
        batches.append((sum(len(data) for data in result.values()),limits['max_bytes']))
        return result
    monkeypatch.setattr(al_git_cache,'blobs',observed)
    scan=facts.Snapshot(repo,base,[],[])
    sources={f'packages/shared/src/mod{i:03}.py' for i in range(30)}
    assert sources <= scan.files.keys() and sources <= scan.base.keys()
    assert scan.total > 10*facts.LIMITS['bytes']
    assert 0 < scan.peak_retained_bytes <= facts.LIMITS['bytes']
    assert not scan.gaps and scan.stable()
    assert all(size <= allowance <= scan.batch_bytes for size,allowance in batches)
    assert len(batches)>1
    for path,data in scan.base.content_items(sorted(sources)):
        assert data==(repo/path).read_bytes()
        assert scan.base.records[path]['sha256']==hashlib.sha256(data).hexdigest()
    assert scan.retained_bytes==sum(map(len,scan.cache.values())) <= facts.LIMITS['bytes']


def test_retention_allowance_does_not_change_semantics_checks_or_source_hashes(repo,monkeypatch):
    base=source_pool(repo)
    expected=copy.deepcopy(capture(repo,base))
    monkeypatch.setitem(facts.LIMITS,'bytes',1024)
    actual=capture(repo,base)
    # The declared storage allowance changes; all engineering facts and semantic
    # requirements, sourced checks/signatures and versioned content hashes do not.
    assert facts.semantic_requirements(actual)==facts.semantic_requirements(expected)
    expected['scan']['limits']['bytes']=1024
    assert actual==expected
    assert not actual['enumeration']['gaps']


def test_python_analysis_does_not_retain_repository_ast_pool(repo,monkeypatch):
    base=source_pool(repo,20)
    imports.GRAPHS.clear()
    # Every source is parsed here: no stored per-blob facts (#25).
    monkeypatch.setenv('AL_ANALYSIS_CACHE','0')
    import al_blob_analysis as blob
    real=blob.parse;trees=[];peak=0
    def tracked(data):
        nonlocal peak
        gc.collect()
        tree=real(data)
        trees.append(weakref.ref(tree))
        peak=max(peak,sum(ref() is not None for ref in trees))
        assert peak<=2
        return tree
    monkeypatch.setattr(blob,'parse',tracked)
    observed=capture(repo,base)
    # Identical base and current blobs are parsed once (#25).
    assert len(trees)>=20 and peak<=2 and observed['scan']['stable']
    gc.collect()
    assert not any(ref() is not None for ref in trees)


@pytest.mark.parametrize('cached',[False,True])
def test_captured_current_version_cannot_silently_rebind_after_same_mtime_edit(repo,cached):
    scan=facts.Snapshot(repo,None,[],[])
    path='svc/app.py'
    if not cached:scan.cache.clear();scan.retained_bytes=0
    before=(repo/path).stat()
    (repo/path).write_text('x = 2\n')
    os.utime(repo/path,ns=(before.st_atime_ns,before.st_mtime_ns))
    with pytest.raises(ValueError,match='captured coverage input changed'):
        scan.files[path]
    assert not scan.stable()


def test_cached_payload_is_not_an_authority_on_its_own(repo):
    scan=facts.Snapshot(repo,None,[],[])
    path='svc/app.py'
    scan.cache[('current',path)]=b'forged bytes'
    with pytest.raises(ValueError,match='captured coverage content changed'):
        scan.files[path]


def test_metadata_only_membership_hash_queries_do_not_read_payload(repo,monkeypatch):
    scan=facts.Snapshot(repo,base_oid(repo),[],[])
    def unavailable(*args,**kwargs):raise AssertionError('metadata operation opened source')
    monkeypatch.setattr(scan,'read_observed',unavailable)
    assert 'svc/app.py' in scan.files and 'missing.py' not in scan.files
    assert set(scan.files)==set(scan.files.keys())
    assert scan.files.hashes()['svc/app.py']==hashlib.sha256(b'x = 1\n').hexdigest()
    assert scan.base.hashes()['svc/app.py']==hashlib.sha256(b'x = 1\n').hexdigest()


def test_streaming_keeps_per_file_oversize_and_unreadable_sources_as_named_gaps(repo,monkeypatch):
    source_pool(repo,2)
    put(repo,'services/a/src/oversize.py','#'+('x'*4096))
    _git(repo,'add','.');_git(repo,'commit','-qm','oversized source fixture')
    monkeypatch.setitem(facts.LIMITS,'bytes',256)
    monkeypatch.setitem(facts.LIMITS,'file_bytes',2048)
    real=os.open
    def denied(path,*args,**kwargs):
        if str(path)=='common.py':raise PermissionError('fixture denied exact source')
        return real(path,*args,**kwargs)
    monkeypatch.setattr(os,'open',denied)
    scan=facts.Snapshot(repo,base_oid(repo),[],[])
    assert any(g['code']=='unreadable-input' and g['path']=='packages/shared/src/common.py' for g in scan.gaps)
    assert any(g['code']=='unreadable-input' and g['path']=='services/a/src/oversize.py' for g in scan.gaps)
    assert any(g['code']=='base-byte-limit' and g['path']=='services/a/src/oversize.py' for g in scan.gaps)
    assert 'services/a/src/oversize.py' not in scan.files and 'services/a/src/oversize.py' not in scan.base
    assert 'packages/shared/src/mod001.py' in scan.files and 'packages/shared/src/mod001.py' in scan.base


def test_streamed_base_payload_still_matches_captured_content_hash(repo,monkeypatch):
    scan=facts.Snapshot(repo,base_oid(repo),[],[])
    import al_git_cache
    real=al_git_cache.blobs
    def changed(root,oids,**kwargs):return {key:b'forged base' for key in real(root,oids,**kwargs)}
    monkeypatch.setattr(al_git_cache,'blobs',changed)
    with pytest.raises(ValueError,match='captured coverage content changed'):
        list(scan.base.items())


def test_ignored_recursive_symlink_is_excluded_before_source_inspection(repo, tmp_path, monkeypatch):
    seed(repo)
    put(repo,'.gitignore','apps/front/generated/\n')
    put(repo,'apps/front/package.json','{"name":"front"}')
    outside=tmp_path/'private-target'; outside.mkdir()
    put(outside,'hidden.py','raise RuntimeError("must not inspect")\n')
    link='apps/front/generated/link'
    (repo/link).parent.mkdir(parents=True); (repo/link).symlink_to(outside,target_is_directory=True)
    original=facts.Snapshot.safe_file
    def check(self,path):
        assert path != link, 'incidental ignored symlink reached source inspection'
        return original(self,path)
    monkeypatch.setattr(facts.Snapshot,'safe_file',check)
    observed=facts.collect(repo,base_oid=None,paths=['apps/front'],opaque=[])
    assert not any(g['code']=='symlink-boundary' for g in observed['enumeration']['gaps'])
    assert any(e['path']=='apps/front/generated' for e in observed['enumeration']['ignored_outputs'])
    assert not any(p['path'].startswith('apps/front/generated/') for p in observed['scan_inputs'])


@pytest.mark.parametrize('kind',['explicit','tracked','base','declared'])
def test_ignored_symlink_still_blocks_when_it_is_a_protected_input(repo,tmp_path,kind):
    seed(repo)
    put(repo,'.gitignore','ignored/\n')
    target=tmp_path/'private-target'; target.mkdir()
    link='ignored/link'; (repo/link).parent.mkdir(); (repo/link).symlink_to(target,target_is_directory=True)
    paths=['services/a/src/app.py']; base=None
    if kind=='explicit': paths=[link]
    elif kind in {'tracked','base'}:
        _git(repo,'add','-f',link); _git(repo,'commit','-qm','protected symlink')
        if kind=='base':
            base=base_oid(repo); (repo/link).unlink()
    else:
        put(repo,'pyproject.toml','[tool.uv.workspace]\nmembers=["ignored/link"]\n')
    observed=facts.collect(repo,base_oid=base,paths=paths,opaque=[])
    assert any(g['path']==link and g['code'] in {'symlink-boundary','base-symlink'}
               for g in observed['enumeration']['gaps'])


@pytest.mark.parametrize('kind',['explicit','declared'])
def test_selected_ignored_generated_source_root_retains_its_members(repo,kind):
    seed(repo)
    put(repo,'.gitignore','build/\n')
    put(repo,'build/member/pyproject.toml','[project]\nname="generated-source"\n')
    put(repo,'build/member/src/mod.py','VALUE=1\n')
    paths=['build/member']
    if kind=='declared':
        put(repo,'pyproject.toml','[tool.uv.workspace]\nmembers=["build/member"]\n')
        paths=['services/a/src/app.py']
    snapshot=facts.Snapshot(repo,None,paths,[])
    assert 'build/member/pyproject.toml' in snapshot.files
    assert 'build/member/src/mod.py' in snapshot.files
    assert snapshot.files['build/member/src/mod.py']==b'VALUE=1\n'
