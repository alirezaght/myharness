"""Coherent source capture and task-local enumeration regressions."""
import subprocess
import pytest

import al_gate_coverage as Coverage
import al_repository_facts as Facts
import al_service_coverage as Services
import al_store
from conftest import _git
from test_service_coverage import put, seed


def test_unconsumed_shared_document_has_no_service_fallback(repo):
    seed(repo)
    put(repo, 'docs/guide.md', '# Shared narrative\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'shared document')
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    put(repo, 'docs/guide.md', '# Changed narrative\n')
    facts = Facts.collect(repo, base_oid=base, paths=['docs/guide.md'], opaque=[])
    assessment = Services.assess(facts)
    assert assessment['selected_services'] == []
    assert assessment['dependency_uncertainties'] == []


def test_unchanged_opaque_diagnostic_is_not_capture_drift(repo):
    put(repo, 'docs/guide.md', '# Guide\n')
    (repo / 'unrelated-link').symlink_to('unavailable-target')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'opaque metadata')
    scan = Facts.Snapshot(repo, None, ['docs/guide.md'], [])
    assert any(g['path'] == 'unrelated-link' for g in scan.gaps)
    assert scan.stable(), 'repeated diagnostics must not manufacture source drift'


def test_directory_expansion_defers_unrelated_opaque_boundary(repo, monkeypatch):
    put(repo, 'docs/guide.md', '# Guide\n')
    (repo / 'unrelated-link').symlink_to('unavailable-target')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'opaque metadata')
    monkeypatch.setattr(al_store, 'REPO_ROOT', repo)
    assert Coverage.expand(['docs']) == ['docs', 'docs/guide.md', 'docs/readme.md']


def test_unconsumed_shared_package_document_has_no_service_fallback(repo):
    seed(repo)
    put(repo, 'packages/shared/guide.md', '# Shared narrative\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'shared package document')
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    put(repo, 'packages/shared/guide.md', '# Changed narrative\n')
    facts = Facts.collect(repo, base_oid=base, paths=['packages/shared/guide.md'], opaque=[])
    assert Services.assess(facts)['selected_services'] == []


def test_one_real_change_discards_first_capture(repo, monkeypatch):
    seed(repo)
    original = Facts.Snapshot.revalidate
    calls = []
    def change_once(scan):
        calls.append(scan)
        if len(calls) == 1:
            put(repo, 'services/a/src/app.py', 'VALUE = 99\n')
        return original(scan)
    monkeypatch.setattr(Facts.Snapshot, 'revalidate', change_once)
    result = Facts.collect(repo, base_oid=None, paths=['services/a/src/app.py'], opaque=[])
    assert result['capture']['attempts'] == 2
    assert len(result['capture']['discarded_attempts']) == 1
    assert calls[0] is not calls[1]
    assert ['services/a/src/app.py', 'packages/shared/src/common.py'] not in result['dependencies']['edges']


def test_repeated_actual_drift_refuses_after_two_attempts(repo, monkeypatch):
    import pytest
    from al_repository_capture import CaptureDrift
    seed(repo)
    original = Facts.Snapshot.revalidate
    calls = []
    def always_change(scan):
        calls.append(scan)
        put(repo, 'services/a/src/app.py', 'VALUE = '+str(len(calls))+'\n')
        return original(scan)
    monkeypatch.setattr(Facts.Snapshot, 'revalidate', always_change)
    with pytest.raises(CaptureDrift) as caught:
        Facts.collect(repo, base_oid=None, paths=['services/a/src/app.py'], opaque=[])
    assert caught.value.reason == 'repeated-drift'
    assert len(calls) == 2


def test_changed_then_restored_identity_is_still_drift(repo):
    import pytest
    from al_repository_capture import CaptureDrift
    seed(repo)
    scan = Facts.Snapshot(repo, None, ['services/a/src/app.py'], [])
    path = repo / 'services/a/src/app.py'
    original = path.read_bytes()
    path.write_bytes(b'VALUE = 99\n')
    path.write_bytes(original)
    with pytest.raises(CaptureDrift):scan.revalidate()


def test_absent_input_becoming_present_invalidates_capture(repo):
    import pytest
    from al_repository_capture import CaptureDrift
    scan = Facts.Snapshot(repo, None, ['docs/readme.md'], [])
    assert scan.capture_bytes('pytest.ini') == b''
    put(repo, 'pytest.ini', '[pytest]\n')
    with pytest.raises(CaptureDrift):scan.revalidate()


def test_permission_failure_is_not_retried_as_drift(repo, monkeypatch):
    seed(repo)
    read = Facts.Snapshot.read_observed
    def denied(scan, path):
        if path == 'services/a/src/app.py':raise PermissionError('synthetic refusal')
        return read(scan, path)
    monkeypatch.setattr(Facts.Snapshot, 'read_observed', denied)
    facts = Facts.collect(repo, base_oid=None, paths=['services/a/src/app.py'], opaque=[])
    assert facts['capture']['attempts'] == 1
    assert any(g['code'] == 'unreadable-input' for g in facts['enumeration']['gaps'])
    assert Services.assess(facts)['outcome'] == 'incomplete'


def test_capture_limit_is_not_retried_as_drift(repo, monkeypatch):
    monkeypatch.setitem(Facts.LIMITS, 'files', 2)
    facts = Facts.collect(repo, base_oid=None, paths=['docs/readme.md'], opaque=[])
    assert facts['capture']['attempts'] == 1
    assert any(g['code'] == 'file-limit' for g in facts['enumeration']['gaps'])


def test_requested_symlink_and_internal_boundary_refuse_expansion(repo):
    import pytest
    put(repo, 'docs/guide.md', '# Guide\n')
    (repo / 'docs/opaque').symlink_to('../unavailable')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'internal boundary')
    with pytest.raises(SystemExit):Coverage.expand(['docs'])
    with pytest.raises(SystemExit):Coverage.expand(['.'])
    (repo / 'requested-link').symlink_to('docs', target_is_directory=True)
    with pytest.raises(SystemExit):Coverage.expand(['requested-link'])


