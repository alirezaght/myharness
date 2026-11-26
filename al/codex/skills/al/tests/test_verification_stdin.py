"""Managed verification reads EOF; unrelated launches and supplied input keep their meaning."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest
import al_action_cache as Cache
import al_gate_process as Gate
import al_harness_process as Processes
import al_ledger as Ledger
import al_verification_policy as Policy
from evidence_helpers import entries, ok_cmd
from launcher_context import AL_ROOT


EOF_CHECK = "import os; assert not os.isatty(0); assert os.read(0,1)==b''; print('1 passed')"


@pytest.fixture(autouse=True)
def private_verification_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOME', str(tmp_path/'private-harness-home'))
    monkeypatch.setenv('AL_ACTION_CACHE_DIR', str(tmp_path/'private-action-cache'))


@contextmanager
def caller_input():
    with tempfile.TemporaryFile() as source:
        source.write(b'caller-input'); source.seek(0)
        saved = os.dup(0)
        try:
            os.dup2(source.fileno(), 0)
            yield lambda: os.read(0, 4096)
        finally:
            try: os.dup2(saved, 0)
            finally: os.close(saved)


@pytest.mark.parametrize('managed', [False, True])
def test_public_evidence_reads_eof_without_consuming_caller_input(
        cli, repo, run_state, state_file, launcher, managed):
    run_state('implemented')
    command = [sys.executable, '-I', '-c', EOF_CHECK]
    area = 'managed-stdin' if managed else 'historical-unmanaged-stdin'
    assert cli('evidence', 'plan', '--area', area, '--kind', 'check', '--scope', 'svc', '--', *command)[0] == 0
    if not managed:
        import al_store
        state = cli.state()
        def historical_declaration():
            current = al_store.load(state['session_id'])
            current['evidence']['plan'][area].pop('storage')
            al_store.save(state['session_id'], current)
        # Construct only the supported historical declaration absence, never a result or pin.
        launcher.run(repo, state['session_id'], False, historical_declaration)
    with caller_input() as remaining:
        result = cli('evidence', 'run', '--area', area)
        untouched = remaining()
    assert result[0] == 0, result
    assert untouched == b'caller-input'
    entry = next(e for e in entries(state_file) if e['area'] == area)
    assert entry['status'] == 'PASS' and entry['exit'] == 0
    assert '1 passed' in Path(entry['log']).read_text()
    assert ('storage_allocation' in entry) is managed


def test_public_gate_uses_eof_and_preserves_outer_input(cli, repo, tmp_path, run_state):
    from batch13_helpers import start, manifest
    run_state('stdin-gate-before-manifest', lambda: start(cli, repo))
    manifest(cli, tmp_path, [sys.executable, '-I', '-c', EOF_CHECK])
    with caller_input() as remaining:
        result = cli('gates', 'run', '--stage', 'base', '--id', 'boundary')
        untouched = remaining()
    assert result[0] == 0, result
    assert untouched == b'caller-input'
    executed = [e for e in cli.state()['evidence']['entries'] if e['area'] == 'gate-base-boundary']
    assert len(executed) == 1 and executed[0]['status'] == 'PASS'
    assert '1 passed' in Path(executed[0]['log']).read_text()


@pytest.mark.parametrize('mode', ['pipe', 'file'])
def test_explicit_gate_input_keeps_supplied_bytes(tmp_path, mode):
    log = tmp_path/'gate.log'
    with Gate.Owner(log) as owner, tempfile.TemporaryFile() as source:
        source.write(b'explicit-input'); source.seek(0)
        try:
            child = owner.spawn([sys.executable, '-I', '-c',
                                 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'],
                                cwd=tmp_path, stdin=subprocess.PIPE if mode == 'pipe' else source,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output = child.communicate(input=b'explicit-input' if mode == 'pipe' else None, timeout=5)
            assert output == (b'explicit-input', b'') and child.returncode == 0
        finally:
            try:
                if owner.proc is not None: Gate.stop(owner.proc)
            finally:
                if owner.proc is not None:
                    for stream in (owner.proc.stdin, owner.proc.stdout, owner.proc.stderr):
                        if stream is not None and not stream.closed: stream.close()
        assert owner.completion()['complete']


def test_generic_popen_still_inherits_input_and_owned_spawn_forwards_explicit_file(tmp_path):
    from installer.owned_process import OwnedProcess
    # Fixed child family: the inner process neither forks nor detaches, and has its own finally.
    body = '''import sys,subprocess
sys.path.insert(0,sys.argv[1])
import al_harness_process as processes
child=processes.popen([sys.executable,'-I','-c','import os; os.write(1,os.read(0,5))'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
try:
 out,err=child.communicate(timeout=5)
 assert child.returncode==0 and out==b'input' and err==b''
 print('generic inheritance preserved')
finally:
 try:
  if child.poll() is None:child.kill()
 finally:
  try:child.wait(timeout=5)
  finally:
   for stream in (child.stdin,child.stdout,child.stderr):
    if stream is not None and not stream.closed:stream.close()
'''
    owner = OwnedProcess(tmp_path/'generic-owner', 'generic-input-control')
    log = tmp_path/'generic.log'
    with tempfile.TemporaryFile() as source, log.open('wb') as output:
        source.write(b'input'); source.seek(0)
        try:
            child = owner.spawn([sys.executable, '-I', '-c', body, str(AL_ROOT/'codex/skills/al/scripts')],
                                cwd=tmp_path, env=dict(os.environ), output=output, stdin=source)
            assert child.wait(timeout=10) == 0
        finally:
            proof = owner.cleanup()
            assert proof['complete'], proof
    assert 'generic inheritance preserved' in log.read_text()


def test_verification_dispatch_cannot_fall_back_without_harness_channel(monkeypatch):
    monkeypatch.setenv('HARNESS_CHECK_CONTEXT', 'controlled-missing-channel')
    monkeypatch.delenv('HARNESS_CHECK_FD', raising=False)
    native = []
    def forbidden(*args, **kwargs):
        native.append((args, kwargs)); raise AssertionError('native fallback')
    monkeypatch.setattr(Processes.subprocess, 'Popen', forbidden)
    for dispatch in (Processes.popen, Processes.verification_popen):
        with pytest.raises(OSError, match='dispatch channel unavailable'):
            dispatch(['fixed-check'])
    assert native == []


@pytest.mark.parametrize('mode', ['evidence', 'gate'])
def test_old_cache_without_execution_policy_misses_and_new_policy_hits(repo, tmp_path, mode):
    import al_evidence_storage
    command = ok_cmd('1 passed')
    if mode == 'evidence':
        plan, reason = Cache.evidence_plan({}, {'area':'stdin', 'kind':'test', 'argv':command,
            'cwd':'.', 'scope':['svc'], 'sources':[], 'env':[],
            'storage':al_evidence_storage.default_declaration()})
    else:
        gate = {'id':'stdin', 'argv':command, 'cwd':'.', 'scope':['svc'], 'sources':[],
                'env_names':[], 'category':'contracts', 'execution_kind':'local'}
        plan, reason = Cache.gate_plan({}, {'gates':[]}, gate, 'candidate')
    assert plan is not None, reason
    legacy_key = copy.deepcopy(plan.key)
    legacy_key.pop('execution_policy')
    legacy = Cache.Plan(plan.mode, legacy_key, plan.inputs, plan._tree_of)
    # Controlled cache records exercise real immutable store lookup, not claimed AL evidence.
    log = tmp_path/'cache.log'; log.write_text('controlled fixture output\n')
    row = {'id':'fixture', 'status':'PASS', 'exit':0, 'counts':{}, 'note':'',
           'log_sha256':Cache._log_sha(log)}
    origin = {'run':'controlled-fixture', 'entry':'fixture'}
    assert Cache.save(legacy, row, log, origin)
    assert Cache.lookup(legacy)['status'] == 'PASS'
    assert Cache.lookup(plan) is None
    assert Cache.save(plan, row, log, origin)
    assert Cache.lookup(plan)['status'] == 'PASS'
    assert Cache.lookup(legacy)['key'] == legacy_key


def _legacy_environment_digest(h, names):
    from al_environment_observation import DEFAULT_VERIFICATION_NAMES, value_digest
    for name in sorted(set(names or ()) | DEFAULT_VERIFICATION_NAMES):
        h.update(f'\0env:{name}={value_digest(name)}'.encode())
    return h.hexdigest()[:16]


def test_prior_policy_pass_and_preflight_stale_without_cache_and_rerun(
        cli, run_state, state_file, monkeypatch, tmp_path):
    run_state('implemented')
    monkeypatch.setenv('AL_ACTION_CACHE', '0')
    marker = tmp_path/'executions'
    command = [sys.executable, '-I', '-c',
               f"open({str(marker)!r},'a').write('x'); " + EOF_CHECK]
    assert cli('evidence', 'plan', '--area', 'stdin-policy', '--scope', 'svc', '--', *command)[0] == 0
    # Genuine executions, with only the historical pre-policy fingerprint algorithm retained.
    # This does not import a pin, rewrite an entry, or assert historical stdin behavior.
    with monkeypatch.context() as patch:
        patch.setattr(Ledger, '_env_digest', _legacy_environment_digest)
        assert cli('evidence', 'run', '--area', 'stdin-policy')[0] == 0
        assert cli('preflight', '--name', 'stdin-preflight', '--check', 'cmd:git')[0] == 0
        old = copy.deepcopy([e for e in entries(state_file) if e['area'] in {'stdin-policy','stdin-preflight'}])
        assert len(old) == 2 and all(Ledger.assess(e)[0] == 'VALID' for e in old)
    assert all(Ledger.assess(e)[0] == 'STALE' for e in old)
    assert cli('evidence', 'require', '--areas', 'stdin-policy,stdin-preflight')[0] == 13
    assert cli('evidence', 'run', '--area', 'stdin-policy')[0] == 0
    current = [e for e in entries(state_file) if e['area'] == 'stdin-policy'][-1]
    assert marker.read_text() == 'xx' and current['id'] != old[0]['id']
    assert current['status'] == 'PASS' and not current.get('action_cache')
    assert all(next(e for e in entries(state_file) if e['id'] == row['id']) == row for row in old)


@pytest.mark.parametrize('scope', [[], ['svc']])
def test_execution_policy_changes_fingerprint_inside_one_observation(repo, monkeypatch, scope):
    from al_analysis_cache import ObservationContext
    before = Policy.execution_policy()
    with ObservationContext({'test':'bounded execution policy observation'}):
        first = Ledger.scope_fingerprint(scope)
        assert first == Ledger.scope_fingerprint(scope)
        monkeypatch.setattr(Policy, 'execution_policy', lambda: {**before, 'version':before['version']+1})
        second = Ledger.scope_fingerprint(scope)
        assert second != first and second == Ledger.scope_fingerprint(scope)
    assert Ledger._env_digest(hashlib.sha256(), []) != _legacy_environment_digest(hashlib.sha256(), [])


def test_real_recipe_children_receive_eof_before_the_trusted_runner(
        cli, repo, run_state, tmp_path, monkeypatch):
    from test_evidence_recipe import bank, start
    from installer import verification_harness
    def build():
        bank(repo, mixed=False); start(cli)
    run_state('stdin-recipe-planned', build)
    materialize = verification_harness.materialize_child
    marker = tmp_path/'recipe-eof'
    def observed(*args, **kwargs):
        original = materialize(*args, **kwargs)
        probe = ("import os,sys; assert not os.isatty(0); assert os.read(0,1)==b''; "
                 + f"open({str(marker)!r},'a').write('x'); "
                 + "os.execv(sys.argv[1],sys.argv[1:])")
        return [sys.executable, '-I', '-c', probe, *original]
    monkeypatch.setattr(verification_harness, 'materialize_child', observed)
    with caller_input() as remaining:
        result = cli('evidence', 'run', '--area', 'harness-recipe')
        untouched = remaining()
    assert result[0] == 0, result
    state = cli.state()
    parent = next(e for e in state['evidence']['entries'] if e.get('recipe'))
    assert parent['status'] == 'PASS' and parent['counts'].get('passed', 0) == 1
    assert not any(parent['counts'].get(key, 0) for key in ('failed', 'error', 'skipped'))
    children = [e for e in state['evidence']['entries'] if e.get('recipe_assignment')]
    assert len(children) == 2 and marker.read_text() == 'x'*len(children)
    assert untouched == b'caller-input'
    assert all(e['status'] == 'PASS' and e['storage_settlement']['owned_group_settled'] for e in children)
