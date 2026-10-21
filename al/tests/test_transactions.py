"""Focused outer-publication failures and guarded rollback against real engine APIs."""
from pathlib import Path
import pytest
from al.installer import journal as J
from al.installer import operations as O
from al.installer.engine import D
from al.installer.registry import load_record
from al.installer.targets import package_path, resolve_target
from al.tests.test_manifest import git_test, make_source, tag_second


def setup_install(tmp_path, monkeypatch, platforms=('codex', 'claude')):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('AL_RUNTIME_DIR', raising=False)
    source = make_source(tmp_path)
    home = tmp_path / 'home'
    home.mkdir()
    target = resolve_target(home=home, platforms=platforms)
    state = tmp_path / 'state'
    request = {'target': target, 'kind': 'install', 'source': str(source), 'version': '1.0.0', 'policy': 'follow'}
    return source, target, state, request


def test_two_version_rollback_uses_fresh_generation(tmp_path, monkeypatch):
    source, target, state, request = setup_install(tmp_path, monkeypatch)
    assert O.run_operation(request, state)['status'] == 'updated'
    row = load_record(state, target['target_id'])
    assert row['current']['packages']['codex']['generation'] == 1
    config = Path(target['root']) / '.codex/config.toml'
    config.write_bytes(config.read_bytes() + b'\n# keep this\n[custom]\nvalue = "user"\n')
    tag_second(source, extra_role=True)
    O.run_operation(dict(request, kind='update', version='1.1.0'), state)
    extra = Path(target['root']) / '.codex/agents/al-extra.toml'
    assert extra.is_file()
    row = load_record(state, target['target_id'])
    assert row['current']['packages']['codex']['generation'] == 2
    O.rollback_previous(target, state)
    row = load_record(state, target['target_id'])
    assert row['current']['release']['version'] == '1.0.0'
    assert row['current']['packages']['codex']['generation'] == 3
    assert not extra.exists()
    assert b'# keep this' in config.read_bytes() and b'value = "user"' in config.read_bytes()
    O.check_current(row, state)


@pytest.mark.parametrize('boundary', ['PREPARED', 'APPLYING', 'VERIFYING', 'VERIFIED', 'COMPLETE'])
def test_publication_boundaries_never_publish_partial(tmp_path, monkeypatch, boundary):
    source, target, state, request = setup_install(tmp_path, monkeypatch, ('codex',))
    original = J.checkpoint
    def fail(journal, path, status):
        original(journal, path, status)
        if status == boundary:
            raise OSError('injected ' + boundary)
    monkeypatch.setattr(J, 'checkpoint', fail)
    with pytest.raises(OSError, match='injected'):
        O.run_operation(request, state)
    row = load_record(state, target['target_id'])
    assert row is None or row['current'] is None
    monkeypatch.setattr(J, 'checkpoint', original)
    with pytest.raises(ValueError, match='unresolved|incomplete/orphan'):
        O.run_operation(request, state)


def test_partial_platform_failure_is_pending_and_not_retried(tmp_path, monkeypatch):
    source, target, state, request = setup_install(tmp_path, monkeypatch)
    original, calls = D.apply, []
    def partial(manifest, backup):
        calls.append(backup)
        if len(calls) == 2:
            raise OSError('second platform failed')
        return original(manifest, backup)
    monkeypatch.setattr(D, 'apply', partial)
    with pytest.raises(OSError, match='second platform'):
        O.run_operation(request, state)
    row = load_record(state, target['target_id'])
    assert row['pending'] and row['current'] is None
    assert (package_path(target, 'codex') / 'SKILL.md').exists()
    with pytest.raises(ValueError, match='unresolved'):
        O.run_operation(request, state)
    assert len(calls) == 2


def test_final_publication_uncertainty_confirms_completed_record(tmp_path, monkeypatch):
    source, target, state, request = setup_install(tmp_path, monkeypatch, ('codex',))
    original = J.write_json
    def uncertain(path, value):
        original(path, value)
        if path.parent.name == 'records' and value.get('current') and not value.get('pending'):
            raise OSError('reported failure after replace')
    monkeypatch.setattr(J, 'write_json', uncertain)
    assert O.run_operation(request, state)['status'] == 'updated'
    row = load_record(state, target['target_id'])
    assert not row['pending']
    assert O.check_snapshot(row['current'], state)


def test_desired_file_drift_and_pin_policy(tmp_path, monkeypatch):
    source, target, state, request = setup_install(tmp_path, monkeypatch, ('codex',))
    O.run_operation(dict(request, policy='pinned'), state)
    tag_second(source)
    result = O.update_registered(None, state)
    assert result[0]['status'] == 'pinned'
    path = package_path(target, 'codex') / 'SKILL.md'
    path.write_text('local edit')
    result = O.update_registered(None, state, version='1.1.0')
    assert result[0]['status'] == 'failed' and 'drift' in result[0]['error']
    assert path.read_text() == 'local edit'


