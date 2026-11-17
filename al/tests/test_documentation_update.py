"""Bounded documentation updates retain activity and immutable runtime bytes."""
import json

import pytest

from al.installer import operations as O
from al.installer.guards import ActiveRuns, scan_activity
from al.installer.manifest import build_manifest
from al.installer.registry import digest, load_record, read_json, write_json
from al.installer.source import resolve_release
from al.installer.targets import package_path, resolve_target
from al.tests.test_manifest import git_test
from al.tests.test_policy import session

DOC = 'claude/skills/al/SKILL.md'


def tagged(workspace, changes=None):
    source = workspace['source']
    path = source / 'al' / DOC
    path.write_text(path.read_text() + '\nReviewed documentation correction.\n')
    if changes:
        changes(source / 'al')
    git_test(source, 'add', '.')
    git_test(source, 'commit', '-qm', 'documentation correction')
    git_test(source, 'tag', 'v1.0.1')
    return build_manifest(resolve_release(str(source), version='1.0.1'))


def review(workspace, old, new):
    proof = workspace['root'] / 'reviewed-instructions.txt'
    proof.write_text('Inspected exact instruction correction; operator authorized global update.')
    provenance = {'path': str(proof), 'sha256': digest(proof.read_bytes())}
    return {'documentation_update': {
        'schema_version': 1, 'reviewed': True, 'reason': 'Exact correction restores existing contract',
        'old_release_digest': old['digest'], 'new_release_digest': new['digest'],
        'resources': {name: {'before': old['files'][name]['sha256'], 'after': item['sha256']}
                      for name, item in new['files'].items()
                      if name in old['files'] and old['files'][name] != item},
        'instruction_review': provenance, 'user_authorization': provenance}}


@pytest.fixture
def enrolled(installer_workspace, monkeypatch, request):
    w = installer_workspace
    target = resolve_target(home=w['home'])
    O.run_operation({'kind': 'install', 'target': target, 'source': str(w['source']),
                     'version': '1.0.0', 'policy': getattr(request, 'param', 'pinned'),
                     'invoking': str(w['root'])}, w['state'])
    runtime = w['root'] / 'runtime'
    runtime.mkdir()
    session(runtime / 'retained.json', status='RUNNING', phase='PLANNING')
    session(runtime / 'stopped.json', status='STOPPED_WITH_BLOCKERS', phase='REVIEW')
    # A protected pin directory is never modified by an installer update.
    pin = w['state'] / 'al-run-pins' / 'fixture-pin'
    pin.parent.mkdir(exist_ok=True)
    pin.write_bytes(b'immutable existing pin')
    row = load_record(w['state'], target['target_id'])
    return w, target, row, runtime, pin


def request(enrolled, new):
    w, target, row, runtime, pin = enrolled
    return {'kind': 'update', 'target': target, 'version': '1.0.1',
            'documentation_only': True, 'reviews': review(w, row['current']['release'], new),
            'runtime_roots': [str(runtime)], 'invoking': str(w['root'])}


@pytest.mark.parametrize('enrolled', ['pinned', 'follow'], indirect=True)
def test_explicit_mode_updates_and_retains_truthful_activity(enrolled):
    w, target, old, runtime, pin = enrolled
    new = tagged(w)
    req = request(enrolled, new)
    before = {p: p.read_bytes() for p in (runtime / 'retained.json', runtime / 'stopped.json', pin)}
    with pytest.raises(ActiveRuns):
        O.run_operation(dict(req, documentation_only=False), w['state'])
    result = O.run_operation(req, w['state'])
    assert result['version'] == '1.0.1'
    assert result['activity']['blockers'][0]['category'] == 'terminal-workflow'
    assert {p: p.read_bytes() for p in before} == before
    row = load_record(w['state'], target['target_id'])
    assert row['policy'] == old['policy']
    assert row['previous'] == old['current']
    O.check_current(row, w['state'])
    assert O.check_snapshot(old['current'], w['state']) == old['current']['release']
    # Ordinary default numeric release selection now chooses the installed release.
    assert resolve_release(str(w['source']))['version'] == row['current']['release']['version']
    receipt = read_json(row['current']['journal'])['documentation_compatibility']
    assert receipt['new_release']['digest'] == new['digest']
    assert receipt['resources'] == req['reviews']['documentation_update']['resources']
    assert scan_activity([runtime])['blockers']


