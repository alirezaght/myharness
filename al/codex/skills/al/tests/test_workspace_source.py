"""Real public approval remains required by the isolated installer query."""
import json
from pathlib import Path
import sys
import subprocess
import os
import shlex

import pytest
import al_store

PLATFORM = 'codex'
TRUSTED = Path(__file__).resolve().parents[5]


def canonical_source(repository, *, session, run, platform):
    bridge = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
              "from al.installer.terminal_source import terminal_source;"
              "print(json.dumps(terminal_source(sys.argv[2],session=sys.argv[3],run=sys.argv[4],platform=sys.argv[5])))")
    result = subprocess.run([sys.executable, '-I', '-B', '-c', bridge, str(TRUSTED),
                             str(repository), session, run, platform],
                            cwd=repository, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr[-4000:])
    return json.loads(result.stdout)


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def terminal_run(repo, tmp_path, monkeypatch):
    """An approved pinned run created through the real launcher admission sequence.

    terminal_fixture.py binds runtime resources to the run's pin in its own process and
    pins into the installed validator's state root, where `source-proof` finds it."""
    runtime = repo / ('.' + PLATFORM) / 'al-runtime'
    monkeypatch.setattr(al_store, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(al_store, 'CURRENT', runtime / 'current')
    monkeypatch.setenv('AL_RUNTIME_DIR', str(runtime))
    # Runtime membership is excluded by the ordinary repository ignore rules.
    with (repo / '.git/info/exclude').open('a') as output:
        output.write('\n.' + PLATFORM + '/al-runtime/\n')
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(TRUSTED / 'al/tests/terminal_fixture.py'),
                             str(TRUSTED / 'al'), PLATFORM, str(repo), str(tmp_path / 'artifacts'),
                             str(tmp_path / 'installer-state')],
                            cwd=repo, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-1000:]
    return json.loads(Path(json.loads(result.stdout)['state']).read_bytes())


def installed(repo, tmp_path):
    launcher = tmp_path / 'installed-bin/alctl'
    boot = subprocess.run([sys.executable, '-I', '-B', str(TRUSTED / 'al/alctl.py'), 'bootstrap',
                           '--source-root', str(TRUSTED / 'al'), '--destination', str(launcher),
                           '--state-root', str(tmp_path / 'installer-state')],
                          cwd=repo, capture_output=True, text=True, timeout=120)
    assert boot.returncode == 0, boot.stderr[-2000:] + boot.stdout[-2000:]
    return launcher, json.loads(boot.stdout)


def source_proof(launcher, repository, state, run=None, env=None):
    result = subprocess.run([str(launcher), 'source-proof', '--repository', str(repository),
                             '--session', state['session_id'], '--run', run or state['run_id'], '--platform', PLATFORM],
                            cwd=repository, capture_output=True, text=True, timeout=120, env=env)
    if result.returncode:
        raise ValueError(result.stderr[-4000:] + result.stdout[-2000:])
    return json.loads(result.stdout)


def test_actual_approved_source_uses_trusted_cli_without_runtime_or_index_writes(repo, tmp_path, monkeypatch):
    state = terminal_run(repo, tmp_path, monkeypatch)
    launcher, _ = installed(repo, tmp_path)
    # A pinned run is validated only by its installed launcher, never a canonical checkout.
    with pytest.raises(ValueError, match='launcher'):
        canonical_source(repo, session=state['session_id'], run=state['run_id'], platform=PLATFORM)
    # Target-selected Python modules may never supply the validation logic.
    hostile = repo / 'al_store.py'
    hostile.write_text("raise RuntimeError('target module executed')\n")
    # A new unapproved source file must fail rather than shadow the trusted module.
    before = snapshot(al_store.RUNTIME_DIR)
    index = (repo / '.git/index').read_bytes()
    with pytest.raises(ValueError, match='approval'):
        source_proof(launcher, repo, state)
    hostile.unlink()
    proof = source_proof(launcher, repo, state)
    assert proof['source']['fingerprint'] == state['approval']['fingerprint']
    assert proof['approval']['task_hash'] == state['approval']['task_hash']
    alias = tmp_path/'repository-alias';alias.symlink_to(repo,target_is_directory=True)
    alias_proof = source_proof(launcher, alias, state)
    assert alias_proof['source']==proof['source'] and alias_proof['repository']==str(repo)
    assert proof['schema_version'] == 2 and proof['validator']['origin_digest']
    assert proof['validator']['loaded_modules']['al_store']['origin'].endswith('/scripts/al_store.py')
    assert 'diff' not in proof['source'] and 'untracked' not in proof['source']
    assert snapshot(al_store.RUNTIME_DIR) == before and (repo / '.git/index').read_bytes() == index


