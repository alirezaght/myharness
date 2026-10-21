"""Tiny genuine three-suite source trees for the actual subprocess runner."""
from pathlib import Path
import hashlib

from al.verification_profile import capture_source_binding
from al.verification_plan import collect_suites
from al.verification_isolation import closure_proof


def source(tmp_path, bodies=None):
    root = tmp_path / 'source';root.mkdir()
    bodies = bodies or {}
    for suite, relative in [('installer', 'al'), ('codex', 'al/codex/skills/al'), ('claude', 'al/claude/skills/al')]:
        directory = root / relative / 'tests';directory.mkdir(parents=True)
        (directory / 'conftest.py').write_text('')
        (directory / 'test_a.py').write_text(bodies.get(suite, 'def test_a():\n    assert 1 + 1 == 2\n'))
        if suite != 'installer':
            scripts = root / relative / 'scripts';scripts.mkdir()
            (scripts / 'pure.py').write_text('VALUE = 2\n')
    return root


def declarations(root):
    files = {}
    for suite, relative in [('installer', 'al'), ('codex', 'al/codex/skills/al'), ('claude', 'al/claude/skills/al')]:
        directory = root / relative / 'tests'
        files[suite + ':' + ('al/tests/test_a.py' if suite == 'installer' else 'tests/test_a.py')] = {
            'test': hashlib.sha256((directory / 'test_a.py').read_bytes()).hexdigest(),
            'fixtures': {'sha256': hashlib.sha256(b'').hexdigest(), 'mode': 0o644}}
    return {'schema_version': 1, 'shared': [], 'fast': ['installer:*'], 'affected': [], 'isolated_files': files,
            'classified_nodes': [suite + ':' + ('al/tests/test_a.py' if suite == 'installer' else 'tests/test_a.py') + '::test_a' for suite in ('installer', 'codex', 'claude')],
            'isolation_closure': closure_proof(capture_source_binding(root))}


def collected(root, tmp_path):
    directory = tmp_path / 'collection';directory.mkdir()
    return collect_suites(root, {'artifact_root': str(directory)}), capture_source_binding(root)
