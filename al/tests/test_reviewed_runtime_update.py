"""Exact reviewed updates preserve independently protected historical runtimes."""
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zlib

import pytest

from al.installer.cli import parser
from al.installer import operations as O
from al.installer.manifest import build_manifest
from al.installer.registry import canonical, digest, load_record, read_json, write_json
from al.installer.runtime_pin_transaction import key as subject_key, layout_fields
from al.installer.reviewed_runtime_update import (ACCEPTED_LIMITS, APPROVED, approved_delta,
                                                 release_identity, validate_request)
from al.installer.source import resolve_release
from al.installer.targets import package_path
from al.tests.conftest import copy_al_source
from al.tests.test_manifest import git_test

ROOT = Path(__file__).resolve().parents[2]
# Verbatim hash-bound independent review certificate for this exact historical
# transition, compressed solely to keep this fixture self-contained.
REVIEW_RECORD = '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'


def test_public_mode_is_explicit_and_distinct():
    args = parser().parse_args(['update', '--global', '--version', '0.1.8', '--reviewed-runtime-update'])
    assert args.reviewed_runtime_update and not args.documentation_only


@pytest.fixture(scope='module')
def manifests():
    return tuple(build_manifest(resolve_release(str(ROOT), version=v)) for v in ('0.1.7', '0.1.8'))


def test_exact_reviewed_delta_has_only_two_new_modules(manifests):
    old, new = manifests
    assert approved_delta(old, new) == APPROVED['resources']
    additions = [name for name, item in APPROVED['resources'].items() if item['before'] is None]
    assert sorted(additions) == [p + '/skills/al/scripts/al_writer_progress.py' for p in ('claude', 'codex')]


@pytest.mark.parametrize('mutation', ['script', 'role', 'schema', 'mode', 'addition', 'deletion', 'workflow', 'parity', 'version', 'commit', 'source', 'shared-corruption'])
def test_arbitrary_runtime_or_resource_hashes_cannot_be_reviewed_in(manifests, mutation):
    old, new = copy.deepcopy(manifests)
    path = 'codex/skills/al/scripts/al_writers.py'
    if mutation == 'role': path = 'claude/agents/al-engineer.md'
    elif mutation == 'schema': path = 'codex/skills/al/reference/schemas/task-package.json'
    elif mutation == 'workflow': path = 'codex/skills/al/reference/workflow.md'
    elif mutation == 'parity': path = 'codex/skills/al/reference/parity-manifest.json'
    if mutation == 'mode': new['files'][path]['mode'] ^= 0o111
    elif mutation == 'addition': new['files']['codex/skills/al/scripts/foreign.py'] = dict(new['files'][path])
    elif mutation == 'deletion': del new['files'][path]
    elif mutation == 'version': new['version'] = '0.1.9'
    elif mutation == 'commit': new['commit'] = 'a' * 40
    elif mutation == 'source': new['source_identity'] = '/foreign-source'
    elif mutation == 'shared-corruption':
        path = 'codex/skills/al/SKILL.md'
        old['files'][path]['sha256'] = new['files'][path]['sha256'] = '0' * 64
    else: new['files'][path]['sha256'] = '0' * 64
    with pytest.raises(ValueError): approved_delta(old, new)


@pytest.mark.parametrize('override', [{'kind': 'install'}, {'kind': 'rollback'}, {'version': None},
    {'version': 'latest'}, {'version': '0.1.9'}, {'documentation_only': True}, {'all': True},
    {'adoption': {'reviewed': True}}, {'policy': 'follow'}, {'status_line': True}])
def test_request_scope_is_closed(override):
    target = {'scope': 'global'}
    row = {'target': target, 'current': {'release': {}}}
    request = dict(kind='update', version='0.1.8', target=target, reviewed_runtime_update=True)
    validate_request(request, row)
    with pytest.raises(ValueError): validate_request(dict(request, **override), row)


def invoke(argv, cwd, env):
    result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    try: result.data = json.loads(result.stdout)
    except json.JSONDecodeError: result.data = None
    return result


