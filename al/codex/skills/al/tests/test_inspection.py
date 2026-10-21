"""Batch inspection is explicit, coherent and byte-for-byte observational."""
import json

import al_store as S
import al_scope


def runtime_bytes():
    return {str(p): p.read_bytes() for p in S.RUNTIME_DIR.rglob('*') if p.is_file()}


def test_explicit_inspection_does_not_write_current_or_locks(cli, state_file, repo, monkeypatch):
    assert cli('start', '--spec', 'Inspect this explicit immutable run without any workflow mutations.')[0] == 0
    state = json.loads(state_file().read_bytes());sid = state['session_id']
    S.CURRENT.unlink()
    before = runtime_bytes()
    import subprocess
    original_run = subprocess.run
    mutations = []
    def observed_run(argv, *args, **kwargs):
        index = repo / '.git/index'
        preimage = index.read_bytes()
        result = original_run(argv, *args, **kwargs)
        if index.read_bytes() != preimage:mutations.append(argv)
        return result
    monkeypatch.setattr(subprocess, 'run', observed_run)
    result = cli('--session', sid, 'inspect' , '--expect-run', state['run_id'], '--expect-repo', str(repo))
    assert result[0] == 0, result
    report = json.loads(result[1])
    assert report['stable'] and set(report['sections']) == {'summary', 'evidence', 'gates', 'operations'}
    assert runtime_bytes() == before, ([p for p, value in runtime_bytes().items() if before.get(p) != value], mutations)
    import os, sys
    env = {k: v for k, v in os.environ.items() if k not in {'AL_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_CONVERSATION_ID'}}
    script = __import__('conftest').SCRIPTS / 'al_state.py'
    missing = subprocess.run([sys.executable, str(script), 'inspect', '--expect-run', state['run_id'],
                              '--expect-repo', str(repo)], cwd=repo, env=env, capture_output=True)
    assert missing.returncode != 0 and b'explicit --session' in missing.stderr

    assert runtime_bytes() == before, [p for p, value in runtime_bytes().items() if before.get(p) != value]


def test_terminal_and_foreign_inspection_bindings(cli, state_file, repo):
    cli('start', '--spec', 'Keep read-only inspection available after stopping the source run.')
    cli('stopped', '--reason', 'Explicit fixture stop')
    state = json.loads(state_file().read_bytes());sid = state['session_id']
    before = runtime_bytes()
    args = ('--session', sid, 'inspect', '--expect-repo', str(repo), '--expect-run')
    assert cli(*args, state['run_id'])[0] == 0
    assert cli(*args, 'foreign')[0] != 0
    assert runtime_bytes() == before, [p for p, value in runtime_bytes().items() if before.get(p) != value]


def test_source_movement_is_not_reported_current(cli, state_file, repo, monkeypatch):
    cli('start', '--spec', 'Detect source movement while producing observational inspection output.')
    state = json.loads(state_file().read_bytes())
    import al_inspection
    original = al_inspection.source_binding
    count = 0
    def moved(st):
        nonlocal count
        count += 1
        if count == 2:(repo / 'svc/app.py').write_text('x = 3\n')
        return original(st)
    monkeypatch.setattr(al_inspection, 'source_binding', moved)
    result = cli('--session', state['session_id'], 'inspect', '--expect-run', state['run_id'], '--expect-repo', str(repo), '--sections', 'summary')
    assert result[0] != 0 and not json.loads(result[1])['stable']


def test_populated_inspection_preserves_index_and_runtime(cli,state_file,repo,tmp_path,monkeypatch):
    from hook_regression_helpers import begin
    begin(cli,tmp_path,repo)
    state=json.loads(state_file().read_bytes())
    (repo/'svc/app.py').touch()
    index=repo/'.git/index'
    before_index=index.read_bytes();before=runtime_bytes()
    import subprocess
    real=subprocess.run;mutations=[]
    def observed(argv,*args,**kwargs):
        prior=index.read_bytes()
        result=real(argv,*args,**kwargs)
        if index.read_bytes()!=prior:mutations.append(argv)
        return result
    monkeypatch.setattr(subprocess,'run',observed)
    result=cli('--session',state['session_id'],'inspect','--expect-run',state['run_id'],'--expect-repo',str(repo))
    assert result[0]==0,result
    assert json.loads(result[1])['stable']
    assert runtime_bytes()==before and index.read_bytes()==before_index,mutations
    al_scope.git_bytes('diff','--name-only','HEAD','--')
    assert index.read_bytes()!=before_index
