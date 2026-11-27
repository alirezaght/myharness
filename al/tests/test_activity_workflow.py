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
    environment.update(HOME=str(home), PYTHONDONTWRITEBYTECODE='1', AL_SERVICE='0')
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
    paths = root.parent / 'paths.json';paths.write_text('["src"]')
    manifest = json.loads(cli('gates', 'discover', '--paths-file', str(paths)))
    for gate in manifest['gates']:
        if gate['category'] != 'lint-imports':
            gate.update(applicability='NOT_APPLICABLE', required=False, source=manifest['rules'][0],
                        reason='Independently inspected disposable project has no required check in this category.')
    file = root.parent / 'manifest.json';file.write_text(json.dumps(manifest))
    cli('gates', 'plan', '--file', str(file))
    cli('gates', 'run', '--stage', 'base')
    report = root.parent / 'architect-planning-report.json'
    report.write_text(json.dumps(report_for(json.loads(cli('planning', 'packet')))))
    saved = json.loads(cli('planning', 'report', '--file', str(report)))
    cli('planning', 'admit', '--file', saved['materialized_obligations']['stored_path'])
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
    case = {'root': root, 'runtime': runtime, 'state_path': state_path, 'identity': identity, 'platform': platform}
    observed = inventory(case)
    assert observed['stable'], json.dumps(observed, indent=2)
    assert observed['roots'][0]['records'][0]['classification'] == 'current-session'
    return case


def inventory(case):
    state = json.loads(case['state_path'].read_bytes())
    root = {'repository': case['identity'], 'platform': case['platform'], 'runtime_root': str(case['runtime'])}
    invoking = {**root, 'state_path': str(case['state_path']), 'session_id': state['session_id'], 'run_id': state['run_id']}
    return inventory_activity([root], invoking_run=invoking)


def test_actual_public_attribution_contribution_reconciliation_and_reservation_profile(workflow_case):
    # This metadata journey must not start a detached runtime service.
    assert not (workflow_case['root'].parent / 'home/installer/al-services').exists()
    result = inventory(workflow_case)
    assert result['stable'], json.dumps(result, indent=2)
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
            # Since #25 (5d3ce78) unchanged source is held by the capture's pinned snapshot tree, not a
            # retained blob; the capture record is the retained artifact the baseline references.
            blob = Path(snapshot['baseline_capture_ref']['stored_path'])
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


@pytest.mark.parametrize('damage', ['version', 'mapping', 'foreign-run', 'producer', 'digest',
                                   'compact-reference', 'foreign-specification', 'archive-family',
                                   'payload-list', 'payload-null', 'archive-spec-list',
                                   'planning-list', 'planning-report-null', 'planning-history-row',
                                   'reconciliation-list'])
def test_companion_record_damage_stays_unknown(workflow_case, damage):
    path = workflow_case['state_path'];raw = path.read_bytes()
    state = json.loads(raw)
    contracts = state['agent_contracts']
    key = next(k for k in contracts if k.startswith('requirement-analysis:'))
    value = contracts[key]
    try:
        if damage == 'payload-list':value['payload'] = []
        elif damage == 'payload-null':value['payload'] = None
        elif damage == 'archive-spec-list':
            import copy
            archived = copy.deepcopy(value);archived['source_specification'] = []
            state['agent_contract_history'] = [archived]
        elif damage == 'planning-list':state['planning_authorship'] = []
        elif damage == 'planning-report-null':state['planning_authorship']['report'] = None
        elif damage == 'planning-history-row':state['planning_history'] = [[]]
        elif damage == 'reconciliation-list':state['evidence']['entries'][0]['reconciliation'] = ['malformed']
        elif damage == 'version':value['schema_version'] = 2
        elif damage == 'mapping':contracts[key + '-foreign'] = contracts.pop(key)
        elif damage == 'foreign-run':value['context']['al_run'] = 'foreign-run'
        elif damage == 'producer':
            plan = next(v for v in contracts.values() if v['kind'] == 'implementation-plan')
            plan['context']['producer'] = 'foreign-producer'
        elif damage == 'digest':value['payload']['record_digest'] = 'f' * 64
        elif damage == 'compact-reference':
            value['required_inputs'][0]['reference']['id'] = 'missing-declared-reference'
        elif damage == 'foreign-specification':value['source_specification']['reference']['id'] = 'foreign-spec'
        else:state['agent_contract_history'] = [next(v for v in contracts.values() if v['kind'] == 'verification-result')]
        path.write_text(json.dumps(state))
        result = inventory(workflow_case)
        assert not result['stable']
        assert result['roots'][0]['records'][0]['classification'] == 'unknown'
    finally:
        path.write_bytes(raw)


