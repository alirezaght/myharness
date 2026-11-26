"""Explicit operator replacement preserves installation boundaries and retained history."""
import copy
import json
from pathlib import Path

import pytest

from al.installer import operations as O
from al.installer import operator_update as U
from al.installer.manifest import cache_release
from al.installer.operator_projection import projection
from al.installer.registry import digest, load_record, read_json, write_json
from al.installer.source import resolve_release
from al.installer.targets import package_path, resolve_target
from al.tests import update_templates
from al.tests.conftest import copy_al_source, run_alctl
from al.tests.conftest import installer_workspace as workspace_fixture
from al.tests.test_manifest import git_test
from al.tests.test_policy import session

# Injected source/bootstrap work stays fresh after restoring its enrolled prerequisite.
_PREFIX_CASES = frozenset({
    'test_public_explicit_mode_warns_and_preserves_all_retained_bytes',
    'test_installation_integrity_refusals_are_not_warnings',
    'test_unmanaged_and_foreign_registry_cannot_use_operator_mode',
    'test_operator_installation_metadata_has_real_owned_no_alias_boundary',
    'test_unsafe_history_discovery_warns_but_actual_payload_overlap_refuses',
    'test_barriers_refuse_mutable_installation_inputs',
    'test_historical_progress_between_barriers_is_allowed',
    'test_partial_failure_keeps_pending_manual_reconciliation',
    'test_completed_publication_failures_never_claim_success',
})


@pytest.fixture(scope='module')
def _prefix_cache():
    return {}