@pytest.fixture
def enrolled(tmp_path, monkeypatch, request):
    """Real old payload pin, genuine managed updater and exact local immutable tags."""
    source = tmp_path / 'tagged-source.git'
    git_test(tmp_path, 'init', '--bare', str(source))
    git_test(source, 'fetch', '--no-tags', str(ROOT),
             'refs/tags/v0.1.7:refs/tags/v0.1.7', 'refs/tags/v0.1.8:refs/tags/v0.1.8')
    old_release = resolve_release(str(source), version='0.1.7')
    old_source = copy_al_source(tmp_path / 'old-bootstrap')
    for platform in ('codex', 'claude'):
        shutil.rmtree(old_source / platform)
    for name, item in old_release['tree'].items():
        path = old_source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item['raw']);path.chmod(item['mode'])
    new_source = copy_al_source(tmp_path / 'new-bootstrap')
    home, repo = tmp_path / 'home', tmp_path / 'repo'
    home.mkdir();repo.mkdir()
    git_test(repo, 'init', '-q');git_test(repo, 'commit', '--allow-empty', '-qm', 'fixture')
    state, launcher = home / 'registry', home / 'bin/alctl'
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
    for name in ('AL_RUNTIME_DIR', 'AL_DISTRIBUTION_STATE', 'PYTHONPATH', 'PYTHONHOME',
                 'CLAUDE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_PROJECT_DIR'):
        env.pop(name, None)
    def boot(distribution):
        result = invoke([sys.executable, '-I', '-S', '-B', distribution / 'alctl.py', 'bootstrap',
                         '--source-root', distribution, '--destination', launcher, '--state-root', state], repo, env)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.data
    old_boot = boot(old_source)
    installed = invoke([launcher, 'install', '--global', '--home', home, '--state-root', state,
                        '--source', source, '--version', '0.1.7'], repo, env)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    started = invoke([launcher, 'run', '--repository', repo, '--platform', 'codex', '--session', 'old-session',
                      '--', 'start', '--spec', 'An old pinned fixture remains independent.'], repo, env)
    assert started.returncode == 0, started.stdout + started.stderr
    if getattr(request, 'param', None) == 'reserved':
        reserved = invoke([launcher, 'run', '--repository', repo, '--platform', 'codex', '--session', 'reserved-session',
                           '--', 'start', '--review-mode', 'unsupported', '--spec', 'A failed fixed-pin initialization.'], repo, env)
        assert reserved.returncode != 0
    fault = getattr(request, 'param', None)
    if fault in {'race-state', 'race-index', 'race-pin', 'race-review', 'late-state', 'plan', 'preimage', 'second-platform'}:
        history = repo / '.codex/al-runtime/old-session.json'
        index = next((state / 'al-run-pins/runs').glob('*.json'))
        pin = state / 'al-run-pins/pins' / read_json(index)['pin']['pin_identity']
        path = new_source / 'installer/operations.py'
        raw = path.read_text()
        if fault in {'race-state', 'late-state'}:
            statement = f"write_json({str(history)!r}, dict(read_json({str(history)!r}), review_iteration=999))"
        elif fault == 'race-index':
            statement = f"write_json({str(index)!r}, dict(read_json({str(index)!r}), status='RESERVED'))"
        elif fault == 'race-review':
            statement = f"Path({str(tmp_path / 'operator.txt')!r}).write_text('changed at barrier')"
        elif fault == 'race-pin':
            member = str(pin / 'payload/codex/skills/al/scripts/al_state.py')
            statement = f"Path({member!r}).write_bytes(Path({member!r}).read_bytes() + b'\\n')"
        elif fault == 'plan':
            statement = "prepared['configs']['codex']['raw'] = b'forbidden plan mutation'"
        elif fault == 'preimage':
            statement = f"Path({str(home / '.codex/skills/al/SKILL.md')!r}).write_text('preimage changed after planning')"
        else:
            statement = "raise OSError('injected second platform failure')"
        if fault in {'plan', 'preimage'}:
            needle = '        _recheck(prepared)\n'
            raw = raw.replace(needle, '        ' + statement + '\n' + needle, 1)
        elif fault == 'late-state':
            needle = '        # The completed snapshot references the exact resulting engine generations.\n'
            raw = raw.replace(needle, '        ' + statement + '\n' + needle, 1)
        else:
            needle = '            D.apply(engine_manifest, backup)\n'
            if fault == 'second-platform':
                raw = raw.replace(needle, "            if platform == 'claude':\n                " + statement + '\n' + needle, 1)
            else:
                raw = raw.replace(needle, needle + "            if platform == 'codex':\n                " + statement + '\n', 1)
        assert raw != path.read_text()
        path.write_text(raw)
    new_boot = boot(new_source)
    row = O.records(state)[0]
    new = build_manifest(resolve_release(str(source), version='0.1.8'))
    certificate = tmp_path / 'independent-review.json'
    certificate.write_bytes(zlib.decompress(base64.b64decode(REVIEW_RECORD)))
    assert digest(certificate.read_bytes()) == '107715fb8e6fa6c7b1c189bd1605503af2e33076ad006476b68397d86d1798b1'
    instructions, authorization = tmp_path / 'instructions.txt', tmp_path / 'operator.txt'
    instructions.write_text('No additional instruction files apply to this isolated fixture.')
    authorization.write_text('Fixture operator authorizes this exact future-payload update and its accepted limits.')
    def reference(path): return {'path': str(path), 'sha256': digest(path.read_bytes())}
    review = {'schema_version': 1, 'kind': 'reviewed-runtime-update-v1', 'reviewed': True,
              'reason': 'Exact independently reviewed writer/preflight/planning/progress repair.',
              'target': row['target'], 'source': row['source'],
              'old_release': release_identity(row['current']['release']), 'new_release': release_identity(new),
              'resources': APPROVED['resources'], 'accepted_limits': ACCEPTED_LIMITS,
              'independent_review': reference(certificate), 'instruction_review': reference(instructions),
              'operator_authorization': reference(authorization)}
    reviews = tmp_path / 'update-reviews.json'
    write_json(reviews, {'reviewed_runtime_update': review})
    return dict(root=tmp_path, source=source, home=home, repo=repo, state=state, launcher=launcher, env=env,
                row=row, new=new, reviews=reviews, old_boot=old_boot, new_boot=new_boot,
                old_source=old_source, new_source=new_source, authorization=authorization)


def update(w, *extra, mode=True):
    args = [w['launcher'], 'update', '--global', '--home', w['home'], '--state-root', w['state'], '--version', '0.1.8']
    if mode: args += ['--reviewed-runtime-update', '--reviews', w['reviews']]
    return invoke([*args, *extra], w['repo'], w['env'])


def immutable_bytes(root):
    return {str(path): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in root.rglob('*') if path.is_file()}


