"""Explicit reviewed lock subjects never invent inactive sessions."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from al.installer import activity_orphans as M, activity_sessions as S, activity_retirement as A
from al.installer.activity_inventory import inventory_activity
from al.installer.activity_observation import locked_activity, observe_activity, quiescence_context
from al.installer.registry import canonical, digest
from al.tests.activity_helpers import legacy, planned, scenario, write_state, git_repo

SID = 'al-20261016T125855Z-66b815'


def orphan_case(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    path = case['old_runtime'] / ('.' + SID + '.lock');path.write_bytes(b'')
    root = case['roots'][1]
    fingerprint = M.physical(path)
    row = {**root, 'path': str(path), 'session_id': SID, 'profile': M.PROFILE,
           'fingerprint': fingerprint, 'associated_state_absent': True, 'associated_session_directory_absent': True,
           'associated_pointers': [], 'attributed_members': [path.name],
           'pilot_membership': {'relative_path': path.name, 'mode': 0o644, 'type': 'file', 'sha256': M.EMPTY}}
    reference = tmp_path / 'reviewed-locks.json'
    reference.write_bytes(canonical({'schema_version': 1, 'kind': 'reviewed-orphan-session-lock-inventory', 'records': [row]}))
    monkeypatch.setattr(M, 'REVIEW_SHA', digest(reference.read_bytes()))
    case['orphan_review'] = {'path': str(reference), 'sha256': M.REVIEW_SHA}
    case['orphan_path'] = path
    case['inventory'] = fresh(case)
    return case


def fresh(case):
    return inventory_activity(case['roots'], invoking_run=case['invoking'], orphan_review=case['orphan_review'])


@contextmanager
def process(case, tmp_path, *, waiting=False, holding=False, cwd=None, explicit=True, session=SID):
    script = tmp_path / 'al_state.py';ready = tmp_path / 'ready'
    code = 'from pathlib import Path;import time,fcntl;'
    if waiting or holding:code += 'f=open('+repr(str(case['orphan_path']))+',"rb");'
    if holding:code += 'fcntl.flock(f,fcntl.LOCK_EX);'
    code += 'Path('+repr(str(ready))+').write_text("ready");'
    if waiting:code += 'fcntl.flock(f,fcntl.LOCK_EX);'
    code += 'time.sleep(60)'
    script.write_text(code)
    child = subprocess.Popen([sys.executable, str(script), *(['--session', session] if explicit else [])],
                             cwd=cwd or case['roots'][1]['repository']['root'])
    try:
        until = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < until:time.sleep(.01)
        assert ready.exists()
        yield child
    finally:child.terminate();child.wait(timeout=3)


def test_reviewed_orphan_is_separate_preserved_and_has_no_acknowledgement(tmp_path, monkeypatch):
    case = orphan_case(tmp_path, monkeypatch);before = case['orphan_path'].read_bytes()
    assert case['inventory']['stable']
    subjects = case['inventory']['roots'][1]['orphan_locks']
    assert len(subjects) == 1 and set(subjects[0]['identity']) == {'repository', 'platform', 'runtime_root', 'lock_path', 'session_id'}
    plan = planned(case, tmp_path)
    for ref in plan['activity']['acknowledgements']:
        assert SID not in [r['identity']['session_id'] for r in A.read_reference(ref)['covered_records']]
    with quiescence_context(plan) as check:
        result = check()
        assert result['assessment']['eligible']
        assert any(p['subject_kind'] == 'orphan-lock' and p['result'] == 'absent' for p in result['observation']['processes'])
    assert case['orphan_path'].read_bytes() == before and not (case['old_runtime'] / (SID + '.json')).exists()
    assert case['orphan_path'].stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize('mutation', ['unlisted', 'unsupported-name', 'nonempty', 'mode', 'owner', 'hardlink', 'symlink', 'fifo'])
def test_unknown_or_changed_lock_cannot_enter_planning(tmp_path, monkeypatch, mutation):
    case = orphan_case(tmp_path, monkeypatch);path = case['orphan_path']
    if mutation == 'unlisted':(path.parent / '.al-20261016T125855Z-aaaaaa.lock').write_bytes(b'')
    elif mutation == 'unsupported-name':path.rename(path.parent / '.unknown.lock')
    elif mutation == 'nonempty':path.write_bytes(b'owner')
    elif mutation == 'mode':path.chmod(0o600)
    elif mutation == 'owner':monkeypatch.setattr(M.os, 'getuid', lambda: path.stat().st_uid + 1)
    elif mutation == 'hardlink':os.link(path, tmp_path / 'second-link')
    else:
        path.unlink()
        if mutation == 'symlink':path.symlink_to(case['old_path'])
        else:os.mkfifo(path)
    inventory = fresh(case)
    if inventory['stable']:
        with pytest.raises(ValueError, match='unknown runtime lock'):
            with locked_activity(inventory):pass
    else:
        case['inventory'] = inventory
        with pytest.raises(ValueError):planned(case, tmp_path)


def test_actual_competing_flock_blocks_and_exception_releases_descriptor(tmp_path, monkeypatch):
    case = orphan_case(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with process(case, tmp_path, holding=True):
        with pytest.raises(BlockingIOError):
            with quiescence_context(plan):pass
    with pytest.raises(RuntimeError, match='operation'):
        with quiescence_context(plan):raise RuntimeError('operation')
    with case['orphan_path'].open('rb') as file:fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert case['orphan_path'].read_bytes() == b''


@pytest.mark.parametrize('explicit', [True, False])
def test_waiter_or_sessionless_process_blocks_even_when_inspector_holds_lock(tmp_path, monkeypatch, explicit):
    case = orphan_case(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with quiescence_context(plan) as check:
        with process(case, tmp_path, waiting=True, explicit=explicit):
            with pytest.raises(ValueError, match='live|unknown'):check()


def test_conclusively_foreign_same_sid_is_not_relevant(tmp_path, monkeypatch):
    case = orphan_case(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    foreign = git_repo(tmp_path / 'foreign-uncovered-repository')
    with process(case, tmp_path, cwd=foreign['root']):
        with quiescence_context(plan) as check:
            value = check()['observation']
            assert any(p['subject_kind'] == 'orphan-lock' and p['result'] == 'different-process' for p in value['processes'])


@pytest.mark.parametrize('attribution', ['missing', 'denied'])
def test_uncertain_relevant_process_attribution_blocks(tmp_path, monkeypatch, attribution):
    case = orphan_case(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with process(case, tmp_path) as child:
        original = S.process_cwd
        def cwd(pid):
            if pid != child.pid:return original(pid)
            if attribution == 'denied':raise PermissionError('denied attribution')
            return None
        monkeypatch.setattr(S, 'process_cwd', cwd)
        with pytest.raises(ValueError, match='blocked|incomplete'):
            with quiescence_context(plan):pass


@pytest.mark.parametrize('mutation', ['state', 'directory', 'pointer', 'inode'])
def test_same_sid_material_or_inode_movement_invalidates_held_observation(tmp_path, monkeypatch, mutation):
    case = orphan_case(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with quiescence_context(plan) as check:
        if mutation == 'state':write_state(case['old_runtime'], legacy(SID))
        elif mutation == 'directory':(case['old_runtime'] / SID).mkdir()
        elif mutation == 'pointer':(case['old_runtime'] / 'current').write_text(SID)
        else:
            old = case['orphan_path'].with_suffix('.old');case['orphan_path'].rename(old);case['orphan_path'].write_bytes(b'')
        with pytest.raises(ValueError, match='changed|incomplete|inode'):check()


@pytest.mark.parametrize('mutation', ['omit', 'duplicate'])
def test_orphan_observation_completeness_cannot_be_forged(tmp_path, monkeypatch, mutation):
    from al.installer import activity_observation as O
    case = orphan_case(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    original = O._processes
    def changed(inventory):
        rows = original(inventory)
        if mutation == 'omit':return [p for p in rows if p['subject_kind'] != 'orphan-lock']
        return rows + [next(p for p in rows if p['subject_kind'] == 'orphan-lock')]
    monkeypatch.setattr(O, '_processes', changed)
    with pytest.raises(ValueError, match='incomplete|duplicate'):
        with quiescence_context(plan):pass


def test_tagged_guard_and_current_nonterminal_requirement_still_block(tmp_path, monkeypatch):
    from al.installer.guards import scan_activity
    case = orphan_case(tmp_path, monkeypatch)
    assert scan_activity([case['old_runtime']])['blockers']
    value = json.loads(case['current_path'].read_text());value.update(status='RUNNING', phase='IMPLEMENTATION')
    write_state(case['runtime'], value);case['inventory'] = fresh(case);plan = planned(case, tmp_path)
    with pytest.raises(ValueError, match='independently APPROVED/DONE'):
        with quiescence_context(plan):pass


def test_reviewed_unsupported_filename_is_not_a_supported_profile(tmp_path, monkeypatch):
    case = orphan_case(tmp_path, monkeypatch)
    reference = Path(case['orphan_review']['path']);value = json.loads(reference.read_text())
    path = case['orphan_path'];unsupported = path.with_name('.unsupported.lock');path.rename(unsupported)
    value['records'][0].update(path=str(unsupported), attributed_members=[unsupported.name])
    value['records'][0]['pilot_membership']['relative_path'] = unsupported.name
    reference.write_bytes(canonical(value));monkeypatch.setattr(M, 'REVIEW_SHA', digest(reference.read_bytes()))
    case['orphan_review']['sha256'] = M.REVIEW_SHA
    assert not fresh(case)['stable']


@pytest.mark.parametrize('target', ['known-root', 'empty-root', 'ambiguous-cwd'])
def test_unknown_explicit_session_cannot_hide_new_activity(tmp_path, monkeypatch, target):
    case = orphan_case(tmp_path, monkeypatch)
    cwd = case['roots'][1]['repository']['root']
    if target == 'empty-root':
        repo = git_repo(tmp_path / 'empty-repository');cwd = repo['root']
        case['roots'].append({'repository': repo, 'platform': 'codex', 'runtime_root': str(Path(cwd) / '.codex/al-runtime')})
        case['inventory'] = fresh(case)
    plan = planned(case, tmp_path)
    with process(case, tmp_path, cwd=cwd, session='unknown-new-session') as child:
        if target == 'ambiguous-cwd':
            original = S.process_cwd
            monkeypatch.setattr(S, 'process_cwd', lambda pid: None if pid == child.pid else original(pid))
        with pytest.raises(ValueError, match='blocked|incomplete'):
            with quiescence_context(plan):pass
