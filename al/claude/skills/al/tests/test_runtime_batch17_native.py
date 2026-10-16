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

def process(repo,native,*args):
    env={**os.environ,'CLAUDE_SESSION_ID':native,'AL_RUNTIME_DIR':str(al_store.RUNTIME_DIR)}
    return subprocess.run([sys.executable,str(SCRIPTS/'al_state.py'),*args],cwd=repo,env=env,capture_output=True,text=True)

def begin(repo,native):
    result=process(repo,native,'start','--spec',SPEC)
    assert result.returncode==0,result.stderr
    return N.active(native),result

def resources():
    root=al_store.RUNTIME_DIR
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and (p.suffix=='.json' or 'artifacts' in p.parts or p.name.startswith('.pointer-'))}

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
    assert rejected.returncode!=0 and 'cwd mismatch' in rejected.stderr
    assert process(repo,'conversation','--session',first,'show').returncode==0
    if nested:assert process(destination,'conversation','--session',first,'show').returncode!=0
    assert 'AL' in al_statusline.al_segment('conversation')

@pytest.mark.parametrize('corruption',['array','missing-field','foreign-conversation','invalid-session','dangling','schema','attribution','session','root','git_dir','common_dir'])
def test_invalid_previous_reference_refuses_before_task_or_state(repo,corruption):
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
    before=resources();result=process(repo/'svc','conversation','start','--spec',SPEC)
    assert result.returncode!=0
    assert resources()==before


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
