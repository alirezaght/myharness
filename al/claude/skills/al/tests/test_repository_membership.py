"""Membership and byte budgets must not hide real manifests."""
import pytest
from al_repository_facts import Snapshot, LIMITS, collect
from test_service_coverage import seed, put


@pytest.mark.parametrize("starvation", ["membership", "content"])
def test_generated_inputs_do_not_starve_real_units(repo, monkeypatch, starvation):
    seed(repo)
    put(repo, "apps/web/package.json", '{"name":"web"}')
    put(repo, ".gitignore", "aaa-generated/\n")
    for i in range(100):
        put(repo, f"aaa-generated/{i:03}.json", '"' + 'x'*128 + '"')
    monkeypatch.setitem(LIMITS, "files" if starvation == "membership" else "bytes", 60 if starvation == "membership" else 4096)
    facts = collect(repo, base_oid=None, paths=["services/a/src/app.py"], opaque=[])
    units = {u["id"] for u in facts["units"]}
    assert {"apps/web", "services/a", "services/b"} <= units
    assert not any(g["code"] in {"file-limit", "unreadable-input"} for g in facts["enumeration"]["gaps"])


def test_unknown_nonignored_overflow_is_incomplete_but_keeps_priority_manifests(repo, monkeypatch):
    seed(repo)
    put(repo, "apps/web/package.json", '{"name":"web"}')
    for i in range(100):put(repo, f"aaa-artifacts/{i:03}.json", '{}')
    monkeypatch.setitem(LIMITS, "files", 60)
    facts = collect(repo, base_oid=None, paths=["services/a/src/app.py"], opaque=[])
    assert {"apps/web", "services/a"} <= {u["id"] for u in facts["units"]}
    assert any(g["code"] == "file-limit" for g in facts["enumeration"]["gaps"])


def test_content_budget_prioritizes_manifests(repo, monkeypatch):
    seed(repo)
    put(repo, "apps/web/package.json", '{"name":"web"}')
    for i in range(40):put(repo, f"aaa-artifacts/{i:03}.json", '"' + 'x'*128 + '"')
    monkeypatch.setitem(LIMITS, "bytes", 4096)
    scan = Snapshot(repo, None, [], [])
    assert {"apps/web/package.json", "services/a/pyproject.toml", "services/b/pyproject.toml"} <= scan.files.keys()
    assert scan.gaps


@pytest.mark.parametrize("protection", ["tracked", "base", "explicit", "declared"])
def test_protected_generated_looking_sources_are_preserved(repo, protection):
    from conftest import _git
    import subprocess
    put(repo,"build/source.py","VALUE=1\n")
    put(repo,".gitignore","build/\n")
    base=None;paths=[]
    if protection in {"tracked","base"}:
        _git(repo,"add","-f","build/source.py")
        _git(repo,"commit","-qm","protect generated-looking source")
        if protection=="base":
            base=subprocess.check_output(["git","rev-parse","HEAD"],cwd=repo).decode().strip()
            _git(repo,"rm","--cached","build/source.py")
    elif protection=="explicit":paths=["build/source.py"]
    else:put(repo,"pyproject.toml",'[tool.setuptools.packages.find]\nwhere=["build"]\n')
    scan=Snapshot(repo,base,paths,[])
    assert scan.files["build/source.py"]==b"VALUE=1\n"
    assert any(e['path'].startswith('build') and 'overrides' in e['reason'] for e in scan.membership_observation['exclusions'])


def test_nonignored_untracked_source_and_ignore_rule_currency(repo):
    seed(repo);put(repo,"services/new/pyproject.toml",'[project]\nname="new"\n')
    put(repo,"services/new/src/app.py","VALUE=1\n")
    scan=Snapshot(repo,None,[],[])
    assert 'services/new/src/app.py' in scan.files and scan.stable()
    put(repo,".gitignore","services/new/\n")
    assert not scan.stable()


@pytest.mark.parametrize("boundary",["symlink","gitlink","depth","bytes"])
def test_unsafe_or_limited_inputs_never_certify(repo,monkeypatch,boundary):
    seed(repo)
    if boundary=="symlink":(repo/'services/link').symlink_to(repo/'services/a',target_is_directory=True)
    elif boundary=="gitlink":
        from conftest import _git
        import subprocess
        oid=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo).decode().strip()
        _git(repo,'update-index','--add','--cacheinfo','160000,'+oid+',services/child')
        _git(repo,'commit','-qm','opaque child')
        base=oid
    elif boundary=="depth":
        monkeypatch.setitem(LIMITS,'depth',3);put(repo,'services/a/src/nested/deep.py','VALUE=1\n')
    else:
        put(repo,'services/a/src/huge.py','x'*4096);monkeypatch.setitem(LIMITS,'file_bytes',2048)
    scan=Snapshot(repo,None,[],['services/child'] if boundary=='gitlink' else [])
    if boundary=='gitlink':
        # Opaque supplied boundaries are retained in collected coverage, never traversed.
        facts=collect(repo,base_oid=None,paths=[],opaque=['services/child'])
        assert facts['unsupported_boundaries']
    else:assert scan.gaps


def test_pre_read_limit_does_not_open_oversize_input(repo,monkeypatch):
    import os
    put(repo,'huge.py','x'*4096);monkeypatch.setitem(LIMITS,'file_bytes',1024)
    original=os.open
    def opening(path,*args,**kwargs):
        assert str(path)!='huge.py','oversized bytes were opened before validation'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,'open',opening)
    scan=Snapshot(repo,None,[],[])
    assert any(g['path']=='huge.py' for g in scan.gaps)



