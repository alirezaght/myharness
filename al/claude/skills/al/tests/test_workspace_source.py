"""Real public approval remains required by the isolated installer query."""
import json
from pathlib import Path
import sys
import subprocess
import os
import shlex

import pytest
import al_store
from evidence_helpers import complete_review, plan_and_run_broad, start_implementation

PLATFORM = 'claude'


def terminal_source(repository, *, session, run, platform):
    trusted = Path(__file__).resolve().parents[5]
    bridge = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
              "from al.installer.terminal_source import terminal_source;"
              "print(json.dumps(terminal_source(sys.argv[2],session=sys.argv[3],run=sys.argv[4],platform=sys.argv[5])))")
    result = subprocess.run([sys.executable, '-I', '-B', '-c', bridge, str(trusted),
                             str(repository), session, run, platform],
                            cwd=repository, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr[-4000:])
    return json.loads(result.stdout)


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def terminal_run(cli, repo, tmp_path, monkeypatch):
    runtime = repo / ('.' + PLATFORM) / 'al-runtime'
    monkeypatch.setattr(al_store, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(al_store, 'CURRENT', runtime / 'current')
    monkeypatch.setenv('AL_RUNTIME_DIR', str(runtime))
    # Runtime membership is excluded by the ordinary repository ignore rules.
    with (repo / '.git/info/exclude').open('a') as output:
        output.write('\n.' + PLATFORM + '/al-runtime/\n')
    start_implementation(cli, tmp_path)
    assert cli('phase', 'FINALIZATION')[0] == 0
    assert cli('phase', 'VERIFICATION')[0] == 0
    assert plan_and_run_broad(cli) == 0
    complete_review(cli, tmp_path, blocking=False)
    assert cli('phase', 'FINAL_VERIFICATION')[0] == 0
    assert cli('approved', '--with-notes', '0')[0] == 0
    return cli.state()


def test_actual_approved_source_uses_trusted_cli_without_runtime_or_index_writes(cli, repo, tmp_path, monkeypatch):
    state = terminal_run(cli, repo, tmp_path, monkeypatch)
    # Target-selected Python modules may never supply the validation logic.
    hostile = repo / 'al_store.py'
    hostile.write_text("raise RuntimeError('target module executed')\n")
    # A new unapproved source file must fail rather than shadow the trusted module.
    before = snapshot(al_store.RUNTIME_DIR)
    index = (repo / '.git/index').read_bytes()
    with pytest.raises(ValueError, match='approval'):
        terminal_source(repo, session=state['session_id'], run=state['run_id'], platform=PLATFORM)
    hostile.unlink()
    proof = terminal_source(repo, session=state['session_id'], run=state['run_id'], platform=PLATFORM)
    assert proof['source']['fingerprint'] == state['approval']['fingerprint']
    assert proof['approval']['task_hash'] == state['approval']['task_hash']
    alias = tmp_path/'repository-alias';alias.symlink_to(repo,target_is_directory=True)
    alias_proof = terminal_source(alias,session=state['session_id'],run=state['run_id'],platform=PLATFORM)
    assert alias_proof['source']==proof['source'] and alias_proof['repository']==str(repo)
    assert proof['schema_version'] == 2 and proof['validator']['origin_digest']
    assert proof['validator']['loaded_modules']['al_store']['origin'].endswith('/scripts/al_store.py')
    assert 'diff' not in proof['source'] and 'untracked' not in proof['source']
    assert snapshot(al_store.RUNTIME_DIR) == before and (repo / '.git/index').read_bytes() == index


def test_foreign_run_and_nonterminal_or_changed_approval_refuse_atomically(cli, repo, tmp_path, monkeypatch):
    state = terminal_run(cli, repo, tmp_path, monkeypatch)
    path = al_store.state_path(state['session_id'])
    original = path.read_bytes()
    for change in ('run', 'nonterminal', 'approval'):
        value = json.loads(original)
        if change == 'nonterminal':value.update(status='RUNNING', phase='REVIEW')
        if change == 'approval':value['approval']['task_hash'] = '0' * 64
        path.write_text(json.dumps(value))
        before = snapshot(al_store.RUNTIME_DIR)
        with pytest.raises(ValueError, match='terminal approval refused'):
            terminal_source(repo, session=state['session_id'], run='foreign' if change == 'run' else state['run_id'], platform=PLATFORM)
        assert snapshot(al_store.RUNTIME_DIR) == before
    path.write_bytes(original)


def test_installed_snapshot_validates_original_architecture_binding_without_rewriting(cli, repo, tmp_path, monkeypatch):
    state = terminal_run(cli, repo, tmp_path, monkeypatch)
    canonical = terminal_source(repo, session=state['session_id'], run=state['run_id'], platform=PLATFORM)
    trusted = Path(__file__).resolve().parents[5]
    launcher = tmp_path/'installed-bin/alctl'
    boot = subprocess.run([sys.executable,'-I','-B',str(trusted/'al/alctl.py'),'bootstrap',
                           '--source-root',str(trusted/'al'),'--destination',str(launcher),
                           '--state-root',str(tmp_path/'installer-state')],
                          cwd=repo,capture_output=True,text=True,timeout=120)
    assert boot.returncode == 0, boot.stderr[-2000:]+boot.stdout[-2000:]
    receipt = json.loads(boot.stdout)
    before = snapshot(al_store.RUNTIME_DIR)
    index = (repo/'.git/index').read_bytes()
    # The installed CLI's preamble owns the protected origin, not the query.
    result = subprocess.run([str(launcher),'source-proof','--repository',str(repo),
                             '--session',state['session_id'],'--run',state['run_id'],'--platform',PLATFORM],
                            cwd=repo,capture_output=True,text=True,timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]+result.stdout[-3000:]
    installed = json.loads(result.stdout)
    (tmp_path/'terminal-source-positive.json').write_text(json.dumps({'canonical':canonical,'installed':installed,'bootstrap':receipt},indent=2))
    for key in ('source','approval','state_sha256'):
        assert installed[key] == canonical[key]
    assert installed['validator']['resource_bindings'] == canonical['validator']['resource_bindings']
    assert installed['validator']['origin_digest'] == receipt['validator_binding']['origin_digest']
    assert installed['validator']['snapshot_identity'] == receipt['validator_binding']['snapshot_identity']
    assert installed['validator']['closure_digest'] == receipt['receipt']['closure_digest']
    for module in installed['validator']['loaded_modules'].values():
        assert Path(module['origin']).is_relative_to(receipt['snapshot'])
    runner = installed['validator']['resource_bindings']['al_architecture_check.py']
    gate = next(g for g in json.loads((tmp_path/'gates.json').read_bytes())['gates'] if g['id']=='imports-root')
    assert gate['argv'] == [sys.executable,'-I',runner['identity'],'--unit','.']
    assert runner['identity'] == str(trusted/'al'/PLATFORM/'skills/al/scripts/al_architecture_check.py')
    assert snapshot(al_store.RUNTIME_DIR)==before and (repo/'.git/index').read_bytes()==index


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
    argv = [str(launcher),'source-proof','--repository',str(repo),'--session',state['session_id'],'--run',state['run_id'],'--platform',PLATFORM]
    injected = subprocess.run(argv,cwd=repo,env=environment,capture_output=True,text=True,timeout=120)
    assert injected.returncode==0,injected.stderr[-2000:]+injected.stdout[-1000:]
    assert json.loads(injected.stdout)['source']==canonical['source'] and not marker.exists()
    (bin_dir/'git').write_text('#!/bin/sh\n: > '+shlex.quote(str(marker))+'\n');(bin_dir/'git').chmod(0o755)
    denied = subprocess.run(argv,cwd=repo,env=environment,capture_output=True,text=True,timeout=120)
    assert denied.returncode!=0 and not marker.exists()
    assert snapshot(al_store.RUNTIME_DIR)==before and (repo/'.git/index').read_bytes()==index
