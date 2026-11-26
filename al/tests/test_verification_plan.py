"""New nodes run by default; no declaration can certify its own equivalence."""
import copy
from pathlib import Path

import pytest

from al.verification_plan import build_plan, collect_suites, environment
from al.verification_profile import capture_source_binding
from al.tests.verification_helpers import source, declarations, collected


def test_environment_forwards_only_explicit_go_executable_override(tmp_path, monkeypatch):
    executable = tmp_path / 'toolchain' / 'bin' / 'go'
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    monkeypatch.setenv('AL_TEST_GO_EXECUTABLE', str(executable))
    inherited_names = (
        'GOFLAGS', 'GOTOOLCHAIN', 'GOROOT', 'GOOS', 'GOARCH', 'GOENV', 'GOPATH',
        'GOPROXY', 'GOSUMDB', 'CGO_ENABLED', 'PYTHONSTARTUP', 'PYTHONHOME',
    )
    for name in inherited_names:
        monkeypatch.setenv(name, 'UNTRUSTED_' + name)

    child_env = environment(tmp_path, tmp_path / 'private-home')

    assert child_env['AL_TEST_GO_EXECUTABLE'] == str(executable)
    assert all(name not in child_env for name in inherited_names)


@pytest.mark.parametrize(
    'setting', ['relative', 'missing', 'directory', 'non-executable', 'overlong']
)
def test_environment_refuses_invalid_go_executable_override(tmp_path, monkeypatch, setting):
    if setting == 'relative':
        value = 'toolchain/bin/go'
    elif setting == 'missing':
        value = str(tmp_path / 'missing-go')
    elif setting == 'directory':
        value = str(tmp_path)
    elif setting == 'non-executable':
        path = tmp_path / 'not-executable'
        path.write_text('not executable\n')
        path.chmod(0o644)
        value = str(path)
    else:
        value = '/' + ('x' * 4096)
    monkeypatch.setenv('AL_TEST_GO_EXECUTABLE', value)

    with pytest.raises(ValueError) as failure:
        environment(tmp_path, tmp_path / 'private-home')

    assert str(failure.value) == 'invalid AL_TEST_GO_EXECUTABLE'
    assert value not in str(failure.value)


def test_complete_collection_ignores_inherited_selection_and_new_nodes_run(tmp_path, monkeypatch):
    root = source(tmp_path)
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k missing --ignore=tests')
    collections, binding = collected(root, tmp_path)
    plan = build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=binding)
    assert len(plan['collected_obligations']) == len(plan['execution_assignments']) == 3
    assert plan['complete_required'] and plan['environment_binding']['inherited_pytest_addopts_ignored']
    path = root / 'al/codex/skills/al/tests/test_a.py'
    path.write_text(path.read_text() + '\ndef test_new():\n    assert True\n')
    second = tmp_path / 'second';second.mkdir()
    collections = collect_suites(root, {'artifact_root': str(second)})
    plan = build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=capture_source_binding(root))
    assert len(plan['execution_assignments']) == 4


def test_equivalence_requires_complete_actual_fixture_and_source_equality(tmp_path):
    root = source(tmp_path)
    collections, binding = collected(root, tmp_path)
    declared = declarations(root)
    declared['shared'] = [{'omitted': {'suite': 'claude', 'nodeid': 'tests/test_a.py::test_a'},
                           'executed': {'suite': 'codex', 'nodeid': 'tests/test_a.py::test_a'},
                           'closure': ['scripts/pure.py', 'tests/conftest.py', 'tests/test_a.py'],
                           'reason': 'Pure function with identical actual fixtures and complete tiny implementation closure.'}]
    plan = build_plan(collections, declared, tier='full', changed_paths=[], binding=binding)
    assert len(plan['execution_assignments']) == 2 and len(plan['shared_equivalents']) == 1
    changed = copy.deepcopy(binding)
    changed['files']['al/claude/skills/al/tests/conftest.py']['sha256'] = 'changed'
    plan = build_plan(collections, declared, tier='full', changed_paths=[], binding=changed)
    assert len(plan['execution_assignments']) == 3 and plan['rejected_equivalences']
    declared['shared'][0]['closure'].remove('scripts/pure.py')
    assert not build_plan(collections, declared, tier='full', changed_paths=[], binding=binding)['shared_equivalents']


def test_fast_and_affected_never_claim_complete_unknown_changes_broaden(tmp_path):
    root = source(tmp_path);collections, binding = collected(root, tmp_path)
    declared = declarations(root)
    fast = build_plan(collections, declared, tier='fast', changed_paths=[], binding=binding)
    assert len(fast['execution_assignments']) == 1 and not fast['complete_required']
    affected = build_plan(collections, declared, tier='affected', changed_paths=['unknown/source.py'], binding=binding)
    assert len(affected['execution_assignments']) == 3 and not affected['complete_required']