def test_unreadable_source_is_a_named_gap(repo,monkeypatch):
    import os
    seed(repo);original=os.open
    def opening(path,*args,**kwargs):
        if str(path)=='app.py':raise PermissionError('fixture denies source read')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,'open',opening)
    scan=Snapshot(repo,None,[],[])
    assert any(g['code']=='unreadable-input' and g['path']=='services/a/src/app.py' for g in scan.gaps)


def test_bounded_git_output_reaps_its_owned_child(repo,monkeypatch):
    import subprocess
    import sys
    from al_repository_facts import git_records
    original=subprocess.Popen;children=[]
    def overflowing(*args,**kwargs):
        proc=original([sys.executable,'-c','import sys,time;sys.stdout.buffer.write(b"member\\0"*100);sys.stdout.flush();time.sleep(30)'],**kwargs)
        children.append(proc);return proc
    monkeypatch.setattr(subprocess,'Popen',overflowing)
    with pytest.raises(ValueError,match='file-limit'):
        git_records(repo,['ls-files','-z'],{**LIMITS,'files':2})
    assert children and all(p.poll() is not None for p in children)



def test_recognized_generated_cache_does_not_consume_membership(repo,monkeypatch):
    seed(repo);put(repo,'apps/web/package.json','{"name":"web"}')
    for i in range(100):put(repo,f'build/{i:03}.json','{}')
    monkeypatch.setitem(LIMITS,'files',60)
    scan=Snapshot(repo,None,[],[])
    assert 'apps/web/package.json' in scan.files and 'services/b/pyproject.toml' in scan.files
    assert not scan.gaps
    assert any(e['path']=='build' and 'exclusion' in e['reason'] for e in scan.membership_observation['exclusions'])



def test_nested_repository_is_opaque_without_relying_on_callers(repo):
    from conftest import _git
    child=repo/'services/child';child.mkdir(parents=True)
    _git(child,'init','-q');put(repo,'services/child/pyproject.toml','[project]\nname="child"\n')
    put(repo,'services/child/src/app.py','raise RuntimeError("never read as parent source")\n')
    scan=Snapshot(repo,None,[],[])
    assert 'services/child/pyproject.toml' not in scan.files
    assert any('gitlink' in g['reason'] for g in scan.gaps)


def test_ignored_cache_creation_keeps_source_requirements_current(cli,repo,tmp_path):
    import al_gate_currency as Currency
    from test_service_coverage import start,plan
    seed(repo);start(cli);plan(cli,repo,tmp_path,['services/a/src/app.py'])
    import json
    st=json.loads(cli('show')[1])
    put(repo,'services/a/src/__pycache__/cache.pyc','generated bytecode')
    assert Currency.check(st)['current']



def test_membership_does_not_execute_repository_fsmonitor(repo):
    from conftest import _git
    from al_repository_facts import git_records
    hook=repo/'monitor-hook'
    hook.write_text('#!/bin/sh\nprintf 1\ntouch "'+str(repo/'monitor-executed')+'"\n')
    hook.chmod(0o700)
    _git(repo,'config','core.fsmonitor',str(hook))
    git_records(repo,['ls-files','--cached','-z'],LIMITS)
    assert not (repo/'monitor-executed').exists()



def test_unknown_manifest_named_data_cannot_starve_supported_manifest_bytes(repo,monkeypatch):
    seed(repo);put(repo,'apps/web/package.json','{"name":"web"}')
    for i in range(40):put(repo,f'aaa-artifacts/{i:03}/package.json','"'+'x'*128+'"')
    monkeypatch.setitem(LIMITS,'bytes',4096)
    scan=Snapshot(repo,None,[],[])
    assert {'apps/web/package.json','services/a/pyproject.toml','services/b/pyproject.toml'}<=scan.files.keys()
    assert scan.gaps



def test_generated_unit_named_cache_is_not_promoted_by_priority_probe(repo):
    seed(repo);put(repo,'services/node_modules/package.json','{"name":"generated"}')
    facts=collect(repo,base_oid=None,paths=['services/a/src/app.py'],opaque=[])
    assert 'services/node_modules' not in {u['id'] for u in facts['units']}


def test_tracked_manifest_protects_its_declared_untracked_generated_root(repo):
    from conftest import _git
    seed(repo)
    put(repo,'services/build/pyproject.toml','[project]\nname="real"\n[tool.setuptools.packages.find]\nwhere=["src"]\n')
    _git(repo,'add','-f','services/build/pyproject.toml')
    put(repo,'services/build/src/source.py','VALUE=1\n')
    scan=Snapshot(repo,None,[],[])
    assert 'services/build/src/source.py' in scan.files



def test_non_source_marker_preserves_requirements_but_changes_execution_candidate(cli,repo,tmp_path):
    import al_gate_currency as Currency
    import al_scope as Scope
    import json
    from test_service_coverage import start,plan
    seed(repo);start(cli);plan(cli,repo,tmp_path,['services/a/src'])
    st=json.loads(cli('show')[1]);before=Scope.capture_candidate(st)['fingerprint']
    put(repo,'services/a/src/fixed','marker')
    assert Currency.check(st)['current']
    assert Scope.capture_candidate(st)['fingerprint']!=before
    assert cli('phase','IMPLEMENTATION')[0]==0