def test_foreign_run_and_nonterminal_or_changed_approval_refuse_atomically(repo, tmp_path, monkeypatch):
    state = terminal_run(repo, tmp_path, monkeypatch)
    launcher, _ = installed(repo, tmp_path)
    assert source_proof(launcher, repo, state)['approval']['task_hash'] == state['approval']['task_hash']
    path = al_store.state_path(state['session_id'])
    original = path.read_bytes()
    for change in ('run', 'nonterminal', 'approval'):
        value = json.loads(original)
        if change == 'nonterminal':value.update(status='RUNNING', phase='REVIEW')
        if change == 'approval':value['approval']['task_hash'] = '0' * 64
        path.write_text(json.dumps(value))
        before = snapshot(al_store.RUNTIME_DIR)
        with pytest.raises(ValueError):
            source_proof(launcher, repo, state, run='f' * 8 if change == 'run' else None)
        assert snapshot(al_store.RUNTIME_DIR) == before
    path.write_bytes(original)


def test_installed_snapshot_validates_original_architecture_binding_without_rewriting(repo, tmp_path, monkeypatch):
    state = terminal_run(repo, tmp_path, monkeypatch)
    launcher, receipt = installed(repo, tmp_path)
    before = snapshot(al_store.RUNTIME_DIR)
    index = (repo/'.git/index').read_bytes()
    # The installed CLI's preamble owns the protected origin, not the query.
    proof = source_proof(launcher, repo, state)
    (tmp_path/'terminal-source-positive.json').write_text(json.dumps({'installed':proof,'bootstrap':receipt},indent=2))
    assert proof['source']['fingerprint'] == state['approval']['fingerprint']
    assert proof['validator']['origin_digest'] == receipt['validator_binding']['origin_digest']
    assert proof['validator']['snapshot_identity'] == receipt['validator_binding']['snapshot_identity']
    assert proof['validator']['closure_digest'] == receipt['receipt']['closure_digest']
    for module in proof['validator']['loaded_modules'].values():
        assert Path(module['origin']).is_relative_to(receipt['snapshot'])
    # The approved architecture binding is the run's pinned runner, not the checkout or snapshot.
    runner = proof['validator']['resource_bindings']['al_architecture_check.py']
    gate = next(g for g in json.loads((tmp_path/'artifacts/gates.json').read_bytes())['gates'] if g['id']=='imports-root')
    assert gate['argv'] == [sys.executable,'-I',runner['identity'],'--unit','.']
    assert 'al-run-pins' in Path(runner['identity']).parts
    # Cwd modules and Python/AL controls cannot select validation code or origin.
    with (repo/'.git/info/exclude').open('a') as output:
        output.write('\nal_store.py\nsitecustomize.py\nhostile-bin/\n')
    marker = tmp_path/'injection-marker'
    (repo/'al_store.py').write_text('open('+repr(str(marker))+',"w").write("target")\n')
    (repo/'sitecustomize.py').write_text('open('+repr(str(marker))+',"w").write("startup")\n')
    bin_dir = repo/'hostile-bin';bin_dir.mkdir()
    (bin_dir/'python3').write_text('#!/bin/sh\n: > '+shlex.quote(str(marker))+'\n');(bin_dir/'python3').chmod(0o755)
    environment = dict(os.environ,PYTHONPATH=str(repo),PYTHONHOME=str(repo),AL_RUNTIME_DIR=str(tmp_path/'foreign'),
                       AL_VALIDATOR_ORIGIN=str(tmp_path/'foreign-origin'),PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
    assert source_proof(launcher, repo, state, env=environment)['source']==proof['source'] and not marker.exists()
    (bin_dir/'git').write_text('#!/bin/sh\n: > '+shlex.quote(str(marker))+'\n');(bin_dir/'git').chmod(0o755)
    with pytest.raises(ValueError):
        source_proof(launcher, repo, state, env=environment)
    assert not marker.exists()
    assert snapshot(al_store.RUNTIME_DIR)==before and (repo/'.git/index').read_bytes()==index
