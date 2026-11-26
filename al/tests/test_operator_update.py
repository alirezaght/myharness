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

# Source-injected faults, construction subjects and unknown cases remain fresh.
_PREFIX_CASES = frozenset({
    'test_public_explicit_mode_warns_and_preserves_all_retained_bytes',
    'test_installation_integrity_refusals_are_not_warnings',
    'test_unmanaged_and_foreign_registry_cannot_use_operator_mode',
    'test_operator_installation_metadata_has_real_owned_no_alias_boundary',
    'test_unsafe_history_discovery_warns_but_actual_payload_overlap_refuses',
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


def test_public_explicit_mode_warns_and_preserves_all_retained_bytes(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    refused = public(enrolled, req)
    assert refused.returncode == 3 and refused.data['status'] == 'deferred'
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.data['activity']['settlement'] == 'NOT_ASSESSED'
    for key in ('active-or-unsettled-writers', 'active-or-unsettled-evidence', 'unsupported', 'oversize', 'unreadable-or-malformed'):
        assert result.data['activity']['counts'][key] > 0
    assert retained(enrolled) == before
    current = load_record(w['state'], target['target_id'])
    assert current['previous'] == row['current'] and current['policy'] == row['policy']
    O.check_current(current, w['state'])
    receipt = read_json(current['current']['journal'])
    assert [r['barrier'] for r in receipt['operator_update_barriers']] == [
        'pre-apply', 'before-platform:codex', 'before-platform:claude', 'before-completed-publication']
    assert not receipt['ancillary_actions']
    assert receipt['operator_update']['kind'] == 'operator-global-package-update-receipt-v1'


@pytest.mark.parametrize('overrides', [
    {'kind': 'install'}, {'kind': 'rollback'}, {'version': None}, {'version': 'latest'},
    {'policy': 'pinned'}, {'source': 'same-explicit-source'}, {'source_subdir': 'al'},
    {'all': True}, {'adoption': {'reviewed': True}}, {'status_line': True},
    {'documentation_only': True}, {'reviewed_runtime_update': True}, {'operator_review_artifact': None},
])
def test_scope_refuses_before_activity_or_mutation(overrides):
    target = {'scope': 'global'}
    row = {'target': target, 'current': {'release': {}}}
    req = {'kind': 'update', 'target': target, 'version': '1.1.0', 'allow_running': True,
           'operator_review_artifact': {'path': '/unused/reviews.json', 'sha256': '0' * 64}}
    before = copy.deepcopy((req, row))
    U.validate_request(req, row)
    with pytest.raises(ValueError):
        U.validate_request(dict(req, **overrides), row)
    assert (req, row) == before


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


def test_historical_progress_between_barriers_is_allowed(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    path = str(runtime / 'active.json')
    inject(enrolled, [f"            write_json({path!r}, dict(read_json({path!r}), handoffs=read_json({path!r})['handoffs'] + 1))"])
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(runtime / 'active.json')['handoffs'] == 2


def test_partial_failure_keeps_pending_manual_reconciliation(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    inject(enrolled, ["            if platform == 'claude': raise OSError('injected second-platform failure')"], after=False)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1 and 'injected second-platform' in result.data['error']
    current = load_record(w['state'], target['target_id'])
    assert current['pending'] and current['current'] == row['current']
    journal = read_json(w['state'] / 'installer/operations' / current['pending'] / 'journal.json')
    assert journal['status'] == 'FAILED' and 'manual reconciliation' in journal['recovery']
    assert journal['engine_steps'][0]['status'] == 'COMPLETE'
    assert retained(enrolled) == before
    refused = public(enrolled, req, '--allow-running')
    assert refused.returncode == 1 and 'unresolved' in refused.data['error']


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


def test_projection_allows_only_reviewable_body_changes(projection_data):
    old, contents = projection_data
    new, after = copy.deepcopy(old), dict(contents)
    for name in ('codex/agents/al-architect.toml', 'claude/agents/al-architect.md', 'codex/skills/al/SKILL.md'):
        after[name] = contents[name].replace(b'# AL', b'# Reviewed AL', 1)
        new['files'][name]['sha256'] = digest(after[name])
    assert projection(old, new, contents, after, {})['version'] == 2


@pytest.mark.parametrize('kind', ['model', 'effort', 'sandbox', 'extra-key', 'structured-key', 'missing-body',
                                  'claude-model', 'claude-tools', 'claude-unknown', 'skill-codex', 'skill-claude',
                                  'hook', 'hook-dependency', 'model-code', 'role-add', 'role-remove', 'role-mode'])
def test_authority_mutations_cannot_be_reviewed_in(projection_data, kind):
    old, contents = projection_data
    new, after = copy.deepcopy(old), dict(contents)
    name = 'codex/agents/al-architect.toml'
    raw = after[name]
    if kind == 'model': raw = raw.replace(b'gpt-6-astra', b'other-model', 1)
    elif kind == 'effort': raw = raw.replace(b'"medium"', b'"high"', 1)
    elif kind == 'sandbox': raw = raw.replace(b'"read-only"', b'"workspace-write"', 1)
    elif kind == 'extra-key': raw = b'future_permission = true\n' + raw
    elif kind == 'structured-key': raw = b'future_permission = { write = true, depth = 99 }\n' + raw
    elif kind == 'missing-body': raw = raw.replace(b'developer_instructions', b'other_instructions', 1)
    elif kind.startswith('claude-'):
        name = 'claude/agents/al-architect.md'; raw = after[name]
        if kind == 'claude-model': raw = raw.replace(b'model: fable', b'model: opus', 1)
        elif kind == 'claude-tools': raw = raw.replace(b'tools: Read', b'tools: Write, Read', 1)
        else: raw = raw.replace(b'---\n', b'---\nfuture-permission: true\n', 1)
    elif kind.startswith('skill-'):
        name = kind.split('-')[1] + '/skills/al/SKILL.md'
        raw = after[name].replace(b'---\n', b'---\nfuture-permission: true\n', 1)
    elif kind in {'hook', 'hook-dependency', 'model-code'}:
        name = {'hook': 'claude/skills/al/scripts/al_readonly_guard.py',
                'hook-dependency': 'claude/skills/al/scripts/al_shell.py',
                'model-code': 'codex/skills/al/scripts/al_models.py'}[kind]
        raw = after[name] + b'\n'
    elif kind == 'role-add':
        new['files']['codex/agents/al-extra.toml'] = dict(new['files'][name])
        after['codex/agents/al-extra.toml'] = raw
    elif kind == 'role-remove':
        new['files'].pop(name); after.pop(name)
    elif kind == 'role-mode': new['files'][name]['mode'] = 0o755
    if name in after:
        after[name] = raw; new['files'][name]['sha256'] = digest(raw)
    with pytest.raises(ValueError):
        projection(old, new, contents, after, {})


@pytest.mark.parametrize('failure', ['review-hash', 'review-fields', 'provenance', 'source-review', 'delta', 'projection', 'projection-version',
                                    'drift', 'collision', 'cache', 'tag', 'registry', 'pending', 'hardlink', 'symlink', 'overlap',
                                    'manifest-hardlink', 'cache-hardlink'])
def test_installation_integrity_refusals_are_not_warnings(enrolled, failure):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    review = req['reviews']['global_package_update']
    review_path = Path(req['operator_review_artifact']['path'])
    if failure == 'review-hash': review_path.write_text('{}')
    elif failure == 'review-fields': review['invented'] = True
    elif failure == 'provenance': Path(review['operator_authorization']['path']).write_text('changed')
    elif failure == 'source-review':
        ref = review['source_review']; value = read_json(ref['path']); value['reviewed'] = False
        write_json(ref['path'], value); ref['sha256'] = digest(Path(ref['path']).read_bytes())
    elif failure == 'delta': review['resources'] = {}
    elif failure == 'projection': review['projection']['authority_sha256'] = '0' * 64
    elif failure == 'projection-version': review['projection']['version'] = 1
    elif failure == 'drift': (package_path(target, 'codex') / 'SKILL.md').write_text('changed')
    elif failure == 'collision': (package_path(target, 'codex') / 'scripts/reviewed_addition.py').write_text('unmanaged')
    elif failure == 'cache': (Path(row['current']['cache']) / 'payload/codex/skills/al/SKILL.md').write_text('changed')
    elif failure == 'tag': git_test(w['source'], 'tag', '-f', 'v1.0.0')
    elif failure == 'registry':
        record = Path(row['current']['packages']['codex']['path']); value = read_json(record)
        value['generation'] += 1; write_json(record, value)
    elif failure == 'pending':
        value = dict(row, pending='unsettled-installer-operation')
        write_json(O.record_path(w['state'], target['target_id']), value)
    elif failure in {'hardlink', 'symlink'}:
        path = package_path(target, 'codex') / 'SKILL.md'; saved = w['root'] / 'aliased-resource'
        path.rename(saved)
        if failure == 'hardlink': path.hardlink_to(saved)
        else: path.symlink_to(saved)
    elif failure == 'overlap': req['runtime_roots'].append(str(package_path(target, 'codex') / 'reference'))
    elif failure in {'manifest-hardlink', 'cache-hardlink'}:
        suffix = 'manifest.json' if failure == 'manifest-hardlink' else 'payload/codex/skills/al/SKILL.md'
        path = Path(row['current']['cache']) / suffix
        (w['root'] / 'cache-alias').hardlink_to(path)
    if failure in {'review-fields', 'source-review', 'delta', 'projection', 'projection-version'}:
        review_path.write_text(json.dumps(req['reviews']))
        req['operator_review_artifact']['sha256'] = digest(review_path.read_bytes())
    before = retained(enrolled)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'genuine isolated' not in result.data['error']
    assert retained(enrolled) == before
    assert load_record(w['state'], target['target_id'])['current'] == row['current']


@pytest.mark.parametrize('mutation', ['review', 'source', 'plan', 'config', 'role', 'postimage', 'ownership'])
def test_barriers_refuse_mutable_installation_inputs(enrolled, mutation):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    if mutation == 'review': statement = f"Path({req['operator_review_artifact']['path']!r}).write_text('{{}}')"
    elif mutation == 'source': statement = f"__import__('subprocess').run(['git','-C',{str(w['source'])!r},'tag','-f','v1.1.0','v1.0.0'],check=True,capture_output=True)"
    elif mutation == 'config': statement = f"Path({str(w['home'] / '.codex/config.toml')!r}).write_text('changed = true\\n')"
    elif mutation == 'role': statement = f"Path({str(w['home'] / '.codex/agents/al-architect.toml')!r}).write_text('changed = true\\n')"
    elif mutation == 'postimage': statement = f"Path({str(package_path(target, 'codex') / 'SKILL.md')!r}).write_text('changed')"
    elif mutation == 'ownership':
        path = row['current']['packages']['codex']['path']
        statement = f"write_json({path!r}, dict(read_json({path!r}), generation=999))"
    else: statement = "prepared['plans']['codex']['files'].clear()"
    if mutation == 'plan': inject(enrolled, ['        ' + statement], '        _recheck(prepared)\n', after=False)
    else: inject(enrolled, ["            if platform == 'codex': " + statement])
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    current = load_record(w['state'], target['target_id'])
    assert current['pending'] and current['current'] == row['current']
    assert read_json(w['state'] / 'installer/operations' / current['pending'] / 'journal.json')['status'] == 'FAILED'


def test_observation_limits_are_explicit_and_do_not_print_state_prose(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    for index in range(5):
        write_json(runtime / (str(index) + '.json'), {'schema_version': 9, 'spec': 'PRIVATE PROSE'})
    monkeypatch.setattr(U, 'SCAN_LIMIT', 2)
    report = U.observe(tmp_path / 'state', [], resolve_target(home=tmp_path), [str(runtime)], str(tmp_path))
    assert report['counts'] == {'unsupported': 2}
    assert 'metadata-enumeration-truncated' in report['gaps']
    assert 'PRIVATE PROSE' not in json.dumps(report)


def test_managed_old_pin_stays_old_and_separate_bootstrap_selects_new(enrolled):
    import os
    import shutil
    import sys

    from al.tests.conftest import copy_al_source
    from al.tests.runtime_run_helpers import invoke as invoke_raw

    def invoke(*args):
        result = invoke_raw(*args)
        try:
            result.data = json.loads(result.stdout)
        except json.JSONDecodeError:
            result.data = None
        return result

    w, target, row, runtime = enrolled
    (w['state'] / 'al-run-pins').chmod(0o700)
    repo = w['root'] / 'repository'; repo.mkdir()
    git_test(repo, 'init', '-q'); git_test(repo, 'commit', '--allow-empty', '-qm', 'fixture repository')
    launcher = w['home'] / 'bin/alctl'
    env = dict(os.environ, HOME=str(w['home']), PYTHONDONTWRITEBYTECODE='1')
    for name in ('AL_RUNTIME_DIR', 'AL_DISTRIBUTION_STATE', 'PYTHONPATH', 'PYTHONHOME',
                 'CLAUDE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_PROJECT_DIR'):
        env.pop(name, None)
    def boot(source):
        result = invoke([sys.executable, '-I', '-S', '-B', source / 'alctl.py', 'bootstrap',
                         '--source-root', source, '--destination', launcher, '--state-root', w['state']], repo, env)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.data
    def run(session, *args):
        return invoke([launcher, 'run', '--repository', repo, '--platform', 'codex', '--session', session, '--', *args], repo, env)
    old_source = copy_al_source(w['root'] / 'old-bootstrap')
    old_boot = boot(old_source)
    started = run('old-session', 'start', '--spec', 'Retain the old protected runtime.')
    assert started.returncode == 0, started.stdout + started.stderr
    cache, manifest = tag(enrolled)
    req = artifact(enrolled, cache, manifest)
    old_state = repo / '.codex/al-runtime/old-session.json'
    before = retained(enrolled)
    before[str(old_state)] = (old_state.read_bytes(), old_state.stat().st_mode)
    snapshot = {str(p): p.read_bytes() for p in Path(old_boot['snapshot']).rglob('*') if p.is_file()}
    result = invoke([launcher, 'update', '--global', '--home', w['home'], '--state-root', w['state'],
                     '--version', '1.1.0', '--reviews', req['operator_review_artifact']['path'],
                     '--runtime-root', runtime, '--allow-running'], repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(Path(p).read_bytes() == raw and Path(p).stat().st_mode == mode for p, (raw, mode) in before.items())
    assert all(Path(p).read_bytes() == raw for p, raw in snapshot.items())
    shown = run('old-session', 'show', '--json')
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert shown.data['run_id'] == read_json(old_state)['run_id']
    new_source = copy_al_source(w['root'] / 'new-bootstrap')
    for platform in ('codex', 'claude'):
        shutil.rmtree(new_source / platform)
    for name, item in manifest['files'].items():
        path = new_source / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((cache / 'payload' / name).read_bytes()); path.chmod(item['mode'])
    boot(new_source)  # Separate supported coordinator publication, never implicit in update.
    fresh = run('new-session', 'start', '--spec', 'Select the newly bootstrapped runtime.')
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    bindings = [read_json(p) for p in (w['state'] / 'al-run-pins/runs').glob('*.json')]
    old, new = [next(b for b in bindings if b.get('session_id') == sid) for sid in ('old-session', 'new-session')]
    name = 'payload/codex/skills/al/scripts/reviewed_addition.py'
    assert not (w['state'] / 'al-run-pins/pins' / old['pin']['pin_identity'] / name).exists()
    added = w['state'] / 'al-run-pins/pins' / new['pin']['pin_identity'] / name
    assert digest(added.read_bytes()) == manifest['files'][name.removeprefix('payload/')]['sha256']
    assert old['pin']['pin_identity'] != new['pin']['pin_identity']
    assert all(Path(p).read_bytes() == raw and Path(p).stat().st_mode == mode for p, (raw, mode) in before.items())


@pytest.mark.parametrize('enrolled', ['status-line', 'follow'], indirect=True)
def test_existing_configuration_and_policy_are_preserved(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = {name: Path(name).read_bytes() for name in row['current']['roles']}
    configs = {p: (w['home'] / ('.' + p) / ('config.toml' if p == 'codex' else 'settings.json'))
               for p in target['platforms']}
    raw = {p: path.read_bytes() if path.exists() else None for p, path in configs.items()}
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 0, result.stdout + result.stderr
    current = load_record(w['state'], target['target_id'])
    assert current['policy'] == row['policy']
    assert current['current']['status_line'] == row['current']['status_line']
    assert {p: path.read_bytes() if path.exists() else None for p, path in configs.items()} == raw
    assert any(Path(name).read_bytes() != data for name, data in before.items())  # reviewed bodies changed


@pytest.mark.parametrize('failure', ['late-postimage', 'smoke', 'publication'])
def test_completed_publication_failures_never_claim_success(enrolled, failure):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    if failure == 'late-postimage':
        inject(enrolled, [f"            if platform == 'claude': Path({str(package_path(target, 'codex') / 'SKILL.md')!r}).write_text('changed before publication')"])
    elif failure == 'smoke':
        inject(enrolled, ["        raise ValueError('injected smoke failure')"], "        journal['verification'] = {'smoke': smoke_installation(target, candidate['roles']), 'activity': activity}\n", after=False)
    else:
        inject(enrolled, ["        raise ValueError('injected publication failure')"], '        J.publish_completed(pending, candidate, journal, path, state_root)\n', after=False)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    current = load_record(w['state'], target['target_id'])
    assert current['pending'] and current['current'] == row['current']
    assert retained(enrolled) == before
    journal = read_json(w['state'] / 'installer/operations' / current['pending'] / 'journal.json')
    assert journal['status'] == 'FAILED' and 'manual reconciliation' in journal['recovery']


def test_public_flag_is_update_only_and_single_global():
    from al.installer.cli import parser
    for action in ('install', 'rollback', 'doctor'):
        with pytest.raises(SystemExit):
            parser().parse_args([action, '--allow-running'])
    value = parser().parse_args(['update', '--global', '--version', '1.1.0', '--allow-running', '--reviews', '/absolute/reviews.json'])
    assert value.allow_running and value.version == '1.1.0' and value.global_scope


def test_deeply_malformed_history_is_a_bounded_warning(tmp_path):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    (runtime / 'deep.json').write_text('[' * 100000 + ']' * 100000)
    report = U.observe(tmp_path / 'state', [], resolve_target(home=tmp_path), [str(runtime)], str(tmp_path))
    assert report['counts']['unreadable-or-malformed'] == 1


def test_unmanaged_and_foreign_registry_cannot_use_operator_mode(enrolled):
    import shutil
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    unmanaged = run_alctl('update', '--global', '--home', w['home'], '--state-root', w['state'],
                           '--version', '1.1.0', '--reviews', req['operator_review_artifact']['path'],
                           '--allow-running', cwd=w['root'])
    assert unmanaged.returncode == 1 and 'genuine isolated managed' in unmanaged.data['error']
    other = w['root'] / 'foreign-registry'
    shutil.copytree(w['state'], other)
    foreign = public(enrolled, req, '--allow-running', '--state-root', str(other))
    assert foreign.returncode == 1 and 'registry differs' in foreign.data['error']
    assert load_record(w['state'], target['target_id']) == row
    assert retained(enrolled) == before


@pytest.mark.parametrize('name', ['record', 'source', 'ownership', 'sentinel', 'lock'])
@pytest.mark.parametrize('damage', ['hardlink', 'symlink', 'writable'])
def test_operator_installation_metadata_has_real_owned_no_alias_boundary(enrolled, name, damage):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    path = {'record': O.record_path(w['state'], target['target_id']),
            'source': next((w['state'] / 'installer/sources').glob('*.json')),
            'ownership': Path(row['current']['packages']['codex']['path']),
            'sentinel': Path(next(iter(row['current']['roles'].values()))['sentinel']['path']),
            'lock': w['state'] / 'installer/coordination.lock'}[name]
    if damage == 'hardlink': (w['root'] / 'metadata-alias').hardlink_to(path)
    elif damage == 'symlink':
        saved = w['root'] / 'metadata-saved'; path.rename(saved); path.symlink_to(saved)
    else: path.chmod(0o666)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    assert any(word in result.data['error'] for word in ('metadata', 'symlink', 'ownership', 'alias'))
    assert not row['pending']
    # Reading unsafe metadata through normal APIs may itself refuse; resource payload stays old.
    assert (package_path(target, 'codex') / 'SKILL.md').read_bytes() == (
        Path(row['current']['cache']) / 'payload/codex/skills/al/SKILL.md').read_bytes()


def test_coordination_descriptor_and_legacy_held_callers_remain_compatible(tmp_path):
    from al.installer.registry import Held, coordination, namespace
    old = Held(namespace(tmp_path))
    assert old.fd is None
    with coordination(tmp_path) as held:
        assert type(held.fd) is int
        with coordination(tmp_path, held=held) as nested:
            assert nested is held
    assert held.active is False


def test_owned_metadata_rejects_a_foreign_owner_comparison(tmp_path, monkeypatch):
    from al.installer import operator_registry as G
    path = tmp_path / 'metadata'; path.write_text('fixture')
    uid = path.stat().st_uid
    monkeypatch.setattr(G.os, 'getuid', lambda: uid + 1)
    with pytest.raises(ValueError, match='ownership'):
        G.owned(path)


@pytest.mark.parametrize('point', ['before-lock', 'under-lock-replace', 'under-lock-alias'])
def test_real_lock_replacement_and_alias_races_refuse(enrolled, point):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    lock = str(w['state'] / 'installer/coordination.lock')
    if point == 'before-lock':
        inject(enrolled, [f"    if operator_registry is not None: Path({lock!r}).rename({lock + '.old'!r}); D.atomic(Path({lock!r}), b'', 0o644)"],
               '    with coordination(state_root) as held:\n', after=False)
    else:
        statement = (f"Path({lock!r}).rename({lock + '.old'!r}); D.atomic(Path({lock!r}), b'', 0o644)"
                     if point == 'under-lock-replace' else f"Path({lock + '.alias'!r}).hardlink_to(Path({lock!r}))")
        inject(enrolled, ["            if platform == 'codex': " + statement])
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1 and ('lock' in result.data['error'] or 'alias' in result.data['error'])
    current = load_record(w['state'], target['target_id'])
    assert current['current'] == row['current']
    assert bool(current['pending']) == (point != 'before-lock')


@pytest.mark.parametrize('overlap', [False, True])
def test_unsafe_history_discovery_warns_but_actual_payload_overlap_refuses(enrolled, overlap):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    link = w['root'] / 'history-link'
    link.symlink_to(package_path(target, 'codex') / 'reference' if overlap else runtime)
    req['runtime_roots'].append(str(link))
    result = public(enrolled, req, '--allow-running')
    if overlap:
        assert result.returncode == 1 and 'overlaps protected' in result.data['error']
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'runtime-root-unreadable-or-unsafe' in result.data['activity']['gaps']