def test_rollback_optional_status_line_metadata(tmp_path, monkeypatch):
    source, target, state, request = setup_install(tmp_path, monkeypatch, ('claude',))
    O.run_operation(request, state)
    tag_second(source)
    O.run_operation(dict(request, kind='update', version='1.1.0', status_line=True), state)
    O.rollback_previous(target, state)
    row = load_record(state, target['target_id'])
    assert row['current']['status_line'] is False
    settings = Path(target['root']) / '.claude/settings.json'
    import json
    assert 'statusLine' not in json.loads(settings.read_bytes())
    O.run_operation(dict(request, kind='update', version='1.1.0'), state)
    assert 'statusLine' not in json.loads(settings.read_bytes())


def test_project_lock_policy_and_mode_round_trip(tmp_path, monkeypatch):
    source, _, state, request = setup_install(tmp_path, monkeypatch, ('codex',))
    repo = tmp_path / 'project'
    repo.mkdir()
    git_test(repo, 'init', '-q')
    git_test(repo, 'commit', '--allow-empty', '-qm', 'project fixture')
    target = resolve_target(repo=repo, platforms=['codex'])
    request['target'] = target
    O.run_operation(request, state)
    lock = repo / 'al.lock.json'
    import json
    assert json.loads(lock.read_bytes())['policy'] == 'follow'
    tag_second(source)
    O.run_operation(dict(request, kind='update', version='1.1.0', policy='pinned'), state)
    assert json.loads(lock.read_bytes())['policy'] == 'pinned'
    O.rollback_previous(target, state)
    assert json.loads(lock.read_bytes())['version'] == '1.0.0'
    assert json.loads(lock.read_bytes())['policy'] == 'follow'
    assert lock.stat().st_mode & 0o777 == 0o644
    O.check_current(load_record(state, target['target_id']), state)


def test_retained_engine_journal_corruption_refuses_rollback(tmp_path, monkeypatch):
    import json
    source, target, state, request = setup_install(tmp_path, monkeypatch, ('codex',))
    O.run_operation(request, state)
    first = load_record(state, target['target_id'])['current']
    tag_second(source)
    O.run_operation(dict(request, kind='update', version='1.1.0'), state)
    outer = json.loads(Path(first['journal']).read_bytes())
    engine = Path(outer['engine_steps'][0]['backup']) / 'distribution-journal.json'
    data = json.loads(engine.read_bytes())
    data['actions'] = []
    engine.write_text(json.dumps(data))
    before = (package_path(target, 'codex') / 'SKILL.md').read_bytes()
    with pytest.raises(ValueError, match='integrity'):
        O.rollback_previous(target, state)
    assert (package_path(target, 'codex') / 'SKILL.md').read_bytes() == before


def test_bootstrap_state_inside_source_repository_refused(tmp_path):
    from al.installer.bootstrap import bootstrap_cli
    source = make_source(tmp_path)
    (source / 'al/alctl.py').write_text('')
    with pytest.raises(ValueError, match='outside source'):
        bootstrap_cli(source / 'al', tmp_path / 'bin/alctl', source / '.installer-state')
    assert not (source / '.installer-state').exists()


def test_new_enrollment_cannot_alias_existing_target(tmp_path, monkeypatch):
    source, _, state, request = setup_install(tmp_path, monkeypatch, ('codex',))
    repo = tmp_path / 'project'
    repo.mkdir()
    git_test(repo, 'init', '-q')
    git_test(repo, 'commit', '--allow-empty', '-qm', 'project fixture')
    project = resolve_target(repo=repo, platforms=['codex'])
    O.run_operation(dict(request, target=project), state)
    alias = resolve_target(home=repo, platforms=['codex'])
    before = (package_path(project, 'codex') / 'SKILL.md').read_bytes()
    with pytest.raises(ValueError, match='overlapping'):
        O.run_operation(dict(request, target=alias), state)
    assert load_record(state, alias['target_id']) is None
    assert (package_path(project, 'codex') / 'SKILL.md').read_bytes() == before


def test_batch_continues_after_malformed_release(tmp_path, monkeypatch):
    rows = [{'target_id': 'first', 'target': {'target_id': 'first'}},
            {'target_id': 'second', 'target': {'target_id': 'second'}}]
    monkeypatch.setattr(O, 'records', lambda state: rows)
    def apply(request, state):
        if request['target']['target_id'] == 'first':
            raise SyntaxError('malformed release source')
        return {'target_id': 'second', 'status': 'updated'}
    monkeypatch.setattr(O, 'run_operation', apply)
    result = O.update_registered(None, tmp_path / 'state')
    assert [entry['status'] for entry in result] == ['failed', 'updated']
    assert 'SyntaxError' in result[0]['error']


def test_identical_bytes_under_symlinked_directory_are_unsafe(tmp_path, monkeypatch):
    source, target, state, request = setup_install(tmp_path, monkeypatch, ('codex',))
    O.run_operation(request, state)
    scripts = package_path(target, 'codex') / 'scripts'
    relocated = tmp_path / 'relocated-scripts'
    scripts.rename(relocated)
    scripts.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        O.check_current(load_record(state, target['target_id']), state)