def test_real_old_pin_survives_and_fresh_bootstrap_selects_new_payload(enrolled):
    w = enrolled
    before = immutable_bytes(w['state'] / 'al-run-pins')
    state = w['repo'] / '.codex/al-runtime/old-session.json'
    old_state = state.read_bytes()
    old_snapshot = immutable_bytes(Path(w['old_boot']['snapshot']))
    refused = update(w, mode=False)
    assert refused.returncode == 3 and refused.data['status'] == 'deferred'
    result = update(w)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.data['activity']['blockers']
    assert immutable_bytes(w['state'] / 'al-run-pins') == before and state.read_bytes() == old_state
    assert immutable_bytes(Path(w['old_boot']['snapshot'])) == old_snapshot
    row = load_record(w['state'], w['row']['target_id'])
    O.check_current(row, w['state'])
    receipt = read_json(row['current']['journal'])['reviewed_runtime_compatibility']
    assert len(receipt['retained']['contexts']) == 1
    assert receipt['retained']['contexts'][0]['status'] == 'ACTIVE'
    shown = invoke([w['launcher'], 'run', '--repository', w['repo'], '--platform', 'codex',
                    '--session', 'old-session', '--existing-run', '--', 'show', '--json'], w['repo'], w['env'])
    assert shown.returncode == 0 and shown.data['run_id'] == json.loads(old_state)['run_id']
    fresh = invoke([w['launcher'], 'run', '--repository', w['repo'], '--platform', 'codex', '--session', 'future-session',
                    '--', 'start', '--spec', 'A future run selects the newly bootstrapped payload.'], w['repo'], w['env'])
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    bindings = [read_json(path) for path in (w['state'] / 'al-run-pins/runs').glob('*.json')]
    old, new = [next(item for item in bindings if item['session_id'] == sid) for sid in ('old-session', 'future-session')]
    old_pin = w['state'] / 'al-run-pins/pins' / old['pin']['pin_identity']
    new_pin = w['state'] / 'al-run-pins/pins' / new['pin']['pin_identity']
    assert not (old_pin / 'payload/codex/skills/al/scripts/al_writer_progress.py').exists()
    script = new_pin / 'payload/codex/skills/al/scripts/al_writer_progress.py'
    assert digest(script.read_bytes()) == w['new']['files']['codex/skills/al/scripts/al_writer_progress.py']['sha256']
    assert state.read_bytes() == old_state


@pytest.mark.parametrize('enrolled', ['reserved'], indirect=True)
def test_actual_reserved_initialization_is_preserved_with_unknown_settlement(enrolled):
    w = enrolled
    before = immutable_bytes(w['state'] / 'al-run-pins')
    result = update(w)
    assert result.returncode == 0, result.stdout + result.stderr
    row = load_record(w['state'], w['row']['target_id'])
    contexts = read_json(row['current']['journal'])['reviewed_runtime_compatibility']['retained']['contexts']
    reserved = next(item for item in contexts if item['status'] == 'RESERVED')
    assert reserved['classification'] == 'pinned-initialization-settlement-unknown'
    assert reserved['state_sha256'] is None
    assert immutable_bytes(w['state'] / 'al-run-pins') == before


@pytest.mark.parametrize('failure', ['writer', 'running-evidence', 'interrupted-evidence', 'cleanup',
    'malformed-state', 'foreign-origin', 'unbound-history', 'malformed-index', 'unknown-layout',
    'corrupt-pin', 'missing-pin', 'pin-alias', 'drift', 'collision', 'moved-tag', 'pending',
    'authorization', 'review', 'old-cache'])
def test_existing_refusals_are_not_overridden(enrolled, failure):
    w = enrolled
    state_path = w['repo'] / '.codex/al-runtime/old-session.json'
    state = read_json(state_path)
    index = next((w['state'] / 'al-run-pins/runs').glob('*.json'))
    binding = read_json(index)
    pin = w['state'] / 'al-run-pins/pins' / binding['pin']['pin_identity']
    if failure == 'writer':
        state['writers']['live'] = {'id': 'live', 'state': 'ACTIVE', 'role': 'al-engineer',
                                   'files': ['owned.py'], 'commands': [], 'phase': state['phase'], 'started_at': 0}
    elif failure in {'running-evidence', 'interrupted-evidence', 'cleanup'}:
        entry = {'id': 'E1', 'status': 'RUNNING' if failure == 'running-evidence' else 'INFRA',
                 'run': state['run_id'], 'argv': ['python3', '-c', 'pass'], 'reservation': 'fixture'}
        if failure == 'interrupted-evidence': entry['interrupted'] = True
        if failure == 'cleanup': entry['cleanup'] = {'exit': 0, 'errors': ['owned cleanup unresolved'], 'unresolved': False}
        state['evidence']['entries'] = [entry];state['evidence']['seq'] = 1
    elif failure == 'malformed-state': state['review_iteration'] = -1
    elif failure == 'foreign-origin': state['runtime_origin']['run_id'] = 'foreign'
    elif failure == 'unbound-history':
        other = dict(state, session_id='unbound', status='APPROVED', phase='DONE')
        write_json(state_path.parent / 'unbound.json', other)
    elif failure == 'malformed-index':
        path = index.parent / ('a' * 64 + '.json');path.write_text('{invalid');path.chmod(0o600)
    elif failure == 'unknown-layout':
        binding['schema_version'] = 9;write_json(index, binding)
    elif failure == 'corrupt-pin':
        path = pin / 'payload/codex/skills/al/scripts/al_state.py';path.write_bytes(path.read_bytes() + b'\n')
    elif failure == 'missing-pin': shutil.rmtree(pin)
    elif failure == 'pin-alias': os.link(pin / 'manifest.json', w['root'] / 'pin-alias')
    elif failure == 'drift': package_path(w['row']['target'], 'codex').joinpath('SKILL.md').write_text('managed drift')
    elif failure == 'collision': package_path(w['row']['target'], 'codex').joinpath('scripts/al_writer_progress.py').write_text('unmanaged')
    elif failure == 'moved-tag': git_test(w['source'], 'update-ref', 'refs/tags/v0.1.7', APPROVED['new']['commit'])
    elif failure == 'pending': write_json(O.record_path(w['state'], w['row']['target_id']), dict(w['row'], pending='unresolved'))
    elif failure == 'authorization': w['authorization'].write_text('changed after exact review')
    elif failure == 'review':
        review = read_json(w['reviews']);review['reviewed_runtime_update']['accepted_limits'] = []
        write_json(w['reviews'], review)
    else:
        path = Path(w['row']['current']['cache']) / 'payload/codex/skills/al/SKILL.md'
        path.write_text('corrupt installed historical cache')
    if failure in {'writer', 'running-evidence', 'interrupted-evidence', 'cleanup', 'malformed-state', 'foreign-origin'}:
        write_json(state_path, state)
    preserved = immutable_bytes(w['state'] / 'al-run-pins')
    preserved_state = state_path.read_bytes()
    result = update(w)
    assert result.returncode != 0, result.stdout + result.stderr
    row = load_record(w['state'], w['row']['target_id'])
    assert row['current'] == w['row']['current']
    assert row['pending'] == ('unresolved' if failure == 'pending' else None)
    assert immutable_bytes(w['state'] / 'al-run-pins') == preserved
    assert state_path.read_bytes() == preserved_state


