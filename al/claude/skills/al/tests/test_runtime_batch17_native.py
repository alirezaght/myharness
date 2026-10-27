"""Native public starts validate historical metadata before creating a new run."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
import al_native_sessions as N
import al_state
import al_statusline
import al_store
from test_runtime_batch17_shell import BAD,GOOD

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
SPEC='Verify a separate native run with immutable task history.'

LAUNCHER=[]


@pytest.fixture(autouse=True)
def _launcher(launcher):
    LAUNCHER[:]=[launcher];yield;LAUNCHER.clear()


def process(repo,native,*args):
    """A native host command through the protected launcher: explicit run id, repository root cwd."""
    env={**os.environ,'CLAUDE_SESSION_ID':native,'AL_RUNTIME_DIR':str(al_store.RUNTIME_DIR)}
    implicit=args[:1]!=('--session',)
    session=None if implicit else args[1]
    if not implicit:args=args[2:]
    argv=LAUNCHER[0].child_argv(Path(repo),session,args[:1]==('start',),args,implicit=implicit)
    return subprocess.run(argv,cwd=repo,env=env,capture_output=True,text=True)

def begin(repo,native):
    result=process(repo,native,'start','--spec',SPEC)
    assert result.returncode==0,result.stderr
    return N.active(native),result

def resources():
    root=al_store.RUNTIME_DIR
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and (p.suffix=='.json' or 'artifacts' in p.relative_to(root).parts or p.name.startswith('.pointer-') or p.name=='current')}

@pytest.mark.parametrize('nested',[False,True])
def test_new_cwd_publication_preserves_history_and_execution_binding(repo,nested):
    first,_=begin(repo,'conversation');old=al_store.state_path(first).read_bytes()
    destination=repo/'svc' if nested else repo
    second,result=begin(destination,'conversation')
    assert second!=first and 'previous work remains unfinished' in result.stdout
    assert al_store.state_path(first).read_bytes()==old
    current=json.loads(al_store.state_path(second).read_bytes())
    assert current['identity']['cwd']==str(destination.resolve())
    assert current['native_conversation_id']=='conversation'
    wrong=repo if nested else repo/'svc'
    rejected=process(wrong,'conversation','show')
    # The launcher resolves a conversation's run per repository location, so another cwd is refused.
    assert rejected.returncode!=0 and ('cwd mismatch' in rejected.stderr or 'no active AL run for this Claude conversation' in rejected.stderr)
    assert process(repo,'conversation','--session',first,'show').returncode==0
    if nested:assert process(destination,'conversation','--session',first,'show').returncode!=0
    assert 'AL' in al_statusline.al_segment('conversation')

def coordination_locks():
    root=al_store.RUNTIME_DIR
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*.lock') if p.is_file()}

@pytest.mark.parametrize('ancestor',['ordinary','artifacts'])
@pytest.mark.parametrize('corruption',['array','missing-field','foreign-conversation','invalid-session','dangling','schema','attribution','session','root','git_dir','common_dir'])
def test_invalid_previous_reference_refuses_before_task_or_state(repo,corruption,ancestor,monkeypatch):
    # Keep the managed repository layout, varying an ancestor name in both contexts.
    import shutil
    destination=repo.parent/ancestor/'repo'
    shutil.copytree(repo,destination,symlinks=True)
    repo=destination
    monkeypatch.chdir(repo)
    runtime=repo/'.claude'/'al-runtime'
    monkeypatch.setattr(al_store,'RUNTIME_DIR',runtime);monkeypatch.setattr(al_store,'CURRENT',runtime/'current')
    monkeypatch.setattr(al_store,'REPO_ROOT',repo);monkeypatch.setenv('AL_RUNTIME_DIR',str(runtime));monkeypatch.setenv('CLAUDE_PROJECT_DIR',str(repo))
    sid,_=begin(repo,'conversation');pointer=N.pointer('conversation');state=al_store.state_path(sid)
    if corruption=='array':pointer.write_text('[]')
    elif corruption=='missing-field':pointer.write_text('{}')
    elif corruption=='foreign-conversation':pointer.write_text(json.dumps({'conversation_id':'foreign','session_id':sid}))
    elif corruption=='invalid-session':pointer.write_text(json.dumps({'conversation_id':'conversation','session_id':'../escape'}))
    elif corruption=='dangling':state.unlink()
    else:
        value=json.loads(state.read_bytes())
        if corruption=='schema':value['schema_version']=-1
        elif corruption=='attribution':value['native_conversation_id']='foreign'
        elif corruption=='session':value['session_id']='foreign'
        else:value['identity'][corruption]='/foreign'
        state.write_text(json.dumps(value))
    before=resources();locks=coordination_locks()
    reservations=set((LAUNCHER[0].storage/'runs').glob('*.json'))
    all_before={str(p.relative_to(al_store.RUNTIME_DIR)):p.read_bytes() for p in al_store.RUNTIME_DIR.rglob('*') if p.is_file()}
    result=process(repo/'svc','conversation','start','--spec',SPEC)
    assert result.returncode!=0
    assert resources()==before
    after=coordination_locks()
    assert all(after.get(name)==raw for name,raw in locks.items())
    added=set(after)-set(locks)
    # The protected launcher reserves a distinct failed-start identity before native history validation.
    from installer.runtime_pin_transaction import read
    created=set((LAUNCHER[0].storage/'runs').glob('*.json'))-reservations
    assert len(created)==1
    reservation=read(created.pop())
    assert reservation['status']=='RESERVED' and reservation['session_id']!=sid
    assert reservation['platform']=='claude' and reservation['pin']==LAUNCHER[0].pin
    assert reservation['repository_identity']['root']==str(repo.resolve())
    expected={'.'+reservation['session_id']+'.lock'} if corruption not in {'array','missing-field','foreign-conversation','invalid-session'} else set()
    assert added == expected
    assert all(after[name]==b'' for name in added)
    all_after={str(p.relative_to(al_store.RUNTIME_DIR)):p.read_bytes() for p in al_store.RUNTIME_DIR.rglob('*') if p.is_file()}
    assert all_after == all_before | {name:b'' for name in added}


def public_main(monkeypatch,capsys,repo,*args):
    monkeypatch.chdir(repo);monkeypatch.setenv('CLAUDE_SESSION_ID','conversation')
    monkeypatch.setattr(sys,'argv',['al_state.py',*args])
    try:al_state.main();code=0
    except (SystemExit,OSError) as error:code=str(error)
    out,err=capsys.readouterr();return code,out,err

@pytest.mark.parametrize('prior',[False,True])
@pytest.mark.parametrize('existing_artifact',[False,True])
@pytest.mark.parametrize('after_replace',[False,True])
def test_pointer_write_failure_cleans_only_new_start_resources(repo,monkeypatch,capsys,existing_artifact,after_replace,prior):
    first=begin(repo,'conversation')[0] if prior else '';before=resources()
    monkeypatch.setattr(al_store,'new_session_id',lambda:'new-run')
    if existing_artifact:
        path=al_store.RUNTIME_DIR/'new-run'/'artifacts'/'preexisting';path.parent.mkdir(parents=True);path.write_text('preserved')
        before=resources()
    original=N.os.replace
    failed=False
    def fail_pointer(source,target):
        nonlocal failed
        if Path(target)==N.pointer('conversation') and not failed:
            failed=True
            if after_replace:original(source,target)
            raise OSError('owned pointer write failure')
        return original(source,target)
    monkeypatch.setattr(N.os,'replace',fail_pointer)
    code,out,err=public_main(monkeypatch,capsys,repo/'svc','start','--spec',SPEC)
    assert code!=0 and 'AL session new-run' not in out
    assert resources()==before and N.active('conversation')==first
    assert not al_store.state_path('new-run').exists()
    assert not list(N.pointer('conversation').parent.glob('.pointer-*'))
    if not existing_artifact:assert not (al_store.RUNTIME_DIR/'new-run').exists()


def test_explicit_session_attribution_and_duplicate_history(repo):
    result=process(repo,'conversation','--session','explicit','start','--spec',SPEC)
    assert result.returncode==0,result.stderr
    old=al_store.state_path('explicit').read_bytes()
    assert json.loads(old)['native_conversation_id'] is None
    assert not N.active('conversation')
    assert process(repo,'conversation','--session','explicit','start','--spec',SPEC).returncode!=0
    assert al_store.state_path('explicit').read_bytes()==old

@pytest.mark.parametrize('command',BAD+GOOD)
def test_actual_native_hook_uses_the_same_bounded_selector(repo,command):
    result=subprocess.run([sys.executable,str(SCRIPTS/'al_readonly_guard.py')],cwd=repo,input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),capture_output=True,text=True)
    assert (result.returncode==0)==(command in GOOD),result.stdout
