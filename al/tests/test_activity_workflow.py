"""Actual new public workflow metadata remains recognizable without target-code imports."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from al.installer.activity_inventory import inventory_activity
from al.tests.activity_helpers import git_repo
from al.tests.conftest import copy_al_source
from al.tests.planning_reports import report_for

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module', params=['codex', 'claude'])
def workflow_case(tmp_path_factory, request):
    platform = request.param
    root = tmp_path_factory.mktemp('workflow-records') / 'project'
    identity = git_repo(root)
    (root / 'src').mkdir()
    (root / 'src/app.py').write_text('VALUE=1\n')
    (root / 'pyproject.toml').write_text('[project]\nname="workflow-fixture"\ndependencies=[]\n[tool.setuptools.packages.find]\nwhere=["src"]\n[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n')
    for args in (('add', '.'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'source')):
        subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
    runtime = root / ('.' + platform) / 'al-runtime'
    # New runs are admitted only through the protected managed launcher.
    home = root.parent / 'home';home.mkdir()
    source = copy_al_source(root.parent / 'source/al')
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('AL_', 'CLAUDE_', 'CODEX_', 'PYTHON'))}
    environment.update(HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
    if platform == 'claude':environment['CLAUDE_PROJECT_DIR'] = str(root)
    launcher = home / 'bin/alctl'
    boot = subprocess.run([sys.executable, '-I', '-S', '-B', str(source / 'alctl.py'), 'bootstrap', '--source-root', str(source),
                           '--destination', str(launcher), '--state-root', str(home / 'installer')],
                          cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert boot.returncode == 0, boot.stdout[-2000:] + boot.stderr[-2000:]
    def cli(*args):
        process = subprocess.run([str(launcher), 'run', '--repository', str(root), '--platform', platform,
                                  '--session', 'workflow-format', '--', *args], cwd=root,
                                 env=environment, capture_output=True, text=True, timeout=60)
        assert process.returncode == 0, process.stdout[-2000:] + process.stderr[-2000:]
        return process.stdout
    cli('start', '--spec', 'Verify actual content attribution and reserved gate workflow metadata.')
    cli('spec-gate', 'SPEC_READY')
    cli('classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    report = root.parent / 'architect-planning-report.json'
    report.write_text(json.dumps(report_for(json.loads(cli('planning', 'packet')))))
    saved = json.loads(cli('planning', 'report', '--file', str(report)))
    cli('planning', 'admit', '--file', saved['materialized_obligations']['stored_path'])
    paths = root.parent / 'paths.json';paths.write_text('["src"]')
    manifest = json.loads(cli('gates', 'discover', '--paths-file', str(paths)))
    for gate in manifest['gates']:
        if gate['category'] != 'lint-imports':
            gate.update(applicability='NOT_APPLICABLE', required=False, source=manifest['rules'][0],
                        reason='Independently inspected disposable project has no required check in this category.')
    file = root.parent / 'manifest.json';file.write_text(json.dumps(manifest))
    cli('gates', 'plan', '--file', str(file))
    cli('gates', 'run', '--stage', 'base')
    cli('phase', 'IMPLEMENTATION')
    cli('writer', 'start', '--id', 'repair', '--role', 'al-engineer', '--files', 'src')
    (root / 'src/app.py').write_text('VALUE=2\n')
    cli('writer', 'finish', '--id', 'repair', '--outcome', 'success')
    (root / 'src/app.py').write_text('VALUE=3\n')
    state_path = runtime / 'workflow-format.json'
    state = json.loads(state_path.read_bytes())
    view = json.loads(cli('inspect', '--expect-run', state['run_id'], '--expect-repo', str(root), '--sections', 'attribution'))
    attribution = view['sections']['attribution']
    request_file = root.parent / 'reconciliation.json'
    request_file.write_text(json.dumps({'schema_version': 1, 'kind': 'task-attribution-reconciliation-request-v1',
        'binding': attribution['reconciliation_binding'], 'paths': ['src/app.py'],
        'source': 'Synthetic explicit supervisor reconciliation.', 'reason': 'Preserve the externally changed candidate and its verification scope.'}))
    cli('task', 'reconcile-attribution', '--file', str(request_file))
    architecture = next(g for g in manifest['gates'] if g['category'] == 'lint-imports' and g['argv'])
    cli('verification', 'prepare', '--gate', architecture['id'], '--stage', 'candidate')
    cli('gates', 'run', '--stage', 'candidate', '--resume')
    return {'root': root, 'runtime': runtime, 'state_path': state_path, 'identity': identity, 'platform': platform}


def inventory(case):
    state = json.loads(case['state_path'].read_bytes())
    root = {'repository': case['identity'], 'platform': case['platform'], 'runtime_root': str(case['runtime'])}
    invoking = {**root, 'state_path': str(case['state_path']), 'session_id': state['session_id'], 'run_id': state['run_id']}
    return inventory_activity([root], invoking_run=invoking)


def test_actual_public_attribution_contribution_reconciliation_and_reservation_profile(workflow_case):
    result = inventory(workflow_case)
    assert result['stable'], result['roots']
    record = result['roots'][0]['records'][0]
    assert record['classification'] == 'current-session' and not record['errors']
    assert record['historical_status'] == 'RUNNING'  # Recognition is no approval/eligibility.
    state = json.loads(workflow_case['state_path'].read_bytes())
    assert state['task_attribution']['contributions'] and state['task_attribution']['reconciliations']
    assert state['writers']['repair']['contribution_before']
    assert state['verification_preparations']
    assert all(e['gate_reservation'] for e in state['evidence']['entries'])


@pytest.mark.parametrize('damage', ['unknown-registry', 'foreign-reservation', 'writer-reference', 'preparation-reference', 'blob-mode', 'blob-bytes'])
def test_new_workflow_metadata_drift_stays_unknown(workflow_case, damage):
    path = workflow_case['state_path'];raw = path.read_bytes();mode = path.stat().st_mode & 0o777
    state = json.loads(raw);restore = None
    try:
        if damage == 'unknown-registry':state['task_attribution']['live_lease'] = {'pid': os.getpid()}
        elif damage == 'foreign-reservation':state['evidence']['entries'][0]['gate_reservation']['manifest'] = 'f' * 64
        elif damage == 'writer-reference':state['writers']['repair']['contribution_before']['kind'] = 'task-source-content-v1'
        elif damage == 'preparation-reference':state['verification_preparations'][0]['kind'] = 'task-content-capture-v1'
        else:
            snapshot = json.loads(Path(state['task_attribution']['snapshot']['stored_path']).read_bytes())
            capture = json.loads(Path(snapshot['baseline_capture_ref']['stored_path']).read_bytes())
            blob = Path(capture['contents']['src/app.py']['stored_path'])
            restore = (blob, blob.read_bytes(), blob.stat().st_mode & 0o777)
            if damage == 'blob-mode':blob.chmod(0o644)
            else:blob.write_bytes(b'changed retained source')
        if damage not in {'blob-mode', 'blob-bytes'}:path.write_text(json.dumps(state))
        result = inventory(workflow_case)
        assert not result['stable']
        assert result['roots'][0]['records'][0]['classification'] == 'unknown'
    finally:
        path.write_bytes(raw);path.chmod(mode)
        if restore:
            blob, before, before_mode = restore;blob.write_bytes(before);blob.chmod(before_mode)


PINNED_DAMAGE = ['origin-run', 'origin-kind', 'initialization-outside-pins', 'initialization-bytes',
                 'initialization-mismatch', 'admission-without-origin', 'admission-status']


@pytest.mark.parametrize('damage', PINNED_DAMAGE)
def test_pinned_run_origin_and_initialization_drift_stays_unknown(workflow_case, damage, tmp_path):
    """Pinned runs are recognized only through their exact protected launcher binding."""
    path = workflow_case['state_path'];raw = path.read_bytes();mode = path.stat().st_mode & 0o777
    state = json.loads(raw);restore = None
    origin, admission = state['runtime_origin'], state['proof_admission']
    initialization = Path(origin['initialization']['stored_path'])
    try:
        if damage == 'origin-run':origin['run_id'] = 'f' * 8
        elif damage == 'origin-kind':origin['kind'] = 'run-pin-binding-v2'
        elif damage == 'initialization-outside-pins':
            moved = tmp_path / 'initializations' / initialization.name
            moved.parent.mkdir();moved.write_bytes(initialization.read_bytes())
            origin['initialization']['stored_path'] = admission['initialization']['stored_path'] = str(moved)
        elif damage == 'initialization-bytes':
            restore = (initialization, initialization.read_bytes(), initialization.stat().st_mode & 0o777)
            initialization.chmod(0o600);initialization.write_bytes(initialization.read_bytes() + b' ')
        elif damage == 'initialization-mismatch':admission['initialization'] = dict(admission['initialization'], sha256='0' * 64)
        elif damage == 'admission-without-origin':del state['runtime_origin']
        else:admission['status'] = 'forged'
        if damage != 'initialization-bytes':path.write_text(json.dumps(state))
        result = inventory(workflow_case)
        assert not result['stable']
        assert result['roots'][0]['records'][0]['classification'] == 'unknown'
    finally:
        path.write_bytes(raw);path.chmod(mode)
        if restore:
            target, before, before_mode = restore;target.write_bytes(before);target.chmod(before_mode)


def test_installer_v2_record_profiles_match_both_runtime_distributions():
    import importlib.util
    from al.installer.activity_workflow import EXECUTION, PREPARATIONS
    for platform in ('codex', 'claude'):
        spec = importlib.util.spec_from_file_location(
            'versions_' + platform, REPO / 'al' / platform / 'skills/al/scripts/al_execution_versions.py',
            submodule_search_locations=None)
        sys.path.insert(0, str(REPO / 'al' / platform / 'skills/al/scripts'))
        try:
            module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        finally:
            sys.path.pop(0);sys.modules.pop('al_verification_inputs', None)
        fields = module.V2_FIELDS
        assert PREPARATIONS['verification-preparation-v2'] == fields['verification-preparation']
        assert EXECUTION['execution-receipt-v2'] == fields['execution-receipt']
        assert EXECUTION['execution-association-v2'] == fields['execution-association']