def test_archived_analysis_remains_historical_not_approval(workflow_case):
    path = workflow_case['state_path'];raw = path.read_bytes()
    state = json.loads(raw)
    try:
        # refresh_analysis archives this exact earlier observation before replacement.
        state['agent_contract_history'] = [next(v for v in state['agent_contracts'].values()
                                               if v['kind'] == 'requirement-analysis')]
        path.write_text(json.dumps(state))
        result = inventory(workflow_case)
        assert result['stable'], json.dumps(result, indent=2)
        record = result['roots'][0]['records'][0]
        assert record['classification'] == 'current-session'
        assert record['historical_status'] == 'RUNNING'
    finally:
        path.write_bytes(raw)


@pytest.mark.parametrize('damage', [None, 'original-digest', 'observation-bytes', 'missing-observation',
                                    'retained-list', 'retained-original-null', 'retained-contract-payload-list'])
def test_retained_verification_reconciliation_is_checked(workflow_case, damage):
    import copy
    from al.installer import activity_contract_schema as C

    path = workflow_case['state_path'];raw = path.read_bytes()
    state = json.loads(raw);created = []

    def snapshot(value, kind):
        data = json.dumps(value, sort_keys=True).encode()
        digest = __import__('hashlib').sha256(data).hexdigest()
        artifact = workflow_case['runtime'] / state['session_id'] / 'artifacts' / digest
        assert not artifact.exists()  # Never overwrite a pre-existing fixture artifact.
        artifact.write_bytes(data);artifact.chmod(0o600);created.append(artifact)
        return {'id': digest, 'kind': kind, 'sha256': digest, 'stored_path': str(artifact),
                'source': 'owned observer reconciliation fixture', 'captured_at': state['updated_at']}

    try:
        entry = state['evidence']['entries'][0]
        key = 'verification-result:' + entry['id']
        old_contract = copy.deepcopy(state['agent_contracts'][key])
        retained = {'schema_version': 1, 'entry': copy.deepcopy(entry), 'contract': old_contract}
        if damage == 'original-digest':old_contract['payload']['record_digest'] = 'f' * 64
        elif damage == 'retained-list':retained = []
        elif damage == 'retained-original-null':retained['entry'] = None
        elif damage == 'retained-contract-payload-list':old_contract['payload'] = []
        observation = snapshot(retained, 'check-observation-v1')
        artifact = snapshot({'reason': 'private synthetic interrupted-check observation'}, 'check-reconciliation')
        entry.update(status='INFRA', note='interrupted check explicitly reconciled',
                     reconciliation={'schema_version': 1, 'reason': 'owned observer fixture',
                                     'artifact': artifact, 'observation': observation})
        value = state['agent_contracts'][key]
        value['payload']['record_digest'] = C.fingerprint(entry)
        value['evidence_refs'] = [{'reference': C.ref(ref), 'purpose': 'retained observation', 'required': True}
                                  for ref in (observation, artifact)]
        value['field_applicability'].pop('evidence_refs', None)
        C.validate(value)
        if damage == 'observation-bytes':Path(observation['stored_path']).write_bytes(b'corrupt')
        elif damage == 'missing-observation':Path(observation['stored_path']).unlink()
        path.write_text(json.dumps(state))
        result = inventory(workflow_case)
        if damage is None:
            assert result['stable'], json.dumps(result, indent=2)
            record = result['roots'][0]['records'][0]
            assert record['classification'] == 'current-session'
            assert record['historical_status'] == 'RUNNING'
        else:
            assert not result['stable']
            assert result['roots'][0]['records'][0]['classification'] == 'unknown'
    finally:
        path.write_bytes(raw)
        for artifact in created:
            artifact.unlink(missing_ok=True)