@pytest.mark.parametrize('mutation', [
    lambda root: (root / 'codex/skills/al/scripts/al_state.py').write_bytes(
        (root / 'codex/skills/al/scripts/al_state.py').read_bytes() + b'\n'),
    lambda root: (root / 'codex/skills/al/reference/schemas/task-package.json').write_bytes(
        (root / 'codex/skills/al/reference/schemas/task-package.json').read_bytes() + b'\n'),
    lambda root: (root / 'claude/agents/al-engineer.md').write_bytes(
        (root / 'claude/agents/al-engineer.md').read_bytes() + b'\n'),
    lambda root: (root / 'codex/agents/al-engineer.toml').write_bytes(
        (root / 'codex/agents/al-engineer.toml').read_bytes() + b'\n'),
    lambda root: (root / 'codex/skills/al/reference/unknown.md').write_text('addition'),
    lambda root: (root / 'codex/skills/al/reference/performance.md').unlink(),
    lambda root: (root / DOC).chmod(0o755),
    lambda root: (root / 'codex/skills/al/reference/performance.md').write_text('unrelated'),
    lambda root: (root / 'codex/skills/al/reference/parity-manifest.json').write_text('{}'),
])
def test_non_documentation_delta_refused(enrolled, mutation):
    w, target, row, runtime, pin = enrolled
    new = tagged(w, mutation)
    with pytest.raises(ValueError):
        O.run_operation(request(enrolled, new), w['state'])
    assert load_record(w['state'], target['target_id']) == row


@pytest.mark.parametrize('overrides', [
    {'kind': 'install'}, {'kind': 'rollback'}, {'version': None}, {'version': 'latest'},
    {'policy': 'follow'}, {'adoption': {'reviewed': True}}, {'status_line': True}, {'all': True},
])
def test_mode_scope_is_explicit(enrolled, overrides):
    w, target, row, runtime, pin = enrolled
    new = tagged(w)
    with pytest.raises(ValueError):
        O.run_operation(dict(request(enrolled, new), **overrides), w['state'])
    assert load_record(w['state'], target['target_id']) == row


def test_malformed_activity_is_never_acknowledged(enrolled):
    w, target, row, runtime, pin = enrolled
    new = tagged(w)
    path = runtime / 'retained.json'
    state = json.loads(path.read_bytes())
    state['status'] = 'INVENTED'
    path.write_text(json.dumps(state))
    with pytest.raises(ActiveRuns):
        O.run_operation(request(enrolled, new), w['state'])
    assert load_record(w['state'], target['target_id']) == row


@pytest.mark.parametrize('mutation', ['file', 'plan'])
def test_revalidation_catches_mutation_and_preserves_pending_recovery(enrolled, monkeypatch, mutation):
    w, target, row, runtime, pin = enrolled
    new = tagged(w)
    original = O._execute
    def mutate(*args, **kwargs):
        if mutation == 'file':
            package_path(target, 'claude').joinpath('SKILL.md').write_text('changed after plan')
        else:
            args[3]['configs']['codex']['raw'] = b'changed prepared configuration'
        return original(*args, **kwargs)
    monkeypatch.setattr(O, '_execute', mutate)
    with pytest.raises(ValueError, match='changed after preparation|prepared plan changed'):
        O.run_operation(request(enrolled, new), w['state'])
    pending = load_record(w['state'], target['target_id'])
    assert pending['pending'] and pending['current'] == row['current']
    assert read_json(w['state'] / 'installer/operations' / pending['pending'] / 'journal.json')['status'] == 'FAILED'


@pytest.mark.parametrize('failure', ['source', 'subdir', 'drift', 'collision', 'cache', 'tag', 'journal', 'review', 'writer', 'index'])
def test_existing_refusals_remain(enrolled, failure):
    w, target, row, runtime, pin = enrolled
    collision = 'codex/skills/al/reference/unmanaged.md'
    new = tagged(w, (lambda root: (root / collision).write_text('unmanaged')) if failure == 'collision' else None)
    req = request(enrolled, new)
    if failure == 'source':
        other = w['root'] / 'other-source'
        other.mkdir()
        git_test(other, 'init', '-q')
        req['source'] = str(other)
    elif failure == 'subdir':
        req['source_subdir'] = 'other'
    elif failure == 'drift':
        package_path(target, 'claude').joinpath('SKILL.md').write_text('drift')
    elif failure == 'collision':
        (package_path(target, 'codex') / 'reference/unmanaged.md').write_text('unmanaged')
    elif failure == 'cache':
        (w['state'] / 'installer/cache' / row['current']['release']['digest'] / 'payload' / DOC).write_text('corrupt')
    elif failure == 'tag':
        git_test(w['source'], 'tag', '-f', 'v1.0.0')
    elif failure == 'journal':
        row['pending'] = 'incomplete'
        write_json(O.record_path(w['state'], target['target_id']), row)
    elif failure == 'review':
        (w['root'] / 'reviewed-instructions.txt').write_text('changed provenance')
    elif failure == 'writer':
        session(runtime / 'writer.json', writers={'w': {'id': 'w', 'state': 'RUNNING'}})
    else:
        index = pin.parent / 'runs'
        index.mkdir()
        (index / 'bad.json').write_text('{invalid')
    with pytest.raises((ValueError, OSError)):
        O.run_operation(req, w['state'])
    assert load_record(w['state'], target['target_id'])['current'] == row['current']


