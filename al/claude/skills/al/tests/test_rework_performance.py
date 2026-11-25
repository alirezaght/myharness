"""Small counter regressions for rework and shared readiness; no wide scans."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import al_artifacts as artifacts
import al_ci_context as ci_context
import al_gate_currency as currency
import al_gate_discovery as discovery
import al_gate_inventory as inventory
import al_implementation as implementation
import al_repository_facts as facts
import al_scope as scope
import al_service_coverage as coverage
import al_store as store
import al_task_attribution as attribution
import al_transitions as transitions


@pytest.mark.parametrize('phase', ['PLANNING', 'IMPLEMENTATION', 'REWORK'])
def test_illegal_rework_phase_refuses_before_full_assessment(monkeypatch, phase):
    import al_new_origin as origin
    state = {'status': 'RUNNING', 'phase': phase, 'classification': 'STANDARD',
             'spec_status': 'SPEC_READY'}
    before = copy.deepcopy(state)
    calls = []
    monkeypatch.setattr(origin, 'require_implementation_ready', lambda *a, **k: calls.append('expensive'))
    with pytest.raises(SystemExit, match='rework is not allowed'):
        transitions.require_transition(state, 'rework', 'REWORK')
    assert calls == [] and state == before


def fake_snapshot(members):
    """A Snapshot stand-in that keeps the class's static membership rule (#25 calls Snapshot.relevant)."""
    real = facts.Snapshot

    class Fake:
        relevant = staticmethod(real.relevant)

        def __new__(cls, *a, **k):
            return SimpleNamespace(gaps=[], members=list(members))
    return Fake


def assessment_probe(monkeypatch):
    counters = {'rows': 0, 'classifications': 0, 'inventory': 0}
    state = {'identity': {'merge_base': 'HEAD'}, 'baseline_head': 'HEAD',
             'gate_manifests': [{}], 'task_attribution': {}}
    manifest = {'rules': [], 'gates': [], 'paths': [], 'units': ['.'],
                'service_coverage': {'semantic_requirements': {'enumeration': {
                    'membership_sha256': facts.membership_digest([])}},
                    # The v2 declaration gate currency compares since #25 (al_gate_currency._current).
                    'projection': {'semantic_sha256': 'synthetic'},
                    'capture': {'implementation_binding': 'synthetic'},
                    'assessment': {'required_bindings': []}}}
    current = {'service_coverage': {'gaps': [], 'required_bindings': []},
               'relevance_projection': {'semantic_sha256': 'synthetic'},
               'capture': {'implementation_binding': 'synthetic'}, 'rules': [], 'units': ['.']}

    def rows(*a, **k):
        counters['rows'] += 1
        return []

    @scope.decision
    def classify(st):
        counters['classifications'] += 1
        return {'discovery_paths': [], 'ambiguous': [], 'limitations': []}

    def current_inventory(st, **kwargs):
        assert kwargs == {'current': True}
        counters['inventory'] += 1
        classify(st)
        return []

    monkeypatch.setattr(implementation, 'require_preflight', lambda *a, **k: [])
    monkeypatch.setattr(implementation, 'binding', lambda st: {'synthetic': 'only'})
    monkeypatch.setattr(scope, '_records', rows)
    monkeypatch.setattr(scope, 'git_bytes', lambda *a, **k: b'')
    monkeypatch.setattr(scope, 'source_rows', lambda *a, **k: [])
    monkeypatch.setattr(attribution, 'classify_changes', classify)
    monkeypatch.setattr(artifacts, 'read_json', lambda *a, **k: copy.deepcopy(manifest))
    monkeypatch.setattr(discovery, 'validate_manifest', lambda *a, **k: None)
    monkeypatch.setattr(ci_context, 'binding', lambda *a, **k: {'current': True, 'profile': {}})
    monkeypatch.setattr(facts, 'Snapshot', fake_snapshot([]))
    monkeypatch.setattr(coverage, 'admission_gaps', current_inventory)
    # The inventory is an answer recorded per capture since #25; its own tests are
    # test_observed_records.py and test_discovery_reuse.py. Here it is a fixed current answer.
    monkeypatch.setattr(inventory, 'inventory_inputs', lambda *a, **k: (_ for _ in ()).throw(ValueError('synthetic')))
    monkeypatch.setattr(inventory, 'inventory', lambda *a, **k: copy.deepcopy(current))
    return state, counters



