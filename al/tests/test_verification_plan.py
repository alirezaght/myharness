"""New nodes run by default; no declaration can certify its own equivalence."""
import copy

import pytest

from al.verification_plan import build_plan, collect_suites
from al.verification_profile import capture_source_binding
from al.tests.verification_helpers import source, declarations, collected


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
