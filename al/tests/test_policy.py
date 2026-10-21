"""Strict package ownership and conservative bounded activity."""
import json
from pathlib import Path
import pytest
from al.installer.engine import T
from al.installer.guards import scan_activity, strict_package
from al.installer.registry import records
from al.installer.targets import package_path, resolve_target


def test_identical_unmanaged_resource_refused(tmp_path):
    home = tmp_path / 'home'
    target = resolve_target(home=home, platforms=['codex'])
    path = package_path(target, 'codex') / 'SKILL.md'
    path.parent.mkdir(parents=True)
    path.write_text('same')
    with pytest.raises(ValueError, match='unmanaged'):
        strict_package(target, 'codex', {'SKILL.md': T.state(path)}, tmp_path / 'state')
    assert records(tmp_path / 'absent-state') == []
    assert not (tmp_path / 'absent-state').exists()


def session(path, *, status='APPROVED', phase='DONE', schema=2, writers=None, evidence=None):
    path.write_text(json.dumps({'schema_version': schema, 'session_id': path.stem, 'run_id': 'abc123',
                               'status': status, 'phase': phase, 'identity': {'root': str(path.parent.parent)},
                               'review_iteration': 0, 'correction_iteration': 0, 'handoffs': 0,
                               'task_package': {'sha256': 'a' * 64}, 'phase_timings': {}, 'completed_phases': [],
                               'phase_occurrences': [], 'review_history': [], 'amendments': [],
                               'writers': writers or {}, 'evidence': {'entries': evidence or [], 'plan': {}, 'seq': len(evidence or [])}}))


def test_all_sessions_not_only_current(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    session(runtime / 'done.json')
    session(runtime / 'waiting.json', status='WAITING_FOR_USER', phase='PLANNING')
    (runtime / 'current').write_text('done\n')
    report = scan_activity([runtime])
    assert len(report['blockers']) == 1
    assert report['blockers'][0]['path'].endswith('waiting.json')
    session(runtime / 'waiting.json')
    assert not scan_activity([runtime])['blockers']
    session(runtime / 'waiting.json', writers={'w': {'state': 'UNRESOLVED'}})
    assert scan_activity([runtime])['blockers']
    session(runtime / 'waiting.json', evidence=[{'status': 'RUNNING'}])
    assert scan_activity([runtime])['blockers']


@pytest.mark.parametrize('status,phase,schema', [('AGENT_FAILED','DONE',2), ('TRANSFERRED','DONE',2), ('APPROVED','DONE',3), ('APPROVED','VERIFYING',2)])
def test_unknown_or_resumable_states_block(tmp_path, status, phase, schema):
    session(tmp_path / 'run.json', status=status, phase=phase, schema=schema)
    assert scan_activity([tmp_path])['blockers']


def test_unsafe_runtime_root_and_dangling_pointer(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'current').write_text('missing')
    assert scan_activity([runtime])['blockers']
    link = tmp_path / 'link'
    link.symlink_to(runtime, target_is_directory=True)
    assert scan_activity([link])['blockers']


def test_malformed_approved_record_blocks(tmp_path):
    path = tmp_path / 'done.json'
    session(path)
    row = json.loads(path.read_bytes())
    row['task_package'] = {}
    path.write_text(json.dumps(row))
    assert scan_activity([tmp_path])['blockers']


def test_default_registry_discovery_and_explicit_override_precedence(tmp_path):
    import os
    import subprocess
    import sys

    home = tmp_path / 'home'
    default = home / '.local/state/al-distribution/registry'
    environment_root, flag_root = tmp_path / 'environment-root', tmp_path / 'flag-root'
    targets = []
    for index, root in enumerate((default, environment_root, flag_root)):
        root.mkdir(parents=True)
        target = str(home / f'legacy-{index}/.codex/skills/al')
        targets.append(target)
        (root / f'ownership-{index}.json').write_text(json.dumps(
            {'schema_version': 1, 'target': target, 'platform': 'codex'}))
    def snapshot():
        return {str(path): (path.read_bytes(), path.stat().st_mode & 0o777)
                for root in (home, environment_root, flag_root)
                for path in root.rglob('*') if path.is_file()}
    before = snapshot()
    command = Path(__file__).resolve().parents[1] / 'alctl.py'
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
    env.pop('AL_RUNTIME_DIR', None)
    env.pop('AL_DISTRIBUTION_STATE', None)
    def listing(*flags, environment=env):
        result = subprocess.run([sys.executable, '-B', str(command), 'list', '--json', *flags],
                                cwd=tmp_path, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(result.stdout)
        assert data['installations'] == []
        return [row['target'] for row in data['legacy']]
    assert listing() == [targets[0]]
    assert listing('--home', str(tmp_path / 'other-destination')) == [targets[0]]
    override = dict(env, AL_DISTRIBUTION_STATE=str(environment_root))
    assert listing(environment=override) == [targets[1]]
    assert listing('--state-root', str(flag_root), environment=override) == [targets[2]]
    assert snapshot() == before
    assert all(not (root / 'installer').exists() for root in (default, environment_root, flag_root))