@pytest.mark.parametrize('enrolled', ['race-state', 'race-index', 'race-pin', 'race-review',
                                     'late-state', 'plan', 'preimage', 'second-platform'], indirect=True)
def test_barrier_mutation_and_partial_failure_keep_manual_recovery(enrolled, request):
    w = enrolled
    fault = request.node.callspec.params['enrolled']
    old_state = (w['repo'] / '.codex/al-runtime/old-session.json').read_bytes()
    old_bindings = immutable_bytes(w['state'] / 'al-run-pins')
    result = update(w)
    assert result.returncode != 0, result.stdout + result.stderr
    row = load_record(w['state'], w['row']['target_id'])
    assert row['current'] == w['row']['current'] and row['pending']
    journal = read_json(w['state'] / 'installer/operations' / row['pending'] / 'journal.json')
    assert journal['status'] == 'FAILED' and 'manual reconciliation' in journal['recovery']
    if fault not in {'plan', 'preimage'}:
        assert (package_path(row['target'], 'codex') / 'scripts/al_writer_progress.py').is_file()
    else:
        assert not (package_path(row['target'], 'codex') / 'scripts/al_writer_progress.py').exists()
    if fault not in {'race-state', 'late-state'}:
        assert (w['repo'] / '.codex/al-runtime/old-session.json').read_bytes() == old_state
    if fault not in {'race-index', 'race-pin'}:
        assert immutable_bytes(w['state'] / 'al-run-pins') == old_bindings
    again = update(w)
    assert again.returncode != 0 and 'unresolved' in again.data['error']


def add_layout_metadata(w, version, *, writer=False, reserved=False):
    """Structural layout fixture backed by a genuine public pin, not run authority."""
    root = w['state'] / 'al-run-pins'
    original = read_json(next((root / 'runs').glob('*.json')))
    transaction = dict(original, schema_version=version, kind='run-pin-binding-v' + str(version),
                       session_id='layout-' + str(version), run_id='0123abc' + str(version),
                       publication_transaction='b' * 64, status='RESERVED' if reserved else 'ACTIVE', initialization=None)
    if version == 2:
        runtime = w['repo'] / '.codex/al-runtime/sessions' / transaction['session_id']
        transaction.update(runtime_layout='harness-session-directory-v1', runtime_directory=str(runtime))
    else:
        harness = {'authority_identity': 'e' * 64, 'execution_id': 'fixture-layout',
                   'authority_generation': 0, 'epoch_id': 'legacy-0'}
        runtime = root / 'harness-runtimes' / harness['authority_identity'] / harness['execution_id'] / harness['epoch_id']
        transaction.update(runtime_layout='harness-durable-epoch-v1', runtime_directory=str(runtime), harness_binding=harness)
    cursor = runtime
    missing = []
    while not cursor.exists(): missing.append(cursor);cursor = cursor.parent
    for path in reversed(missing): path.mkdir(mode=0o700)
    state_path = runtime / (transaction['session_id'] + '.json')
    if not reserved:
        initialized = read_json(original['initialization']['stored_path'])
        initialized.update(schema_version=version, kind='proof-admission-initialization-v' + str(version),
                           session_id=transaction['session_id'], run_id=transaction['run_id'])
        initialized.update({key: transaction[key] for key in layout_fields(transaction)})
        sha = digest(canonical(initialized) + b'\n')
        reference = dict(original['initialization'], id=sha, sha256=sha, kind=initialized['kind'],
                         stored_path=str(root / 'initializations' / sha))
        write_json(reference['stored_path'], initialized)
        transaction['initialization'] = reference
        state = read_json(w['repo'] / '.codex/al-runtime/old-session.json')
        state.update(session_id=transaction['session_id'], run_id=transaction['run_id'])
        state['runtime_origin'] = {key: transaction[key] for key in
            ('kind', 'schema_version', 'run_id', 'publication_transaction', 'pin', 'initialization')}
        state['runtime_origin'].update({key: transaction[key] for key in layout_fields(transaction)})
        state['proof_admission']['initialization'] = reference
        if writer:
            state['writers']['hidden'] = {'id': 'hidden', 'state': 'UNRESOLVED', 'role': 'al-engineer',
                                         'files': ['owned.py'], 'commands': [], 'phase': state['phase'], 'started_at': 0}
        write_json(state_path, state)
    subject = {key: transaction[key] for key in ('repository_identity', 'platform', 'session_id')}
    write_json(root / 'runs' / (subject_key(subject) + '.json'), transaction)
    return transaction, state_path