def test_parity_proof_has_only_the_inspected_rationale_field():
    import copy
    from al.installer.documentation_update import _parity
    old = {'schema_version': 1, 'shared_files': ['runtime.py'],
           'platform_adapters': {'reference_rationales': {'handoffs.md': 'old'}}}
    new = copy.deepcopy(old)
    new['platform_adapters']['reference_rationales']['handoffs.md'] = 'reviewed correction'
    changed = {'codex/skills/al/reference/handoffs.md': {}}
    _parity(old, new, changed)
    with pytest.raises(ValueError):
        _parity(old, new, {})
    for key, value in [('schema_version', 2), ('shared_files', ['different.py']), ('new_hash_fields', {})]:
        altered = dict(new, **{key: value})
        with pytest.raises(ValueError):
            _parity(old, altered, changed)


def test_batch_api_cannot_select_documentation_exception(tmp_path):
    with pytest.raises(ValueError, match='batch updates are forbidden'):
        O.update_registered(None, tmp_path, documentation_only=True)


@pytest.mark.parametrize('mutation', ['postimage', 'provenance', 'runtime-index'])
def test_completed_publication_revalidates(enrolled, monkeypatch, mutation):
    w, target, row, runtime, pin = enrolled
    new = tagged(w)
    req = request(enrolled, new)
    original = O.smoke_installation
    def mutate(*args, **kwargs):
        checks = original(*args, **kwargs)
        if mutation == 'postimage':
            package_path(target, 'claude').joinpath('SKILL.md').write_text('post-apply mutation')
        elif mutation == 'provenance':
            (w['root'] / 'reviewed-instructions.txt').write_text('post-apply mutation')
        else:
            root = pin.parent / 'runs'
            root.mkdir()
            (root / 'bad.json').write_text('{invalid')
        return checks
    monkeypatch.setattr(O, 'smoke_installation', mutate)
    before = (runtime / 'retained.json').read_bytes(), pin.read_bytes()
    with pytest.raises(ValueError):
        O.run_operation(req, w['state'])
    pending = load_record(w['state'], target['target_id'])
    assert pending['pending'] and pending['current'] == row['current']
    assert ((runtime / 'retained.json').read_bytes(), pin.read_bytes()) == before


def test_injected_engine_failure_retains_recovery_and_runtime(enrolled, monkeypatch):
    w, target, row, runtime, pin = enrolled
    new = tagged(w)
    req = request(enrolled, new)
    original = O.D.apply
    calls = []
    def fail(manifest, backup):
        calls.append(backup)
        if len(calls) == 2:
            raise OSError('injected second platform failure')
        return original(manifest, backup)
    monkeypatch.setattr(O.D, 'apply', fail)
    before = (runtime / 'retained.json').read_bytes(), pin.read_bytes()
    with pytest.raises(OSError, match='injected'):
        O.run_operation(req, w['state'])
    pending = load_record(w['state'], target['target_id'])
    assert pending['pending'] and pending['current'] == row['current']
    assert ((runtime / 'retained.json').read_bytes(), pin.read_bytes()) == before
    with pytest.raises(ValueError, match='unresolved'):
        O.run_operation(req, w['state'])


def test_public_cli_requires_global_and_accepts_reviewed_update(enrolled):
    from al.tests.conftest import run_alctl
    w, target, row, runtime, pin = enrolled
    new = tagged(w)
    req = request(enrolled, new)
    path = w['root'] / 'reviews.json'
    write_json(path, req['reviews'])
    args = ['update', '--documentation-only', '--home', w['home'], '--state-root', w['state'],
            '--version', '1.0.1', '--reviews', path, '--runtime-root', runtime]
    refused = run_alctl(*args, cwd=w['root'])
    assert refused.returncode == 1 and 'requires --global' in refused.data['error']
    result = run_alctl(*args, '--global', cwd=w['root'])
    assert result.returncode == 0, result.stdout
    assert result.data['version'] == '1.0.1' and result.data['activity']['blockers']