def test_conflicting_delegated_source_cannot_replace_capture(repo):
    import pytest
    from al_repository_capture import CaptureDrift
    scan = Facts.Snapshot(repo, None, ['docs/readme.md'], [])
    scan.capture_bytes('docs/readme.md')
    with pytest.raises(CaptureDrift) as caught:
        scan.adopt_source({'path': 'docs/readme.md', 'sha256': '0'*64}, purpose='test')
    assert caught.value.reason == 'delegated-source-conflict'


def test_ci_parsed_bytes_and_hash_are_one_capture_with_one_fresh_retry(repo,monkeypatch):
    import hashlib
    import al_ci_inventory as CI
    put(repo,'.gitlab-ci.yml','verify:\n  script: python3 -m compileall -q svc\n')
    original=CI.load
    parsed=[]
    def change_while_parsing(raw):
        parsed.append(raw)
        if len(parsed)==1:
            put(repo,'.gitlab-ci.yml','verify:\n  script: python3 -m compileall -qq svc\n')
        return original(raw)
    monkeypatch.setattr(CI,'load',change_while_parsing)
    result=CI.inventory(repo,['svc/app.py'])
    assert result['capture']['attempts']==2
    assert len(result['capture']['discarded_attempts'])==1
    assert '-qq svc' in result['obligations'][0]['execution']['argv'][-1]
    ref=next(r for r in result['dependencies'] if r['path']=='.gitlab-ci.yml')
    assert ref['sha256']==hashlib.sha256((repo/'.gitlab-ci.yml').read_bytes()).hexdigest()
    assert parsed[0]!=parsed[-1]


