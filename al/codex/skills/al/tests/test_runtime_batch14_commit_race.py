"""Locked gate consumers cannot observe PASS before pending cancellation settles."""
import os
from pathlib import Path
import subprocess
import sys
import pytest
from batch14_helpers import state,wait_for
from batch14_process_helpers import prepare,finalized


@pytest.mark.parametrize('remote',[False,True])
def test_locked_observer_cannot_see_transient_pass_before_cancelled_finalization(cli,repo,tmp_path,monkeypatch,remote):
    prepare(cli,repo,tmp_path,monkeypatch,remote,'normal')
    scripts=Path(__file__).resolve().parents[1]/'scripts';sid=state(cli)['session_id']
    ready=tmp_path/'observer-ready';observed=tmp_path/'observer-result';premature=tmp_path/'premature'
    watcher="""import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import al_store
Path(sys.argv[3]).write_text('ready')
with al_store.locked(sys.argv[2]):
    st=al_store.load(sys.argv[2])
    Path(sys.argv[4]).write_text(st['evidence']['entries'][-1]['status'])
"""
    code="""import json,os,signal,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import al_store,al_state,al_gate_process
scripts,sid,ready,observed,premature,watcher=sys.argv[1:]
original_save=al_store.save
original_pending=al_gate_process.Owner.pending
fired=[];children=[]
def save(sid,st):
    if st['evidence']['entries'] and st['evidence']['entries'][-1]['status']=='PASS' and not fired:
        fired.append(True);os.kill(os.getpid(),signal.SIGTERM)
    return original_save(sid,st)
def pending(owner):
    if fired and not children:
        child=subprocess.Popen([sys.executable,'-c',watcher,scripts,sid,ready,observed],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        children.append(child)
        deadline=time.monotonic()+3
        while not Path(ready).exists() and time.monotonic()<deadline:time.sleep(.01)
        assert Path(ready).exists(),'owned observer did not start'
        time.sleep(.2)
        Path(premature).write_text('yes' if Path(observed).exists() else 'no')
    return original_pending(owner)
al_store.save=save;al_gate_process.Owner.pending=pending
sys.argv=['al_state.py','--session',sid,'gates','run','--stage','base']
try:al_state.main()
finally:
    for child in children:child.wait(timeout=4)
"""
    result=subprocess.run([sys.executable,'-c',code,str(scripts),sid,str(ready),str(observed),str(premature),watcher],cwd=repo,capture_output=True,text=True,timeout=12)
    assert result.returncode!=0
    wait_for(observed.exists)
    assert observed.read_text()=='INFRA',result.stdout+result.stderr
    assert premature.read_text()=='no'
    finalized(cli)
