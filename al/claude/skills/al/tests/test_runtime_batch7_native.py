"""Native conversation pointers preserve immutable runs and isolate concurrent tasks."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import al_native_sessions as N
import al_statusline
import al_store

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'


def command(repo,native,*args):
    env={**os.environ,'CLAUDE_SESSION_ID':native,'CLAUDE_PROJECT_DIR':str(repo),'AL_RUNTIME_DIR':str(al_store.RUNTIME_DIR)}
    result=subprocess.run([sys.executable,str(SCRIPTS/'al_state.py'),*args],cwd=repo,env=env,text=True,capture_output=True,timeout=20)
    return result


def start(repo,native,text):
    result=command(repo,native,'start','--spec',text)
    assert result.returncode==0,result.stdout+result.stderr
    return N.active(native),result


def test_native_task_switch_preserves_unfinished_run_and_clear_is_local(repo):
    first,_=start(repo,'native-one','First independent task')
    before=al_store.state_path(first).read_bytes()
    second,result=start(repo,'native-one','Second independent task')
    assert first!=second and al_store.state_path(first).read_bytes()==before
    assert 'previous work remains unfinished' in result.stdout and first in result.stdout
    assert json.loads(al_store.state_path(second).read_text())['native_conversation_id']=='native-one'
    other,_=start(repo,'native-two','Other conversation task')
    assert 'Second independent task' in command(repo,'native-one','show','--spec').stdout
    assert command(repo,'native-one','clear').returncode==0
    assert not N.active('native-one') and N.active('native-two')==other
    assert al_store.state_path(first).read_bytes()==before and al_store.state_path(second).is_file()


def test_parallel_conversations_statusline_and_explicit_selection(repo):
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(start,repo,'conversation-a','Verify the independent native conversation A task')
        b=pool.submit(start,repo,'conversation-b','Verify the independent native conversation B task')
        first=a.result()[0];second=b.result()[0]
    assert first!=second
    assert N.active('conversation-a')==first and N.active('conversation-b')==second
    result=command(repo,'conversation-a','stopped','--reason','Conversation A is awaiting an explicit decision')
    assert result.returncode==0
    assert 'STOPPED_WITH_BLOCKERS' in al_statusline.al_segment('conversation-a')
    assert 'Planning' in al_statusline.al_segment('conversation-b')
    assert 'STOPPED_WITH_BLOCKERS' not in al_statusline.al_segment('conversation-b')
    result=command(repo,'conversation-b','--session',first,'show','--spec')
    assert result.returncode==0 and 'Verify the independent native conversation A task' in result.stdout
    assert N.active('conversation-b')==second
    before=al_store.state_path(first).read_bytes()
    result=command(repo,'conversation-b','--session',first,'start','--spec','Duplicate exact run')
    assert result.returncode!=0 and al_store.state_path(first).read_bytes()==before
    assert N.active('conversation-b')==second


def test_failed_start_never_publishes_pointer(repo):
    sid,_=start(repo,'native-failure','Keep current attribution')
    before=N.pointer('native-failure').read_bytes()
    result=command(repo,'native-failure','start','--review-mode','whole-mr','--spec','Missing explicit target')
    assert result.returncode!=0
    assert N.pointer('native-failure').read_bytes()==before and N.active('native-failure')==sid


def test_actual_native_readonly_hook_denies_pager_and_allows_operands(repo):
    hook=SCRIPTS/'al_readonly_guard.py'
    for command_line,allowed in [('git grep -O pager pattern',False),('git grep --open-files-in-pager=cp pattern',False),
                                 ('git diff -O orderfile',True),('git grep -e --open-files-in-pager',True)]:
        data={'tool_name':'Bash','tool_input':{'command':command_line}}
        result=subprocess.run([sys.executable,str(hook)],input=json.dumps(data),cwd=repo,text=True,capture_output=True)
        assert (result.returncode==0)==allowed,(command_line,result.stdout,result.stderr)


def test_git_really_executes_clustered_pager_and_native_guard_denies(repo,tmp_path):
    marker=tmp_path/'pager-marker'
    pager=tmp_path/'pager.sh'
    pager.write_text('#!/bin/sh\nprintf executed > '+shlex.quote(str(marker))+'\n')
    pager.chmod(0o755)
    argv=['git','grep','-nO'+str(pager),'x','--','svc/app.py']
    executed=subprocess.run(argv,cwd=repo,text=True,capture_output=True,timeout=10)
    assert executed.returncode==0 and marker.read_text()=='executed',executed.stderr
    marker.unlink()
    data={'tool_name':'Bash','tool_input':{'command':shlex.join(argv)}}
    guarded=subprocess.run([sys.executable,str(SCRIPTS/'al_readonly_guard.py')],input=json.dumps(data),cwd=repo,text=True,capture_output=True,timeout=10)
    assert guarded.returncode==2 and not marker.exists()