@pytest.mark.parametrize('version', [2, 3])
def test_supported_private_layout_metadata_is_covered_with_real_pin(enrolled, version):
    w = enrolled
    transaction, state_path = add_layout_metadata(w, version)
    before = immutable_bytes(w['state'] / 'al-run-pins')
    state = state_path.read_bytes()
    result = update(w)
    assert result.returncode == 0, result.stdout + result.stderr
    row = load_record(w['state'], w['row']['target_id'])
    contexts = read_json(row['current']['journal'])['reviewed_runtime_compatibility']['retained']['contexts']
    assert any(item['schema_version'] == version and item['run_id'] == transaction['run_id'] for item in contexts)
    assert immutable_bytes(w['state'] / 'al-run-pins') == before and state_path.read_bytes() == state


def test_v2_unresolved_writer_cannot_disappear_from_flat_root_scan(enrolled):
    w = enrolled
    add_layout_metadata(w, 2, writer=True)
    result = update(w)
    assert result.returncode == 3 and 'active or unresolved writer' in result.data['error']
    assert load_record(w['state'], w['row']['target_id']) == w['row']


def test_v3_reserved_and_missing_state_remain_refusals(enrolled):
    w = enrolled
    transaction, path = add_layout_metadata(w, 3, reserved=True)
    result = update(w)
    assert result.returncode != 0 and 'durable run is reserved or its active state is unavailable' in result.data['error']
    assert not path.exists()
    assert load_record(w['state'], w['row']['target_id']) == w['row']


