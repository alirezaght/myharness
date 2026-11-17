# ruff: noqa: F811
"""Standalone successor diagnostics preserve old origins and require fresh proof."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from al.tests.runtime_run_helpers import invoke, launched, public_run, run_record  # noqa: F401
from tests.conftest import conn, controller, db_path, isolated_home  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401


def files(root):
    return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o777)
            for path in root.rglob('*') if path.is_file()}


def start_standalone(launched, platform):
    result = public_run(launched, platform, 'start', '--spec',
                        'Preserve accepted standalone requirements while rebuilding verification in a fresh run.')
    assert result.returncode == 0, result.stdout + result.stderr
    return run_record(launched)


@pytest.mark.parametrize('platform', ['codex', 'claude'])
def test_standalone_inputs_has_typed_guidance_and_origin_stays_unchanged(launched, platform, tmp_path):
    _, home, repo, launcher, env = launched
    path, binding = start_standalone(launched, platform)
    state_path = repo / ('.' + platform) / 'al-runtime/pin-flow.json'
    state_before, binding_before = state_path.read_bytes(), path.read_bytes()
    pins_before = files(home / 'installer/al-run-pins')
    arguments = [launcher, 'runtime-successor', 'inputs', '--repository', repo, '--platform', platform,
                 '--session', 'pin-flow', '--request-id', 'standalone-guidance', '--authority-ref', '{}']
    result = invoke(arguments, repo, env)
    (tmp_path / 'standalone-inputs-receipt.json').write_text(json.dumps({
        'argv': list(map(str, arguments)), 'returncode': result.returncode,
        'stdout': result.stdout, 'stderr': result.stderr,
        'scope': 'unsupported-layout diagnostic only; empty authority reference was not authenticated',
    }, indent=2) + '\n')
    assert result.returncode == 1, result.stdout + result.stderr
    diagnostic = json.loads(result.stdout)
    assert diagnostic['status'] == 'failed'
    assert 'UNSUPPORTED_RUNTIME_SUCCESSOR_MODE' in diagnostic['error']
    assert 'standalone pin migration is unsupported' in diagnostic['error']
    assert 'runtime-successor origin' in diagnostic['error'] and 'runtime-prepare' in diagnostic['error']
    assert state_path.read_bytes() == state_before and path.read_bytes() == binding_before
    pins_after = files(home / 'installer/al-run-pins')
    assert all(pins_after.get(name) == value for name, value in pins_before.items())
    assert not (home / 'installer/al-run-pins/successors/standalone-guidance').exists()
    origin = invoke([launcher, 'runtime-successor', 'origin', '--repository', repo,
                     '--platform', platform, '--session', 'pin-flow'], repo, env)
    assert origin.returncode == 0, origin.stdout + origin.stderr
    assert json.loads(origin.stdout)['transaction'] == binding
    assert state_path.read_bytes() == state_before and path.read_bytes() == binding_before
    pins_after = files(home / 'installer/al-run-pins')
    assert all(pins_after.get(name) == value for name, value in pins_before.items())


@pytest.mark.parametrize('integration', [None, [], {}, {'binding': None}, {'binding': {}},
    {'binding': {'execution_id': 'fixture'}}, {'binding': {'execution_id': 'fixture'}, 'handoff': None}])
def test_unsupported_integration_layout_is_a_typed_error(integration):
    from al.installer.runtime_successor import UnsupportedRuntimeSuccessorMode, _successor_integration
    with pytest.raises(UnsupportedRuntimeSuccessorMode) as error:
        _successor_integration({'integration': integration})
    assert error.value.code == 'UNSUPPORTED_RUNTIME_SUCCESSOR_MODE'


def test_supported_integration_layout_is_not_rewritten_or_authorized():
    from al.installer.runtime_successor import _successor_integration
    value = {'binding': {'execution_id': 'fixture'}, 'handoff': '/fixture/handoff.json', 'other': 'preserved'}
    assert _successor_integration({'integration': value}) is value


@pytest.mark.parametrize('platform', ['codex', 'claude'])
def test_fresh_standalone_uses_preserved_package_without_importing_verification(launched, platform):
    _, home, repo, launcher, env = launched
    path, binding = start_standalone(launched, platform)
    check = public_run(launched, platform, 'evidence', 'run', '--area', 'historical-check', '--kind', 'check',
                       '--scope', '.', '--', sys.executable, '-c', 'print("actual historical check")')
    assert check.returncode == 0, check.stdout + check.stderr
    state_path = repo / ('.' + platform) / 'al-runtime/pin-flow.json'
    original = state_path.read_bytes()
    old = json.loads(original)
    assert old['evidence']['entries'][-1]['status'] == 'PASS'
    old_pin = home / 'installer/al-run-pins/pins' / binding['pin']['pin_identity']
    before = files(old_pin)
    result = invoke([launcher, 'run', '--repository', repo, '--platform', platform, '--session', 'fresh-guidance',
                     '--', 'start', '--package-file', old['task_package']['stored_path']], repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    fresh = json.loads((repo / ('.' + platform) / 'al-runtime/fresh-guidance.json').read_bytes())
    assert fresh['run_id'] != old['run_id'] and fresh['task_package']['sha256'] == old['task_package']['sha256']
    assert fresh['evidence']['entries'] == [] and fresh['evidence']['plan'] == {}
    assert fresh['proof_admission']['status'] == 'pending' and fresh['spec_status'] is None
    assert state_path.read_bytes() == original and json.loads(path.read_bytes()) == binding
    assert files(old_pin) == before


@pytest.mark.parametrize('action,arguments', [
    ('runtime-prepare', ['--expected-epoch', 'legacy-0', '--expected-authority-generation', '0',
                         '--reason', 'Repair runtime while preserving accepted requirements']),
    ('runtime-inspect', []),
    ('runtime-activate', ['--preparation-sha256', 'a' * 64, '--expected-epoch', 'legacy-0',
                          '--expected-authority-generation', '0']),
])
def test_documented_harness_operator_grammar_without_opening_database(tmp_path, action, arguments):
    from harness.cli import build_parser
    database = tmp_path / 'not-opened.db'
    args = build_parser().parse_args(['--db', str(database), 'execution', action, 'task-fixture',
                                      'execution-fixture', '--request-id', 'runtime-fixture', *arguments])
    assert args.action == action and args.request_id == 'runtime-fixture'
    assert args.task_id == 'task-fixture' and args.execution_id == 'execution-fixture'
    assert not database.exists()


def test_genuine_harness_inputs_still_authenticate_prepared_authority(registered):
    from harness.execution_bootstrap import prepared_authority_snapshot
    _, _, _, initialization, package, execution, envelope = registered[0]
    original_path = Path(initialization['source'])
    original = original_path.read_bytes()
    authority = prepared_authority_snapshot(Path(execution['handoff_path']).parent)
    common = [package['launcher'], 'runtime-successor', 'inputs', '--repository', envelope['repository'],
              '--platform', execution['host'], '--session', execution['al_session_id']]
    result = invoke([*common, '--request-id', 'genuine-guidance', '--authority-ref', json.dumps(authority)],
                    envelope['repository'], dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
    inputs = json.loads(result.stdout)
    assert inputs['predecessor_subject'] == {'session_id': execution['al_session_id'], 'run_id': execution['al_run_id']}
    assert inputs['predecessor_state_sha256'] == hashlib.sha256(original).hexdigest()
    invalid = dict(authority, sha256='0' * 64)
    refused = invoke([*common, '--request-id', 'tampered-guidance', '--authority-ref', json.dumps(invalid)],
                     envelope['repository'], dict(os.environ))
    assert refused.returncode != 0 and 'prepared Harness authority record changed' in refused.stdout
    assert 'UNSUPPORTED_RUNTIME_SUCCESSOR_MODE' not in refused.stdout
    assert original_path.read_bytes() == original