def test_fingerprint_only_preserves_full_capture_digest_without_payload_io(repo, monkeypatch):
    base = scope.git_bytes('rev-parse', 'HEAD').decode().strip()
    state = {'identity': {'target_oid': base, 'merge_base': base},
             'baseline_head': base, 'preexisting_changes': []}
    target = repo / 'untracked.txt'
    target.write_bytes(b'public synthetic bytes\n')
    diffs, reads = [], []
    original_read = Path.read_bytes

    def read(path):
        if path == target:
            reads.append(1)
        return original_read(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(scope, 'parent_diff', lambda *a, **k: diffs.append(1) or b'public diff')
    digest = scope.candidate_fingerprint(state)
    assert diffs == [] and len(reads) == 1
    full = scope.capture_candidate(state)
    assert digest == full['fingerprint'] and len(reads) == 3 and len(diffs) == 1
    assert full['untracked']['untracked.txt']['encoding'] == 'base64'


def run_inventory_probe(repo, monkeypatch, *, extra=(), opaque=()):
    members = ['app/', 'app/sub/', 'app/sub/deeper/', 'app/sub/deeper/code.py', *extra]
    payload = {'enumeration': {'gaps': []}, 'scan_inputs': [], 'rule_sources': [],
               'checks': {'bindings': [], 'obligations': [], 'gaps': []},
               'units': [{'id': 'app', 'path': 'app'}], 'scan': {'stable': True},
               'unsupported_boundaries': []}
    counters = {'initial_units': 0, 'final_units': 0, 'walks': [], 'visited': []}
    original_walk = os.walk

    def walk(path, *args, **kwargs):
        counters['walks'].append(Path(path).relative_to(repo).as_posix())
        for row in original_walk(path, *args, **kwargs):
            counters['visited'].append(Path(row[0]).relative_to(repo).as_posix())
            yield row

    def classify(path, boundaries, semantic=None):
        counters['initial_units' if semantic is None else 'final_units'] += 1
        return 'app'

    monkeypatch.setattr(inventory.CC, 'profile', lambda *a, **k: {})
    monkeypatch.setattr(attribution, 'classify_changes', lambda *a, **k: {'discovery_paths': []})
    rows = [{'path': p, 'child': {'state': 'uninitialized'}, 'worktree': {'type': 'gitlink'}} for p in opaque]
    monkeypatch.setattr(scope, 'source_rows', lambda *a, **k: rows)
    monkeypatch.setattr(facts, 'Snapshot', fake_snapshot(members))
    monkeypatch.setattr(inventory, 'classify_unit', classify)
    monkeypatch.setattr(inventory.al_affected_services, 'select', lambda *a, **k: copy.deepcopy(payload))
    # Service verification bindings come from the real snapshot facts since #25; this probe
    # measures only the instruction frontier, so no service is selected.
    monkeypatch.setattr(inventory.Coverage, 'assess', lambda *a, **k: {
        'selected_services': [], 'outcome': 'identified'})
    monkeypatch.setattr(inventory.CI, 'inventory', lambda *a, **k: {
        'obligations': [], 'dependencies': []})
    monkeypatch.setattr(inventory.Verification, 'conventional_verification', lambda *a, **k: {
        'satisfied': False})
    monkeypatch.setattr(os, 'walk', walk)
    result = inventory.inventory({'gate_manifests': [], 'identity': {'merge_base': 'HEAD'}}, ['app'])
    return result, counters


def make_instruction_fixture(repo):
    for path in ['app/sub/deeper/code.py', 'app/AGENTS.md', 'app/sub/CLAUDE.md',
                 'app/node_modules/vendor/AGENTS.md', 'app/dist/CLAUDE.md']:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('public synthetic fixture\n')


def test_inventory_walks_one_frontier_and_keeps_generated_instructions(repo, monkeypatch):
    make_instruction_fixture(repo)
    result, counters = run_inventory_probe(repo, monkeypatch)
    # Since #25 instruction sources come from the snapshot membership, not a filesystem walk.
    assert counters['walks'] == []
    assert counters['initial_units'] == 0
    # Rule sources follow the snapshot membership (#25): untracked files under the snapshot's
    # excluded directories (node_modules, dist, ...) are not repository instructions any more.
    names = {r['path'] for r in result['rules']}
    assert {'app/AGENTS.md', 'app/sub/CLAUDE.md'} <= names
    assert not {'app/node_modules/vendor/AGENTS.md', 'app/dist/CLAUDE.md'} & names


def test_nonwriter_command_progress_has_fixed_label_and_no_stdout(capsys):
    import al_writer_progress as progress
    with progress.command_progress('rework'):
        progress.report('state-load', force=True)
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'AL · rework ·' in captured.err
    assert 'loading guarded run state' in captured.err


@pytest.mark.parametrize('problem', ['status', 'specification', 'classification', 'complex-edge', 'active-operation'])
def test_cheap_transition_failures_preserve_state_and_skip_assessment(monkeypatch, problem):
    import al_new_origin as origin
    state = {'status': 'RUNNING', 'phase': 'FINALIZATION', 'classification': 'STANDARD',
             'spec_status': 'SPEC_READY', 'writers': {}, 'evidence': {'entries': []}}
    command, destination = 'rework', 'REWORK'
    if problem == 'status':
        state['status'] = 'INTERRUPTED'
    elif problem == 'specification':
        state['spec_status'] = 'SPEC_BLOCKED'
    elif problem == 'classification':
        state['classification'] = 'unknown'
    elif problem == 'complex-edge':
        state.update(phase='IMPLEMENTATION', classification='COMPLEX')
        command, destination = 'phase', 'VERIFICATION'
    else:
        state['phase'] = 'VERIFICATION'
        state['writers']['active'] = {'id': 'active', 'state': 'ACTIVE'}
    before = copy.deepcopy(state)
    calls = []
    monkeypatch.setattr(origin, 'require_implementation_ready', lambda *a, **k: calls.append('expensive'))
    with pytest.raises(SystemExit):
        transitions.require_transition(state, command, destination)
    assert calls == [] and state == before


def test_valid_transition_keeps_recovery_and_current_authority_order(monkeypatch):
    import al_new_origin as origin
    import al_rework_recovery_contract as recovery
    state = {'status': 'RUNNING', 'phase': 'REWORK_REASSESSMENT', 'classification': 'STANDARD',
             'spec_status': 'SPEC_READY', 'writers': {}, 'evidence': {'entries': []}}
    calls = []

    def guard(st, **kwargs):
        assert kwargs == {'implementation': True}
        calls.append('recovery')
        st['phase'] = 'FINALIZATION'

    def ready(st):
        calls.append('current-authority')
        raise SystemExit('original authority refused')

    monkeypatch.setattr(recovery, 'guard', guard)
    monkeypatch.setattr(origin, 'require_implementation_ready', ready)
    with pytest.raises(SystemExit, match='original authority refused'):
        transitions.require_transition(state, 'rework', 'REWORK')
    assert calls == ['recovery', 'current-authority']


def legacy_obligations(st):
    """The ordinary 0.1.8 assessment sequence, without a shared outer decision."""
    obligations = currency.check(st)['reasons'] + coverage.admission_gaps(st, current=True)
    result = attribution.classify_changes(st)
    obligations += ['attribution analysis incomplete: ' + p for p in result['ambiguous']]
    obligations += ['attribution analysis incomplete: ' + str(v) for v in result['limitations']]
    return sorted(set(obligations))



def test_unavailable_readiness_observation_never_grants_cached_authority(monkeypatch):
    state, _ = assessment_probe(monkeypatch)
    before = copy.deepcopy(state)

    def unavailable(*a, **k):
        raise OSError('unavailable synthetic source')

    @scope.decision
    def guarded_classify(st):
        scope.records('HEAD')  # Original attribution still requires actual source.
        return {'discovery_paths': [], 'ambiguous': [], 'limitations': []}

    monkeypatch.setattr(scope, '_records', unavailable)
    monkeypatch.setattr(attribution, 'classify_changes', guarded_classify)
    with pytest.raises(OSError, match='unavailable synthetic source'):
        implementation.require_ready(state)
    assert state == before
    with pytest.raises(OSError, match='unavailable synthetic source'):
        legacy_obligations(before)


def test_readiness_preserves_existing_ambient_observation_semantics(monkeypatch):
    from al_analysis_cache import CURRENT, ObservationContext
    state, counters = assessment_probe(monkeypatch)
    with ObservationContext({'not-a-readiness-authority': True}):
        implementation.require_ready(state)
    assert counters['rows'] == 0 and CURRENT.get() is None


def test_unavailable_outer_view_keeps_legacy_unattributed_debt_path(monkeypatch):
    state = {'identity': {'merge_base': 'HEAD'}, 'baseline_head': 'HEAD', 'gate_manifests': []}
    monkeypatch.setattr(implementation, 'require_preflight', lambda *a, **k: [])
    monkeypatch.setattr(implementation, 'binding', lambda st: {'synthetic': 'only'})

    def unavailable(*a, **k):
        raise OSError('no outer snapshot')

    monkeypatch.setattr(scope, '_records', unavailable)
    actual = implementation.require_ready(state)
    assert actual['obligations'] == ['repository gate manifest missing']



def test_readonly_assessment_does_not_publish_helper_state_mutation(monkeypatch):
    state, _ = assessment_probe(monkeypatch)
    before = copy.deepcopy(state)

    def mutating(st, **kwargs):
        st['unexpected-mutation'] = True
        return []

    monkeypatch.setattr(coverage, 'admission_gaps', mutating)
    with pytest.raises(SystemExit, match='state changed during read-only'):
        implementation.require_ready(state)
    assert state == before


def test_preflight_continuity_and_existing_debt_survive_pure_assessment(monkeypatch):
    state, _ = assessment_probe(monkeypatch)
    state['implementation_readiness'] = {'obligations': ['old pending proof']}

    def preflight(st, **kwargs):
        st['authorized-continuity'] = ['retained']
        return []

    monkeypatch.setattr(implementation, 'require_preflight', preflight)
    implementation.require_ready(state)
    assert state['authorized-continuity'] == ['retained']
    assert state['implementation_readiness']['obligations'] == ['old pending proof']


def test_actual_corrupt_task_artifact_still_refuses_before_readiness_publication(cli, tmp_path):
    from evidence_helpers import start_implementation
    start_implementation(cli, tmp_path)
    state = cli.state()
    before = copy.deepcopy(state)
    original = Path(state['task_package']['stored_path']).read_bytes()
    Path(state['task_package']['stored_path']).write_bytes(original + b'corrupt')
    with pytest.raises(SystemExit):
        implementation.require_ready(state)
    assert state == before


@pytest.mark.parametrize('mutation', ['clean', 'tracked', 'untracked', 'staged', 'deleted', 'executable', 'symlink'])
def test_fingerprint_digest_matches_existing_full_capture_across_source_states(repo, mutation):
    import subprocess
    base = scope.git_bytes('rev-parse', 'HEAD').decode().strip()
    state = {'identity': {'target_oid': base, 'merge_base': base},
             'baseline_head': base, 'preexisting_changes': ['public historic path']}
    if mutation == 'tracked':
        (repo / 'svc/app.py').write_text('changed=True\n')
    elif mutation in {'untracked', 'staged'}:
        (repo / 'new.py').write_text('new=True\n')
        if mutation == 'staged':
            subprocess.run(['git', 'add', 'new.py'], cwd=repo, check=True)
    elif mutation == 'deleted':
        (repo / 'svc/app.py').unlink()
    elif mutation == 'executable':
        (repo / 'svc/app.py').chmod(0o755)
    elif mutation == 'symlink':
        (repo / 'public-link').symlink_to('svc/app.py')
    assert scope.candidate_fingerprint(state) == scope.capture_candidate(state)['fingerprint']


def test_fingerprint_target_errors_and_alias_guard_are_preserved(repo):
    base = scope.git_bytes('rev-parse', 'HEAD').decode().strip()
    state = {'identity': {'merge_base': base}, 'baseline_head': base}
    with pytest.raises(KeyError):
        scope.candidate_fingerprint(state)
    state['identity']['target_oid'] = base
    (repo / 'svc').rename(repo / 'moved-service')
    (repo / 'svc').symlink_to('moved-service', target_is_directory=True)
    with pytest.raises(SystemExit, match='symlink'):
        scope.candidate_fingerprint(state)


def test_fingerprint_preserves_original_canonical_encoding(repo, monkeypatch):
    rows = [{'path': 'public.txt', 'index': None, 'worktree': {'type': 'deleted'}}]
    state = {'identity': {'target_oid': 'target'}, 'baseline_head': 'base'}
    monkeypatch.setattr(scope, 'records', lambda base: copy.deepcopy(rows))
    expected = hashlib.sha256(json.dumps({'base': 'base', 'target': 'target',
        'files': rows}, sort_keys=True).encode()).hexdigest()
    assert scope.candidate_fingerprint(state) == expected


def test_candidate_profile_measures_work_not_only_lookup_key(monkeypatch):
    from collections import defaultdict
    import al_profile as profile
    clock = {'now': 0.0}
    monkeypatch.setenv('AL_PROFILE', '1')
    monkeypatch.setattr(profile, 'TOTALS', defaultdict(float))
    monkeypatch.setattr(profile, 'COUNTS', defaultdict(int))
    monkeypatch.setattr(profile.time, 'monotonic', lambda: clock['now'])

    def rows(base):
        clock['now'] += 7.0
        return []

    def diff(*args, **kwargs):
        clock['now'] += 3.0
        return b''

    monkeypatch.setattr(scope, 'records', rows)
    monkeypatch.setattr(scope, 'parent_diff', diff)
    state = {'identity': {'target_oid': 'target'}, 'baseline_head': 'base'}
    scope.candidate_fingerprint(state)
    assert profile.report() == {'seconds': {'candidate-hashing': 7.0},
                                'calls': {'candidate-hashing': 1}}
    scope.capture_candidate(state)
    assert profile.report() == {'seconds': {'candidate-hashing': 17.0},
                                'calls': {'candidate-hashing': 2}}


def test_fingerprint_preserves_gitlink_pointer_changes(repo):
    import subprocess
    base = scope.git_bytes('rev-parse', 'HEAD').decode().strip()
    state = {'identity': {'target_oid': base, 'merge_base': base}, 'baseline_head': base}
    subprocess.run(['git', 'update-index', '--add', '--cacheinfo',
        '160000,'+base+',public-child'], cwd=repo, check=True)
    before = scope.candidate_fingerprint(state)
    assert before == scope.capture_candidate(state)['fingerprint']
    subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--allow-empty', '-qm', 'public fixture pointer'], cwd=repo, check=True)
    current = scope.git_bytes('rev-parse', 'HEAD').decode().strip()
    subprocess.run(['git', 'update-index', '--cacheinfo',
        '160000,'+current+',public-child'], cwd=repo, check=True)
    after = scope.candidate_fingerprint(state)
    assert after == scope.capture_candidate(state)['fingerprint'] and after != before


def test_instruction_frontier_preserves_explicit_symlink_root_and_opaque_boundary(repo, monkeypatch):
    make_instruction_fixture(repo)
    (repo / 'app/alias').symlink_to('sub', target_is_directory=True)
    target = repo / 'app/opaque/AGENTS.md'
    target.parent.mkdir()
    target.write_text('public opaque canary\n')
    result, counters = run_inventory_probe(repo, monkeypatch,
        extra=['app/alias/', 'app/alias/deeper/', 'app/opaque/'], opaque=['app/opaque'])
    assert counters['walks'] == []
    names = {r['path'] for r in result['rules']}
    # The snapshot records a directory symlink as a link (#25); its target's instructions are sources.
    assert 'app/sub/CLAUDE.md' in names and 'app/alias/CLAUDE.md' not in names
    assert 'app/opaque/AGENTS.md' not in names


def test_frontier_keeps_roots_hidden_by_parent_git_pruning(repo):
    target = repo / 'app/.git/declared'
    target.mkdir(parents=True)
    assert inventory._instruction_frontier(['app', 'app/.git/declared']) == ['app', 'app/.git/declared']


def test_frontier_never_caches_new_instruction_membership(repo, monkeypatch):
    make_instruction_fixture(repo)
    with monkeypatch.context() as first_patch:
        before, _ = run_inventory_probe(repo, first_patch)
    target = repo / 'app/new-root/AGENTS.md'
    target.parent.mkdir()
    target.write_text('new public instruction\n')
    with monkeypatch.context() as second_patch:
        after, counters = run_inventory_probe(repo, second_patch)
    assert 'app/new-root/AGENTS.md' not in {r['path'] for r in before['rules']}
    assert 'app/new-root/AGENTS.md' in {r['path'] for r in after['rules']}
    assert counters['walks'] == []


@pytest.mark.parametrize('command,action', [
    ('show', None), ('inspect', None), ('integration', 'events'), ('artifact', 'show'),
    ('gates', 'status'), ('evidence', 'status'), ('writer', 'handoff'),
    ('publication', 'packet'), ('acceptance', 'status'), ('secret-user-path', None)])
def test_readonly_and_unknown_commands_stay_silent(capsys, command, action):
    import al_writer_progress as progress
    with progress.command_progress(command, action):
        progress.report('state-load', force=True)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('stream_kind', ['absent', 'broken', 'closed'])
def test_nonwriter_progress_io_failure_is_nonfatal(monkeypatch, stream_kind):
    import al_writer_progress as progress

    class Broken(io.StringIO):
        def write(self, value):
            raise OSError('broken diagnostics')

    stream = None if stream_kind == 'absent' else Broken() if stream_kind == 'broken' else io.StringIO()
    if stream_kind == 'closed':
        stream.close()
    monkeypatch.setattr(progress.sys, 'stderr', stream)
    with progress.command_progress('rework'):
        progress.report('state-load', force=True)


def test_public_rework_wrong_phase_is_fast_and_preserves_state(cli, monkeypatch):
    from test_rework import without_repro
    assert cli('start', '--spec', 'Reject an inapplicable pre-review return before any repository assessment.')[0] == 0
    before = store.state_path(cli.state()['session_id']).read_bytes()

    def expensive(*a, **k):
        pytest.fail('illegal planning rework must not assess repository readiness')

    monkeypatch.setattr(implementation, 'require_ready', expensive)
    flags = without_repro()
    flags[flags.index('--cause-demonstrated')+1] = 'no'
    result, output, diagnostics = cli('rework', '--source', 'core-flaw', *flags)
    assert result != 0 and 'rework is not allowed from PLANNING' in str(result)
    assert 'already-authorized scoped implementation' in str(result)
    assert output == '' and 'AL · rework ·' in diagnostics
    assert diagnostics.index('acquiring run admission lock') < diagnostics.index('loading guarded run state')
    assert store.state_path(cli.state()['session_id']).read_bytes() == before


def test_dispatch_progress_precedes_real_lock_and_preload_without_changing_stdout(repo, monkeypatch, capsys):
    import al_state
    import al_new_origin
    import al_writer_progress as progress
    stream = io.StringIO()
    original_flock = store.fcntl.flock
    observed = []

    def flock(fd, operation):
        if operation & store.fcntl.LOCK_EX:  # blocking or non-blocking exclusive acquisition
            observed.append('acquiring run admission lock' in stream.getvalue())
        return original_flock(fd, operation)

    monkeypatch.setattr(store.fcntl, 'flock', flock)
    monkeypatch.setattr(progress.sys, 'stderr', stream)
    monkeypatch.setattr(al_new_origin, 'require_dispatch', lambda: None)
    monkeypatch.setattr(al_state, 'session_id', lambda *a, **k: 'public-counter')

    def preload(sid):
        assert 'run admission lock acquired' in stream.getvalue()
        return {'status': 'RUNNING'}

    monkeypatch.setattr(al_state, 'load', preload)
    args = SimpleNamespace(cmd='rework', action=None, session='public-counter',
        recovery_file=None, fn=lambda *a: print('{"unchanged": true}'))
    with progress.command_progress('rework'):
        al_state._dispatch(args, None)
    assert observed == [True]
    assert capsys.readouterr().out == '{"unchanged": true}\n'
    assert 'public-counter' not in stream.getvalue() and str(repo) not in stream.getvalue()