def superseded_chain(w, *, missing=False):
    """Protected metadata fixture with real pins; deliberately grants no epoch authority."""
    from al.installer.runtime_successor_lineage import revisions
    from al.installer.runtime_successor_start import _context
    root = w['state'] / 'al-run-pins'
    prior_path = next((root / 'runs').glob('*.json'))
    prior = read_json(prior_path)
    prior_state_path = w['repo'] / '.codex/al-runtime/old-session.json'
    prior_state = read_json(prior_state_path)
    identifier = 'superseded-fixture'
    directory = root / 'successors' / identifier
    directory.mkdir(parents=True, mode=0o700)
    authority_dir = w['root'] / 'authority';authority_dir.mkdir(mode=0o700)
    def ref(path, value):
        if isinstance(value, bytes):
            path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(value);path.chmod(0o600)
        else: write_json(path, value)
        return {'stored_path': str(path), 'sha256': digest(path.read_bytes())}
    def external(path, value, *, prefixed=False):
        reference = ref(path, value)
        return {'path': reference['stored_path'], 'sha256': ('sha256:' if prefixed else '') + reference['sha256']}
    handoff = external(authority_dir / 'handoff.json', {'fixture': 'opaque historical handoff'}, prefixed=True)
    prior_state['integration'] = {'handoff': handoff['path'], 'agents': {}}
    write_json(prior_state_path, prior_state)
    successor, successor_state_path = add_layout_metadata(w, 2)
    new_handoff = external(authority_dir / 'prepared/handoff.json', {'fixture': 'opaque successor handoff'}, prefixed=True)
    successor_state = read_json(successor_state_path)
    successor_state['integration'] = {'handoff': new_handoff['path'], 'agents': {}}
    write_json(successor_state_path, successor_state)
    successor_subject = {k: successor[k] for k in ('repository_identity', 'platform', 'session_id')}
    prior_ref = ref(directory / 'predecessor-binding.json', prior)
    state_ref = ref(directory / 'predecessor-state.json', prior_state_path.read_bytes()) if not missing else None
    pin_manifest = read_json(root / 'pins' / successor['pin']['pin_identity'] / 'manifest.json')
    package_sha = prior_state['task_package']['sha256']
    requirement_hashes = revisions(package_sha, prior_state['amendments'])
    bridge_files = {'__init__.py': b'', 'execution_prepared_authority.py': b"raise AssertionError('bridge must never run during global copy')\n"}
    fingerprint = digest(b''.join(name.encode() + raw for name, raw in sorted(bridge_files.items())))
    bridge_base = authority_dir / 'prepared-authority/bridge-revisions' / fingerprint
    for name, raw in bridge_files.items(): ref(bridge_base / 'trusted/harness' / name, raw)
    manifest_ref = ref(bridge_base / 'trusted/manifest.json',
                       {'fingerprint': fingerprint, 'files': {name: digest(raw) for name, raw in bridge_files.items()}})
    Path(manifest_ref['stored_path']).chmod(0o644)
    interpreter = pin_manifest['interpreter']
    authority_ref = ref(bridge_base / 'authority.json', {'schema_version': 1, 'kind': 'prepared-harness-authority-v1',
        'snapshot': str(bridge_base / 'trusted'), 'manifest_sha256': manifest_ref['sha256'],
        'interpreter': {'executable': interpreter['canonical_executable'], 'sha256': interpreter['sha256'], 'mode': interpreter['mode']}})
    request = {'schema_version': 2 if missing else 1, 'request_id': identifier, 'execution_id': 'fixture-execution',
        'expected_epoch_id': 'legacy-0', 'expected_authority_generation': 0,
        'predecessor_subject': {'session_id': prior['session_id'], 'run_id': prior['run_id']},
        'approved_requirement_hash': requirement_hashes[-1],
        'expected_candidate_identity': {'sha256': '1' * 64, 'opaque_limitations': []},
        'distribution_identity': pin_manifest['closure_digest'], 'authority_snapshot_ref': authority_ref,
        'reason': 'Structural metadata fixture; current epoch approval is not assessed.'}
    recovery_ref = None
    if missing:
        recovery = {'original_package_ref': prior_state['task_package'], 'amendments': prior_state['amendments'],
                    'revision_hashes': requirement_hashes}
        lineage_source = ref(authority_dir / 'recovery-lineage.json', recovery)
        recovery_ref = ref(directory / 'recovery-lineage.json', recovery)
        settlement = ref(authority_dir / 'settlement.json', {'fixture': 'historical receipt, not live process settlement'})
        request.update(transition_mode='fresh-planning-missing-state', preview_sha256='sha256:' + '2' * 64,
                       recovery_lineage_ref=lineage_source, settlement_ref=settlement,
                       authority_handoff_ref=handoff, expected_predecessor_state_sha256=None)
        prior_state_path.unlink()
        (prior_state_path.parent / 'current').unlink()
    request_ref = ref(directory / 'request.json', request)
    initialized = read_json(prior['initialization']['stored_path'])
    anchors = {'repository_identity': prior['repository_identity'], 'baseline_head_oid': prior_state['baseline_head'],
               'observed_head_oid': prior_state['baseline_head']}
    plan = {'schema_version': 1, 'kind': 'missing-state-successor-start-plan-v1' if missing else 'successor-start-plan-v1',
            'request': request, 'storage': str(root), 'authority_directory': str(authority_dir),
            'predecessor_transaction': prior, 'successor_transaction': dict(successor, initialization=None, status='RESERVED'),
            'predecessor_binding_ref': prior_ref, 'source_anchors': anchors, 'predecessor_initialization': initialized}
    plan.update({'recovery_lineage_ref': recovery_ref, 'authority_handoff_ref': handoff} if missing else {'predecessor_state_ref': state_ref})
    plan_ref = ref(directory / 'plan.json', plan)
    context = _context(plan_ref, plan)
    ref(directory / 'context.json', context)
    package_ref = external(authority_dir / 'prepared/task-package.json',
                           Path(prior_state['task_package']['stored_path']).read_bytes(), prefixed=True)
    classification = external(authority_dir / 'prepared/classification.json', {'kind': 'prepared-exact-template-classification-v1',
        'context': context, 'predecessor_reference': None, 'original_inventory_sha256': None, 'original_classification_sha256': None})
    ref(directory / 'resources.json', {'handoff_ref': new_handoff, 'package_ref': package_ref, 'classification_ref': classification})
    requirements = {'schema_version': 1, 'kind': 'successor-requirement-lineage-v1',
        'predecessor': {k: prior[k] for k in ('repository_identity', 'platform', 'session_id', 'run_id', 'pin', 'initialization')},
        'successor': {k: successor[k] for k in ('repository_identity', 'platform', 'session_id', 'run_id', 'pin')},
        'original_package': prior_state['task_package'], 'successor_package': successor_state['task_package'],
        'amendments': prior_state['amendments'], 'successor_amendments': successor_state['amendments'],
        'revision_hashes': requirement_hashes, 'requirement_lineage_hash': requirement_hashes[-1], 'scoped_acceptance': {}}
    requirements_ref = ref(directory / 'requirements.json', requirements)
    inherited_key = 'recovery_lineage_ref' if missing else 'predecessor_state_ref'
    inherited_ref = recovery_ref if missing else state_ref
    lineage = {'schema_version': 1, 'kind': 'missing-state-successor-source-lineage-v1' if missing else 'successor-source-lineage-v1',
        'request_id': identifier, 'preparation_sha256': plan_ref['sha256'], 'requirements_ref': requirements_ref,
        'predecessor_binding_ref': prior_ref, inherited_key: inherited_ref, 'source_anchors': anchors,
        'settled_candidate_identity': request['expected_candidate_identity'], 'successor_transaction': plan['successor_transaction']}
    lineage_ref = ref(directory / 'lineage.json', lineage)
    ref(directory / 'lineage-reference.json', lineage_ref)
    receipt = {'schema_version': 1, 'kind': 'prepared-successor-v1', 'state': 'PREPARED', 'request_id': identifier,
        'request_sha256': digest(canonical(request)), 'preparation_sha256': plan_ref['sha256'],
        'predecessor_binding_ref': prior_ref, inherited_key: inherited_ref, 'lineage_ref': lineage_ref,
        'successor_subject': successor_subject, 'successor_run_id': successor['run_id'], 'successor_transaction': successor,
        'successor_state_ref': ref(directory / 'successor-state.json', successor_state_path.read_bytes()),
        'successor_handoff_ref': new_handoff, 'prepared_classification_ref': classification,
        'candidate_identity': request['expected_candidate_identity'], 'distribution_identity': pin_manifest['closure_digest'],
        'native_resumed': False, 'product_authority': False}
    if missing: receipt.update(transition_mode=request['transition_mode'], preview_sha256=request['preview_sha256'])
    receipt_ref = ref(directory / 'receipt.json', receipt)
    ref(directory / 'journal.json', {'schema_version': 1, 'kind': 'successor-preparation-journal-v1', 'state': 'PREPARED',
        'request_ref': request_ref, 'successor_subject': successor_subject, 'receipt_ref': receipt_ref})
    for folder in ('successor-fences', 'successor-active'):
        (root / folder).mkdir(mode=0o700, exist_ok=True)
    ref(root / 'successor-fences' / (subject_key(successor_subject) + '.json'),
        {'schema_version': 1, 'kind': 'prepared-successor-fence-v1', 'subject': successor_subject,
         'request_id': identifier, 'plan_sha256': plan_ref['sha256']})
    selector = {'schema_version': 1, 'kind': 'active-successor-epoch-v1', 'activated_at': 1.0, 'predecessor_child_ids': [],
        'execution_id': request['execution_id'], 'request_id': identifier, 'storage': str(root),
        'epoch_id': 'epoch-' + successor['run_id'], 'authority_generation': 1, 'successor_subject': successor_subject,
        'successor_transaction': successor, 'preparation_sha256': plan_ref['sha256']}
    selector_ref = ref(root / 'successor-active' / (subject_key(successor_subject) + '.json'), selector)
    activation_ref = ref(directory / 'activation-plan.json', {'kind': 'successor-activation-plan-v1',
        'request_id': identifier, 'receipt': receipt, 'selector': selector, 'selector_ref': selector_ref})
    ref(directory / 'activation-journal.json', {'state': 'ACTIVE', 'activation_ref': activation_ref})
    tombstone = {'schema_version': 1, 'kind': 'superseded-run-binding-v1', 'status': 'SUPERSEDED',
        'repository_identity': prior['repository_identity'], 'platform': prior['platform'], 'session_id': prior['session_id'],
        'run_id': prior['run_id'], 'predecessor_binding_ref': prior_ref, 'successor_subject': successor_subject,
        'activation_ref': activation_ref}
    ref(directory / 'predecessor-tombstone.json', tombstone)
    write_json(prior_path, tombstone)
    return dict(directory=directory, predecessor=prior_path, successor=successor,
                successor_state=successor_state_path, selector=Path(selector_ref['stored_path']),
                bridge=bridge_base, authority=authority_dir, prior_state=prior_state)


