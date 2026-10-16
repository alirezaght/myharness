"""Public gates own descendants and cancellation through durable association."""
import os
import signal
import subprocess
import pytest
import al_evidence as E
import al_gate_process as P
import al_store
from hook_regression_helpers import invoke
from batch14_helpers import state,wait_for,alive,clean_process
from batch14_process_helpers import prepare,finalized


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('remote',[False,True])
@pytest.mark.parametrize('number',[signal.SIGINT,signal.SIGTERM])
def test_signals_reap_owned_child_and_grandchild_and_finalize_before_propagation(cli,cli_process,repo,tmp_path,monkeypatch,remote,stage,number):
    _,child,grand=prepare(cli,repo,tmp_path,monkeypatch,remote)
    proc=cli_process('gates','run','--stage',stage);pids=[]
    try:
        wait_for(lambda:child.exists() and grand.exists())
        pids=[int(child.read_text()),int(grand.read_text())]
        assert all(alive(pid) for pid in pids)
        proc.send_signal(number);out,err=proc.communicate(timeout=8)
        assert proc.returncode!=0,out+err
        wait_for(lambda:not any(alive(pid) for pid in pids))
        entry=finalized(cli);assert signal.Signals(number).name in entry['note']
    finally:clean_process(proc,pids)


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('remote',[False,True])
def test_normal_public_gate_still_passes(cli,repo,tmp_path,monkeypatch,remote,stage):
    prepare(cli,repo,tmp_path,monkeypatch,remote,'normal')
    invoke(cli,'gates','run','--stage',stage)
    assert state(cli)['evidence']['entries'][-1]['status']=='PASS'


@pytest.mark.parametrize('remote',[False,True])
@pytest.mark.parametrize('seam',['registration','spawn-signal','before-commit','after-commit','signal-commit'])
def test_keyboard_interrupt_and_signal_seams_cannot_leave_late_pass(cli,repo,tmp_path,monkeypatch,remote,seam):
    argv,child,grand=prepare(cli,repo,tmp_path,monkeypatch,remote,'normal' if 'commit' in seam else 'wait')
    fired=[];owned=[]
    if seam=='registration':
        original=E._commit
        def commit(sid,entry):
            if not fired and entry.get('pid'):
                fired.append(True);raise KeyboardInterrupt
            return original(sid,entry)
        monkeypatch.setattr(E,'_commit',commit)
    elif seam=='spawn-signal':
        original=subprocess.Popen
        def spawn(command,*args,**kwargs):
            proc=original(command,*args,**kwargs)
            if command==argv:owned.append(proc.pid)
            if command==argv and not fired:fired.append(True);os.kill(os.getpid(),signal.SIGTERM)
            return proc
        monkeypatch.setattr(P.subprocess,'Popen',spawn)
    else:
        original=al_store.save
        def save(sid,st):
            entries=st['evidence']['entries']
            if entries and entries[-1]['status']=='PASS' and not fired:
                fired.append(True)
                if seam=='signal-commit':os.kill(os.getpid(),signal.SIGTERM)
                elif seam=='before-commit':raise KeyboardInterrupt
                elif seam=='after-commit':original(sid,st);raise KeyboardInterrupt
            return original(sid,st)
        monkeypatch.setattr(al_store,'save',save)
    try:
        with pytest.raises(KeyboardInterrupt):invoke(cli,'gates','run','--stage','base')
        assert fired
        entry=finalized(cli)
        observed=owned+([entry['pid']] if entry.get('pid') else [])
        assert observed and not any(alive(pid) for pid in observed)
        pids=[int(p.read_text()) for p in [child,grand] if p.exists()]
        wait_for(lambda:not any(alive(pid) for pid in pids))
    finally:
        entries=state(cli)['evidence']['entries']
        pids=owned+[e['pid'] for e in entries if e.get('pid')]+[int(p.read_text()) for p in [child,grand] if p.exists()]
        for pid in pids:
            try:os.killpg(pid,signal.SIGKILL)
            except ProcessLookupError:pass



@pytest.mark.parametrize('remote',[False,True])
def test_repeated_cancellation_cannot_interrupt_group_cleanup(cli,cli_process,repo,tmp_path,monkeypatch,remote):
    _,child,grand=prepare(cli,repo,tmp_path,monkeypatch,remote)
    proc=cli_process('gates','run','--stage','base');pids=[]
    try:
        wait_for(lambda:child.exists() and grand.exists());pids=[int(child.read_text()),int(grand.read_text())]
        proc.send_signal(signal.SIGTERM)
        for _ in range(3):
            if proc.poll() is None:proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=8);assert proc.returncode!=0
        wait_for(lambda:not any(alive(pid) for pid in pids));finalized(cli)
    finally:clean_process(proc,pids)


@pytest.mark.parametrize('remote',[False,True])
def test_pending_signal_during_public_child_commit_cannot_persist_late_pass(cli,repo,tmp_path,monkeypatch,remote):
    import sys
    from pathlib import Path
    prepare(cli,repo,tmp_path,monkeypatch,remote,'normal')
    scripts=Path(__file__).resolve().parents[1]/'scripts';sid=state(cli)['session_id']
    code="""import os,signal,sys
sys.path.insert(0,sys.argv[1])
import al_store,al_state
original=al_store.save
fired=[]
def save(sid,st):
    if st['evidence']['entries'] and st['evidence']['entries'][-1]['status']=='PASS' and not fired:
        fired.append(True);os.kill(os.getpid(),signal.SIGTERM)
    return original(sid,st)
al_store.save=save
sys.argv=['al_state.py','--session',sys.argv[2],'gates','run','--stage','base']
al_state.main()
"""
    result=subprocess.run([sys.executable,'-c',code,str(scripts),sid],cwd=repo,capture_output=True,text=True,timeout=8)
    assert result.returncode!=0
    assert 'SIGTERM' in finalized(cli)['note']