def test_changed_then_restored_include_membership_is_still_drift(repo):
    import pytest
    from al_repository_capture import CaptureDrift
    put(repo,'ci/seed.yml','seed:\n  script: echo stable\n')
    scan=Facts.Snapshot(repo,None,['docs/readme.md'],[])
    scan.observe_matches('ci/*.yml',purpose='include')
    put(repo,'ci/transient.yml','transient:\n  script: echo transient\n')
    (repo/'ci/transient.yml').unlink()
    with pytest.raises(CaptureDrift):scan.revalidate()


def test_listing_observation_is_immutable_and_revalidation_has_no_diagnostic_side_effect(repo):
    from copy import deepcopy
    import pytest
    (repo/'unrelated-link').symlink_to('missing')
    _git(repo,'add','.')
    _git(repo,'commit','-qm','stable boundary')
    scan=Facts.Snapshot(repo,None,['docs/readme.md'],[])
    before=deepcopy(scan.listing_observation)
    gaps=deepcopy(scan.gaps)
    with pytest.raises(TypeError):scan.listing_observation['complete']=True
    assert scan.stable()
    assert scan.listing_observation==before and scan.gaps==gaps


def test_metadata_only_absence_is_retained_and_counted(repo):
    scan=Facts.Snapshot(repo,None,['docs/readme.md'],[])
    assert scan.observe_path('pytest.ini')['state']=='absent'
    assert 'pytest.ini' in scan.absent
    assert scan.capture_metadata()['absence_count']==len(scan.absent)
    assert scan.capture_metadata()['absence_count']>0


@pytest.mark.parametrize('container',['apps','services','packages'])
@pytest.mark.parametrize('kind',['symlink','opaque','ordinary'])
def test_priority_containers_do_not_enumerate_opaque_children(repo,tmp_path,monkeypatch,container,kind):
    import os
    from pathlib import Path
    target=repo/container
    if kind=='symlink':
        outside=tmp_path/'container-target';outside.mkdir()
        put(outside,'private-child/pyproject.toml','[project]\nname="opaque-child"\n')
        target.symlink_to(outside,target_is_directory=True)
    else:
        put(repo,container+'/private-child/pyproject.toml','[project]\nname="ordinary-child"\n')
    original=os.scandir;calls=[]
    def no_opaque_scandir(path):
        if isinstance(path,(str,os.PathLike)) and Path(path)==target:
            calls.append(Path(path))
            assert kind=='ordinary','opaque priority container enumeration attempted'
        return original(path)
    monkeypatch.setattr(os,'scandir',no_opaque_scandir)
    original_git=Facts.git_records
    def bounded_membership(root,args,limits):
        if kind=='opaque' and '--others' in args:
            assert '--exclude=/'+container+'/' in args
        return original_git(root,args,limits)
    monkeypatch.setattr(Facts,'git_records',bounded_membership)
    scan=Facts.Snapshot(repo,None,['docs/readme.md'],[container] if kind=='opaque' else [])
    if kind=='ordinary':
        assert calls and container+'/private-child/pyproject.toml' in scan.members
    else:
        assert not calls
        assert not any(path.startswith(container+'/') for path in scan.members)
        assert any(g['path']==container and g['code'] in {'symlink-boundary','unsafe-boundary','opaque-boundary'} for g in scan.gaps)
        assert not any(g['path'].startswith(container+'/') for g in scan.gaps)


def test_opaque_git_exclusions_are_literal_and_preserve_neighbor_units(repo):
    opaque='services/opaque[1]'
    put(repo,opaque+'/private/pyproject.toml','[project]\nname="hidden"\n')
    put(repo,'services/opaque1/pyproject.toml','[project]\nname="visible"\n')
    scan=Facts.Snapshot(repo,None,['docs/readme.md'],[opaque])
    assert 'services/opaque1/pyproject.toml' in scan.members
    assert not any(p.startswith(opaque+'/') for p in scan.members)
    assert any(g['path']==opaque for g in scan.gaps)
    assert not any(g['path'].startswith(opaque+'/') for g in scan.gaps)