@pytest.mark.parametrize('missing', [False, True])
def test_superseded_is_bookkeeping_noncapability_without_epoch_or_bridge_authority(enrolled, missing):
    w = enrolled
    chain = superseded_chain(w, missing=missing)
    before = immutable_bytes(w['state'] / 'al-run-pins')
    result = update(w)
    assert result.returncode == 0, result.stdout + result.stderr
    assert immutable_bytes(w['state'] / 'al-run-pins') == before
    row = load_record(w['state'], w['row']['target_id'])
    contexts = read_json(row['current']['journal'])['reviewed_runtime_compatibility']['retained']['contexts']
    old = next(item for item in contexts if item['status'] == 'SUPERSEDED')
    assert old['classification'] == 'protected-superseded-non-capability'
    assert old['current_sqlite_epoch_and_approval_authority'] == 'NOT_ASSESSED'
    shown = invoke([w['launcher'], 'run', '--repository', w['repo'], '--platform', 'codex',
                    '--session', 'old-session', '--existing-run', '--', 'show', '--json'], w['repo'], w['env'])
    assert shown.returncode != 0
    assert read_json(chain['predecessor'])['status'] == 'SUPERSEDED'


@pytest.mark.parametrize('failure', ['tombstone-field', 'predecessor-ref', 'activation-ref', 'activation-plan',
    'activation-journal', 'tombstone-copy', 'request', 'plan', 'receipt', 'preparation-journal', 'fence',
    'selector', 'selector-ref', 'successor-index', 'successor-state', 'successor-status', 'missing-successor',
    'missing-selector', 'lineage', 'lineage-pointer', 'requirements', 'context', 'resources', 'external-handoff',
    'authority-record', 'authority-manifest', 'authority-member', 'authority-alias', 'missing-predecessor-state',
    'unexpected-predecessor-state'])