def test_unsupported_repository_collection_filter_fails_closed(tmp_path):
    root = source(tmp_path)
    (root / 'al/tests/conftest.py').write_text('def pytest_collection_modifyitems(items):\n    items.clear()\n')
    directory = tmp_path / 'collection';directory.mkdir()
    with pytest.raises(ValueError, match='collection'):
        collect_suites(root, {'artifact_root': str(directory)})


def test_source_binding_detects_same_size_same_mtime_dirty_movement(tmp_path):
    import os
    root = source(tmp_path)
    before = capture_source_binding(root)
    path = root / 'al/codex/skills/al/scripts/pure.py';stat = path.stat()
    path.write_text('VALUE = 3\n');os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert capture_source_binding(root)['content_digest'] != before['content_digest']


def test_acceptance_tier_is_excluded_from_full_and_runs_only_its_declared_proofs(tmp_path):
    root = source(tmp_path);collections, binding = collected(root, tmp_path)
    declared = dict(declarations(root), acceptance=['codex:tests/test_a.py::*'])
    full = build_plan(collections, declared, tier='full', changed_paths=[], binding=binding)
    ids = {a['id'] for a in full['execution_assignments']}
    assert 'codex:tests/test_a.py::test_a' not in ids and len(ids) == 2 and full['complete_required']
    assert full['acceptance_obligations'] == ['codex:tests/test_a.py::test_a']
    assert 'codex:tests/test_a.py::test_a' in full['intentionally_unselected']
    acceptance = build_plan(collections, declared, tier='acceptance', changed_paths=[], binding=binding)
    assert [a['id'] for a in acceptance['execution_assignments']] == ['codex:tests/test_a.py::test_a']
    assert not acceptance['complete_required']


def _synthetic_collection(tmp_path, monkeypatch, *, report=True, code=1, payload=b'import helper failed\n', overrides=None, remove_log=False):
    import json
    from types import SimpleNamespace
    import al.verification_plan as plan
    import al.verification_process as process
    root = tmp_path / 'repo'; root.mkdir()
    artifact = tmp_path / 'artifacts'
    monkeypatch.setattr(plan, 'SUITES', {'fixture': ('.', 'tests')})
    monkeypatch.setattr(plan, 'allocate_verification_storage', lambda *args: (None, {'allocation': {'path': str(tmp_path / 'private')}}))
    monkeypatch.setattr(plan, 'record_verification_storage', lambda *args, **kwargs: {'complete': True})
    def execute(argv, **kwargs):
        kwargs['output'].write(payload)
        path = Path(argv[argv.index('--al-collection-report') + 1])
        if report:
            value = {'launch_token': 'owned-token', 'exitstatus': code, 'errors': [], 'deselected': [],
                     'nodes': [{'nodeid': 'tests/test_a.py::test_a'}], 'finalized': True,
                     'effective_config': {}, 'pytest_version': 'fixture'}
            value.update(overrides or {})
            path.write_text(json.dumps(value))
        if remove_log:(kwargs['directory'] / 'collection.log').unlink()
        return SimpleNamespace(process=SimpleNamespace(returncode=code), token='owned-token')
    monkeypatch.setattr(process, 'execute', execute)
    return lambda: plan.collect_suites(root, {'artifact_root': str(artifact)}), artifact


@pytest.mark.parametrize('report,overrides,category', [
    (False, {}, 'missing collection report'),
    (True, {'finalized': False}, 'incomplete/unfiltered collection'),
    (True, {'errors': ['import failure']}, 'incomplete/unfiltered collection'),
])
def test_collection_error_keeps_child_context_after_cleanup(tmp_path, monkeypatch, report, overrides, category):
    import shutil
    run, artifact = _synthetic_collection(tmp_path, monkeypatch, report=report, overrides=overrides)
    with pytest.raises(ValueError) as failure:run()
    message = str(failure.value)
    shutil.rmtree(artifact)
    assert category in message and 'fixture' in message and 'collection.log' in message
    assert 'import helper failed' in message


def test_collection_excerpt_is_bounded_replacement_decoded_and_display_safe(tmp_path, monkeypatch):
    payload = b'ImportError: helper\xff\x1b[2J\r\x00' + '\u202e'.encode() + b'x' * 20000
    run, _ = _synthetic_collection(tmp_path, monkeypatch, payload=payload)
    with pytest.raises(ValueError) as failure:run()
    message = str(failure.value)
    assert 'ImportError: helper' in message and '\ufffd' in message
    assert '\x1b' not in message and '\r' not in message and '\x00' not in message and '\u202e' not in message
    assert 'truncated' in message and len(message) < 10000


