"""Real repositories and structurally explicit history; no deployment approval fixture."""
import copy
import json
from pathlib import Path
import subprocess

from al.installer import activity_history as H, activity_retirement as A
from al.installer.activity_inventory import inventory_activity, repository_identity
from al.installer.registry import canonical, digest

SESSION = 'al-20261019T112752Z-d843eb'


def git_repo(path):
    path.mkdir()
    for args in (('init', '-q'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'initial')):
        subprocess.run(['git', *args], cwd=path, check=True, capture_output=True)
    return repository_identity(path)


def legacy(session='historical'):
    return {'workflow': 'AL', 'session_id': session, 'status': 'RUNNING', 'phase': 'PLANNING',
            'task': 'Historical work remains unfinished and must not be marked approved.', 'started_at': 100.0,
            'updated_at': 101.0, 'review_iteration': 0, 'correction_iteration': 0,
            'max_correction_iterations': 6, 'completed_phases': [], 'pending_phases': ['PLANNING'],
            'phase_timings': {}, 'findings': {}, 'failure': None}


def modern(repo, runtime, session=SESSION):
    value = legacy(session)
    artifact = runtime / session / 'artifacts/task'
    artifact.parent.mkdir(parents=True)
    raw = canonical({'accepted_spec': 'Verify activity only; this fixture is not installation approval.'})
    artifact.write_bytes(raw)
    value.update(schema_version=2, run_id='actual-test-run', identity=repo,
                 task_package={'stored_path': str(artifact), 'sha256': digest(raw)}, amendments=[], writers={},
                 phase_occurrences=[], review_history=[], evidence={'seq': 0, 'entries': [], 'plan': {}},
                 status='APPROVED', phase='DONE')
    return value


def write_state(runtime, state):
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / (state['session_id'] + '.json')
    path.write_bytes(canonical(state))
    return path


def scenario(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / 'myharness')
    other = git_repo(tmp_path / 'other')
    runtime = Path(repo['root']) / '.codex/al-runtime';runtime.mkdir(parents=True)
    old_runtime = Path(other['root']) / '.claude/al-runtime';old_runtime.mkdir(parents=True)
    current_path = write_state(runtime, modern(repo, runtime))
    (runtime / ('.' + SESSION + '.lock')).write_bytes(b'')
    (runtime / 'current').write_text(SESSION)
    old_path = write_state(old_runtime, legacy())
    invoking = {'repository': repo, 'platform': 'codex', 'runtime_root': str(runtime),
                'state_path': str(current_path), 'session_id': SESSION, 'run_id': 'actual-test-run'}
    roots = [{'repository': repo, 'platform': 'codex', 'runtime_root': str(runtime)},
             {'repository': other, 'platform': 'claude', 'runtime_root': str(old_runtime)}]
    inventory = inventory_activity(roots, invoking_run=invoking)
    # The external factual-history boundary is fixture-bound, never a product override.
    members = [{'path': str(p), 'sha256': digest(p.read_bytes()), 'mode': p.stat().st_mode & 0o777,
                'session_id': s, 'run_id': run, 'is_current_run': current}
               for p, s, run, current in [(current_path, SESSION, 'actual-test-run', True), (old_path, 'historical', None, False)]]
    preparations = {'scope': {'rows': [{'path': m['path'], 'repo': str(Path(m['path']).parent.parent.parent),
                                     'platform': Path(m['path']).parent.parent.name[1:]} for m in members]},
                    'states': {'members': members}, 'nonstate': {'rows': []}}
    references = {}
    for kind, value in preparations.items():
        path = tmp_path / (kind + '.json');path.write_bytes(canonical(value))
        references[kind] = {'path': str(path), 'sha256': digest(path.read_bytes())}
    monkeypatch.setattr(H, 'ANCHORS', {key: value['sha256'] for key, value in references.items()})
    facts = Path(__file__).with_name('fixtures') / 'workspace-activity-facts.json'
    request = {'source_artifact': {'path': str(facts), 'sha256': A.FACTS_SOURCE_SHA},
               'source_binding': {'content_digest': 'a' * 64}, 'deployment_intent_digest': 'b' * 64,
               'myharness_identity': repo, 'directory': str(tmp_path / 'acknowledgements'), 'history': references}
    return {'inventory': inventory, 'roots': roots, 'invoking': invoking, 'request': request,
            'runtime': runtime, 'old_runtime': old_runtime, 'old_path': old_path, 'current_path': current_path}


def planned(case, tmp_path):
    acknowledgements = A.plan_acknowledgements(case['inventory'], case['request'], excluded_run=case['invoking'])
    inventory_ref = A.publish_once(tmp_path / 'inventory.json', case['inventory'])
    plan = {'source': case['request']['source_binding'],
            'activity': {'inventory': inventory_ref, 'reviewed_history': acknowledgements['reviewed_history'], 'acknowledgements': acknowledgements['artifacts'],
                         'deployment_intent_digest': case['request']['deployment_intent_digest']}}
    return plan