def test_superseded_corruption_or_ambiguous_chain_is_never_ignored(enrolled, failure):
    w = enrolled
    chain = superseded_chain(w, missing=failure == 'unexpected-predecessor-state')
    directory, root = chain['directory'], w['state'] / 'al-run-pins'
    def change(path, key, value):
        document = read_json(path);document[key] = value;write_json(path, document)
    def tombstone(key, value):
        for path in (chain['predecessor'], directory / 'predecessor-tombstone.json'):
            change(path, key, value)
    successor_subject = {k: chain['successor'][k] for k in ('repository_identity', 'platform', 'session_id')}
    successor_index = root / 'runs' / (subject_key(successor_subject) + '.json')
    if failure == 'tombstone-field': change(chain['predecessor'], 'ignored', True)
    elif failure in {'predecessor-ref', 'activation-ref'}:
        key = 'predecessor_binding_ref' if failure == 'predecessor-ref' else 'activation_ref'
        reference = dict(read_json(chain['predecessor'])[key], sha256='0' * 64)
        tombstone(key, reference)
    elif failure == 'activation-plan': change(directory / 'activation-plan.json', 'request_id', 'foreign')
    elif failure == 'activation-journal': change(directory / 'activation-journal.json', 'state', 'FENCING')
    elif failure == 'tombstone-copy': change(directory / 'predecessor-tombstone.json', 'run_id', 'ffffffff')
    elif failure == 'request': change(directory / 'request.json', 'reason', 'Different retained request')
    elif failure == 'plan': change(directory / 'plan.json', 'storage', '/foreign')
    elif failure == 'receipt': change(directory / 'receipt.json', 'distribution_identity', 'f' * 64)
    elif failure == 'preparation-journal': change(directory / 'journal.json', 'receipt_ref', {'stored_path': 'foreign', 'sha256': '0' * 64})
    elif failure == 'fence': change(root / 'successor-fences' / (subject_key(successor_subject) + '.json'), 'plan_sha256', '0' * 64)
    elif failure == 'selector': change(chain['selector'], 'epoch_id', 'foreign')
    elif failure == 'selector-ref':
        activation = read_json(directory / 'activation-plan.json')
        activation['selector_ref']['stored_path'] = str(root / 'successor-active/foreign.json')
        write_json(directory / 'activation-plan.json', activation)
        reference = {'stored_path': str(directory / 'activation-plan.json'), 'sha256': digest((directory / 'activation-plan.json').read_bytes())}
        tombstone('activation_ref', reference)
        change(directory / 'activation-journal.json', 'activation_ref', reference)
    elif failure == 'successor-index': change(successor_index, 'publication_transaction', 'f' * 64)
    elif failure == 'successor-state':
        state = read_json(chain['successor_state']);state['runtime_origin']['run_id'] = 'ffffffff';write_json(chain['successor_state'], state)
    elif failure == 'successor-status': change(successor_index, 'status', 'RESERVED')
    elif failure == 'missing-successor': successor_index.unlink()
    elif failure == 'missing-selector': chain['selector'].unlink()
    elif failure == 'lineage': change(directory / 'lineage.json', 'preparation_sha256', '0' * 64)
    elif failure == 'lineage-pointer': change(directory / 'lineage-reference.json', 'stored_path', 'foreign')
    elif failure == 'requirements': change(directory / 'requirements.json', 'requirement_lineage_hash', '0' * 64)
    elif failure == 'context': change(directory / 'context.json', 'repository', '/foreign')
    elif failure == 'resources': change(directory / 'resources.json', 'handoff_ref', {'path': 'foreign', 'sha256': '0' * 64})
    elif failure == 'external-handoff': change(chain['authority'] / 'prepared/handoff.json', 'fixture', 'corrupt')
    elif failure == 'authority-record': change(chain['bridge'] / 'authority.json', 'manifest_sha256', '0' * 64)
    elif failure == 'authority-manifest': change(chain['bridge'] / 'trusted/manifest.json', 'fingerprint', '0' * 64)
    elif failure == 'authority-member':
        path = chain['bridge'] / 'trusted/harness/execution_prepared_authority.py';path.write_bytes(path.read_bytes() + b'\n')
    elif failure == 'authority-alias': os.link(chain['bridge'] / 'authority.json', w['root'] / 'authority-alias')
    elif failure == 'missing-predecessor-state': (w['repo'] / '.codex/al-runtime/old-session.json').unlink()
    else: write_json(w['repo'] / '.codex/al-runtime/old-session.json', chain['prior_state'])
    before = immutable_bytes(root)
    result = update(w)
    assert result.returncode != 0, result.stdout + result.stderr
    assert load_record(w['state'], w['row']['target_id']) == w['row']
    assert immutable_bytes(root) == before


@pytest.mark.parametrize('missing', [False, True])
def test_superseded_reads_never_select_mutating_storage(enrolled, monkeypatch, missing):
    from al.installer import reviewed_runtime_pins as compatibility
    from al.installer import runtime_successor_store as store
    w = enrolled
    chain = superseded_chain(w, missing=missing)
    root = w['state'] / 'al-run-pins'
    before = immutable_bytes(root)
    def forbidden_storage(*args, **kwargs):
        pytest.fail('readonly compatibility must not select mutating successor storage')
    monkeypatch.setattr(store, 'storage', forbidden_storage)
    tombstone = read_json(chain['predecessor'])
    subject = {key: tombstone[key] for key in ('repository_identity', 'platform', 'session_id')}
    _, result, recovery = compatibility._superseded(root, tombstone, subject, compatibility._index(root))
    assert result['classification'] == 'protected-superseded-non-capability' and recovery == missing
    assert immutable_bytes(root) == before


@pytest.mark.parametrize('missing', [False, True])
def test_removed_storage_is_refused_without_recreation(enrolled, missing):
    w = enrolled
    superseded_chain(w, missing=missing)
    root, moved = w['state'] / 'al-run-pins', w['root'] / 'removed-protected-storage'
    before = {str(Path(name).relative_to(root)): value for name, value in immutable_bytes(root).items()}
    # Inject only into a disposable, genuinely bootstrapped coordinator copy.
    # Removal occurs after a reference's safe capture and before its subsequent
    # validation, exactly where the formerly reused reader could create storage.
    path = w['new_source'] / 'installer/reviewed_runtime_pins.py'
    raw = path.read_text()
    needle = '        result = read(expected)\n'
    assert raw.count(needle) == 1
    path.write_text(raw.replace(needle, needle +
                    "        if expected.name == 'predecessor-binding.json':\n" +
                    f'            root.rename(Path({str(moved)!r}))\n'))
    boot = invoke([sys.executable, '-I', '-S', '-B', w['new_source'] / 'alctl.py', 'bootstrap',
                   '--source-root', w['new_source'], '--destination', w['launcher'], '--state-root', w['state']],
                  w['repo'], w['env'])
    assert boot.returncode == 0, boot.stdout + boot.stderr
    result = update(w)
    assert result.returncode != 0, result.stdout + result.stderr
    assert load_record(w['state'], w['row']['target_id']) == w['row']
    assert not root.exists(), 'readonly refusal recreated removed protected storage'
    after = {str(Path(name).relative_to(moved)): value for name, value in immutable_bytes(moved).items()}
    assert after == before