def test_collection_missing_log_preserves_original_failure(tmp_path, monkeypatch):
    run, _ = _synthetic_collection(tmp_path, monkeypatch, report=False, remove_log=True)
    with pytest.raises(ValueError) as failure:run()
    assert 'missing collection report' in str(failure.value)
    assert 'unavailable' in str(failure.value)


def test_synthetic_successful_collection_is_unchanged(tmp_path, monkeypatch):
    run, _ = _synthetic_collection(tmp_path, monkeypatch, code=0)
    result = run()
    assert result['fixture']['nodes'] == [{'nodeid': 'tests/test_a.py::test_a'}]
    assert result['fixture']['storage_result'] == {'complete': True}


def test_collection_unreadable_log_preserves_original_failure(tmp_path, monkeypatch):
    import al.verification_plan as plan
    run, _ = _synthetic_collection(tmp_path, monkeypatch, report=False)
    real_open = plan.os.open
    def unreadable(path, *args, **kwargs):
        if Path(path).name == 'collection.log':raise PermissionError('fixture denied\nunsafe')
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(plan.os, 'open', unreadable)
    with pytest.raises(ValueError) as failure:run()
    message = str(failure.value)
    assert 'missing collection report' in message and 'PermissionError' in message
    assert 'fixture denied\\x0aunsafe' in message


@pytest.mark.parametrize('overrides,category', [
    ({'nodes': [None]}, 'invalid collection node shape'),
    ({'nodes': [{'nodeid': []}]}, 'invalid collection node shape'),
    ({'nodes': [{'nodeid': 'same'}, {'nodeid': 'same'}]}, 'duplicate collection identity'),
    ({'launch_token': 'foreign'}, 'collection launch token mismatch'),
])
def test_invalid_collection_keeps_bounded_context(tmp_path, monkeypatch, overrides, category):
    run, _ = _synthetic_collection(tmp_path, monkeypatch, code=0, overrides=overrides)
    with pytest.raises(ValueError) as failure:run()
    assert category in str(failure.value) and 'import helper failed' in str(failure.value)


def test_collection_display_expansion_is_bounded(tmp_path, monkeypatch):
    run, _ = _synthetic_collection(tmp_path, monkeypatch, payload=b'\x00' * 4096)
    with pytest.raises(ValueError) as failure:run()
    message = str(failure.value)
    assert 'truncated=true' in message and len(message) < 10000


@pytest.mark.parametrize('stage', ['namespace initialization', 'allocation'])
def test_storage_permission_refusal_names_namespace_and_preserves_failure(tmp_path, monkeypatch, stage):
    import errno
    from types import SimpleNamespace
    import al.verification_plan as plan
    from al.installer import storage_lifecycle

    home = tmp_path / 'private-harness'
    root = home / 'managed-storage'
    monkeypatch.setenv('HARNESS_HOME', str(home))
    denied = PermissionError(errno.EPERM, 'Operation not permitted', 'lock')
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise denied

    if stage == 'namespace initialization':
        monkeypatch.setattr(storage_lifecycle, 'StorageManager', fail)
        command = plan.verification_storage_manager
    else:
        manager = SimpleNamespace(root=root, allocate=fail)
        monkeypatch.setattr(plan, 'verification_storage_manager', lambda: manager)
        command = lambda: plan.allocate_verification_storage(tmp_path / 'artifacts', 'fixture')
    before = set(tmp_path.rglob('*'))
    with pytest.raises(PermissionError) as failure:
        command()
    assert failure.value.errno == errno.EPERM and failure.value.filename == 'lock'
    assert failure.value.__cause__ is denied
    message = str(failure.value)
    assert stage in message and str(root) in message
    assert 'host-reviewed execution context' in message and 'UV_CACHE_DIR' in message
    assert 'cleanup obligations' in message and 'no automatic retry' in message
    assert len(calls) == 1 and set(tmp_path.rglob('*')) == before


def test_storage_integrity_refusal_is_not_reclassified_as_permission(tmp_path, monkeypatch):
    from al.installer import storage_lifecycle
    from al.installer.storage_filesystem import StorageError
    import al.verification_plan as plan

    monkeypatch.setenv('HARNESS_HOME', str(tmp_path / 'private-harness'))
    denied = StorageError('unsafe_lock')

    def fail(*args, **kwargs):
        raise denied

    monkeypatch.setattr(storage_lifecycle, 'StorageManager', fail)
    with pytest.raises(StorageError) as failure:
        plan.verification_storage_manager()
    assert failure.value is denied