@pytest.fixture
def enrolled(tmp_path, tmp_path_factory, monkeypatch, request, _prefix_cache):
    monkeypatch.delenv('AL_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('AL_DISTRIBUTION_STATE', raising=False)
    if request.node.originalname not in _PREFIX_CASES or hasattr(request, 'param'):
        return _fresh_enrolled(tmp_path, monkeypatch, request)
    if 'prefix' not in _prefix_cache:
        container = tmp_path_factory.mktemp('operator-update-prefix')
        _prefix_cache['prefix'] = update_templates.build(
            container, lambda root: _fresh_enrolled(root, monkeypatch, request))
    return update_templates.restore(_prefix_cache['prefix'])


def _fresh_enrolled(root, monkeypatch, request):
    w = workspace_fixture.__wrapped__(root, monkeypatch)
    target = resolve_target(home=w['home'])
    O.run_operation({'kind': 'install', 'target': target, 'source': str(w['source']),
                     'version': '1.0.0', 'policy': 'follow' if getattr(request, 'param', None) == 'follow' else 'pinned',
                     'status_line': getattr(request, 'param', None) == 'status-line', 'invoking': str(w['root'])}, w['state'])
    row = load_record(w['state'], target['target_id'])
    runtime = w['root'] / 'runtime'
    runtime.mkdir()
    session(runtime / 'active.json', status='RUNNING', phase='IMPLEMENTATION')
    state = read_json(runtime / 'active.json')
    state['writers'] = {'eng': {'id': 'eng', 'state': 'ACTIVE'}}
    state['evidence']['entries'] = [{'status': 'RUNNING'}]
    write_json(runtime / 'active.json', state)
    (runtime / 'unknown.json').write_text('{"legacy":true}')
    (runtime / 'malformed.json').write_bytes(b'unknown retained bytes')
    with (runtime / 'oversize.json').open('wb') as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    for path in (w['state'] / 'al-run-pins/runs/unknown.json', w['state'] / 'snapshots/retained'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'unknown retained bytes')
    index = w['state'] / 'al-run-pins/runs/unknown.json'
    index.parent.chmod(0o700)
    write_json(index, {'schema_version': 1, 'status': 'RESERVED'})
    w['bootstrap_source'] = copy_al_source(w['root'] / 'operator-bootstrap')
    w['launcher'] = w['root'] / 'bin/alctl'
    boot = run_alctl('bootstrap', '--source-root', w['bootstrap_source'], '--destination', w['launcher'],
                     '--state-root', w['state'], cwd=w['root'])
    assert boot.returncode == 0, boot.stdout + boot.stderr
    return w, target, row, runtime


def tag(enrolled, mutate=None):
    w, target, row, runtime = enrolled
    root = w['source'] / 'al'
    path = root / 'codex/agents/al-architect.toml'
    path.write_text(path.read_text().replace('# AL-Architect\n', '# AL-Architect\n\nReviewed instruction body correction.\n', 1))
    path = root / 'claude/agents/al-architect.md'
    path.write_text(path.read_text() + '\nReviewed instruction body correction.\n')
    (root / 'codex/skills/al/scripts/reviewed_addition.py').write_text('"""Reviewed inert package addition."""\n')
    if mutate:
        mutate(root)
    git_test(w['source'], 'add', '.')
    git_test(w['source'], 'commit', '-qm', 'reviewed update fixture')
    git_test(w['source'], 'tag', 'v1.1.0')
    return cache_release(resolve_release(str(w['source']), version='1.1.0'), w['state'])


def artifact(enrolled, cache, manifest):
    w, target, row, runtime = enrolled
    prepared = O._prepare(target, cache, manifest, dict(row, _state_root=str(w['state'])), row['policy'], {}, {}, status_line=bool(row['current'].get('status_line')))
    configurations = {p: {'desired': prepared['plans'][p]['desired_config'],
                           'state': prepared['preimages'][config['path']], 'owned': config['owned'],
                           'raw': config['raw'].decode('utf-8') if config['raw'] is not None else None}
                      for p, config in prepared['configs'].items()}
    old = row['current']['release']
    contents = [{name: (Path(c) / 'payload' / name).read_bytes() for name in m['files']}
                for c, m in ((row['current']['cache'], old), (cache, manifest))]
    projected = projection(old, manifest, *contents, configurations)
    changed, unchanged = U.delta(old, manifest)
    def ref(name, value):
        path = w['root'] / name
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return {'path': str(path), 'sha256': digest(path.read_bytes())}
    semantic = {'schema_version': 1, 'kind': 'operator-global-package-source-review-v1', 'reviewed': True,
                'old_release': U.identity(old), 'new_release': U.identity(manifest), 'resources': changed,
                'projection': projected, 'reviewer': 'synthetic independent fixture reviewer',
                'reason': 'Synthetic fixture review covers exactly the selected code and instruction hashes'}
    review = {'schema_version': 1, 'kind': 'operator-global-package-update-v1', 'operation': 'global-update-allow-running',
              'target': target, 'source': row['source'], 'old_release': U.identity(old), 'new_release': U.identity(manifest),
              'manifests': {label: {'path': str(Path(c) / 'manifest.json'), 'sha256': digest((Path(c) / 'manifest.json').read_bytes())}
                            for label, c in (('old', row['current']['cache']), ('new', cache))},
              'resources': changed, 'unchanged_resource_set_sha256': unchanged, 'projection': projected,
              'accepted_limits': U.LIMITS, 'reason': 'Test the explicit disruptive operator package update',
              'provenance': 'Disposable synthetic operator fixture, not a real user approval',
              'operator_authorization': ref('operator.txt', 'Synthetic operator authorization for isolated fixture only'),
              'instruction_review': ref('instructions.txt', 'Synthetic applicable instruction review fixture'),
              'source_review': ref('source-review.json', semantic)}
    reviews = {'global_package_update': review}
    path = w['root'] / 'reviews.json'
    path.write_text(json.dumps(reviews))
    return {'kind': 'update', 'target': target, 'version': '1.1.0', 'allow_running': True,
            'reviews': reviews, 'operator_review_artifact': {'path': str(path), 'sha256': digest(path.read_bytes())},
            'runtime_roots': [str(runtime)], 'invoking': str(w['root'])}


def public(enrolled, req, *args):
    w, target, row, runtime = enrolled
    import os
    import subprocess
    extra_roots = [part for root in req.get('runtime_roots', [])[1:] for part in ('--runtime-root', root)]
    result = subprocess.run([str(w['launcher']), 'update', '--global', '--home', str(w['home']),
                             '--state-root', str(w['state']), '--version', '1.1.0',
                             '--reviews', req['operator_review_artifact']['path'], '--runtime-root', str(runtime), *extra_roots, *args],
                            cwd=w['root'], env=dict(os.environ, HOME=str(w['home'])),
                            text=True, capture_output=True, timeout=120)
    result.data = json.loads(result.stdout)
    return result


def retained(enrolled):
    w, target, row, runtime = enrolled
    roots = [runtime, w['state'] / 'al-run-pins', w['state'] / 'snapshots']
    return {str(p): (p.read_bytes(), p.stat().st_mode) for root in roots for p in root.rglob('*') if p.is_file()}


def inject(enrolled, statements, needle="            D.apply(engine_manifest, backup)\n", *, after=True):
    w, target, row, runtime = enrolled
    path = w['bootstrap_source'] / 'installer/operations.py'
    raw = path.read_text()
    assert needle in raw
    addition = "\n".join(statements) + "\n"
    raw = raw.replace(needle, needle + addition if after else addition + needle, 1)
    path.write_text(raw)
    boot = run_alctl('bootstrap', '--source-root', w['bootstrap_source'], '--destination', w['launcher'],
                     '--state-root', w['state'], cwd=w['root'])
    assert boot.returncode == 0, boot.stdout + boot.stderr


@pytest.fixture
def projection_data():
    root = Path(__file__).resolve().parents[1]
    names = ['codex/agents/al-architect.toml', 'claude/agents/al-architect.md',
             'codex/skills/al/SKILL.md', 'claude/skills/al/SKILL.md',
             'codex/skills/al/scripts/al_models.py', 'claude/skills/al/scripts/al_models.py',
             'claude/skills/al/scripts/al_readonly_guard.py', 'claude/skills/al/scripts/al_shell.py']
    contents = {name: (root / name).read_bytes() for name in names}
    manifest = {'files': {name: {'sha256': digest(raw), 'mode': 0o644, 'platform': name.split('/')[0],
                                 'kind': 'role' if '/agents/' in name else 'package'}
                          for name, raw in contents.items()}}
    return manifest, contents