@pytest.mark.parametrize('shape', ['linux', 'harness', 'local-unavailable', 'legacy-absent'])
def test_historical_process_profiles_preserve_recognition_only(workflow_case, shape):
    from al.installer import activity_contract_schema as C
    path = workflow_case['state_path'];raw = path.read_bytes();state = json.loads(raw)
    entry = state['evidence']['entries'][0]
    try:
        if shape == 'linux':
            entry['process_observation'] = {'schema_version': 1, 'mode': 'local',
                'domain': {'platform': 'linux', 'boot': '12345678-1234-1234-1234-123456789abc', 'namespace': [1, 2]},
                'owner': {'pid': 1, 'pgid': 1, 'sid': 1, 'start': [1], 'zombie': False}, 'children': [None]}
        elif shape == 'harness':
            entry['process_observation'] = {'schema_version': 1, 'mode': 'harness',
                'host_instance': 'a' * 48, 'owner_ref': 'b' * 48,
                **{k: entry[k] for k in ('run', 'id', 'reservation')}}
        elif shape == 'local-unavailable':
            entry['process_observation']['owner'] = None
            entry['process_observation']['children'] = None
        else:
            entry.pop('process_observation');entry.pop('purpose')
        state['agent_contracts']['verification-result:' + entry['id']]['payload']['record_digest'] = C.fingerprint(entry)
        path.write_text(json.dumps(state))
        result = inventory(workflow_case)
        assert result['stable'], json.dumps(result, indent=2)
        record = result['roots'][0]['records'][0]
        assert record['classification'] == 'current-session' and record['historical_status'] == 'RUNNING'
        assert all('process_observation' not in check for check in record['operation_facts']['checks'])
    finally:
        path.write_bytes(raw)


@pytest.mark.parametrize('damage', ['purpose-list', 'purpose-unknown', 'observation-null', 'observation-version',
                                   'domain-extra', 'domain-boot', 'owner-bool', 'child-shape',
                                   'harness-binding', 'harness-selector'])
def test_historical_process_profile_damage_is_unknown(workflow_case, damage):
    from al.installer import activity_contract_schema as C
    path = workflow_case['state_path'];raw = path.read_bytes();state = json.loads(raw)
    entry = state['evidence']['entries'][0]
    try:
        observation = entry['process_observation']
        if damage == 'purpose-list':entry['purpose'] = []
        elif damage == 'purpose-unknown':entry['purpose'] = 'approval'
        elif damage == 'observation-null':entry['process_observation'] = None
        elif damage == 'observation-version':observation['schema_version'] = 2
        elif damage == 'domain-extra':observation['domain']['authority'] = True
        elif damage == 'domain-boot':observation['domain']['boot'] = []
        elif damage == 'owner-bool':observation['owner']['pid'] = True
        elif damage == 'child-shape':observation['children'] = [{'pid': 1}]
        else:
            entry['process_observation'] = {'schema_version': 1, 'mode': 'harness',
                'host_instance': 'a' * 48, 'owner_ref': 'b' * 48,
                **{k: entry[k] for k in ('run', 'id', 'reservation')}}
            entry['process_observation']['run' if damage == 'harness-binding' else 'owner_ref'] = 'foreign'
        # Reach the observation profile rather than failing its typed companion digest.
        state['agent_contracts']['verification-result:' + entry['id']]['payload']['record_digest'] = C.fingerprint(entry)
        path.write_text(json.dumps(state))
        result = inventory(workflow_case)
        assert not result['stable']
        assert result['roots'][0]['records'][0]['classification'] == 'unknown'
    finally:
        path.write_bytes(raw)


@pytest.mark.parametrize('change_on_reread', [False, True])
def test_companion_cache_overflow_rechecks_real_source(workflow_case, monkeypatch, change_on_reread):
    from al.installer import activity_companions as companions
    state = json.loads(workflow_case['state_path'].read_bytes())
    source = Path(state['task_package']['stored_path']);before = source.read_bytes()
    opened = [];real_open = os.open
    monkeypatch.setattr(companions, 'RAW_CACHE_BYTES', 1)

    def observe_open(path, flags, *args, **kwargs):
        if Path(path) == source:
            opened.append(path)
            if change_on_reread and len(opened) == 2:
                source.write_bytes(before + b' ')
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(companions.os, 'open', observe_open)
    try:
        result = inventory(workflow_case)
        assert len(opened) >= 2  # Overflowed bytes were genuinely reread, not retained.
        if change_on_reread:
            assert not result['stable']
            assert result['roots'][0]['records'][0]['classification'] == 'unknown'
        else:
            assert result['stable'], json.dumps(result, indent=2)
            assert result['roots'][0]['records'][0]['classification'] == 'current-session'
    finally:
        source.write_bytes(before)
