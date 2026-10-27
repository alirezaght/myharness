"""Replacement is a real newer rerun; original failures stay immutable."""
import copy
import json
import sys
from pathlib import Path


def test_audited_replacement_retains_failure_and_latest_failure_wins(cli,repo,state_file,tmp_path):
    assert cli('start','--spec','Retain original failures while recording a current replacement execution.')[0]==0
    argv=[sys.executable,'-c',"from pathlib import Path; raise SystemExit(1 if 'x = 1' in Path('svc/app.py').read_text() else 0)"]
    assert cli('evidence','plan','--area','repair','--kind','check','--scope','svc','--',*argv)[0]==0
    assert cli('evidence','run','--area','repair')[0]!=0
    before=json.loads(state_file().read_bytes())['evidence']['entries'][0]
    log=Path(before['log']).read_bytes()
    (repo/'svc/app.py').write_text('x = 2\n')
    assert cli('evidence','run','--area','repair')[0]==0
    args=('evidence','supersede','--id','E1','--replaced-by','E2','--reason','Source repair passed the same declared check.')
    result=cli(*args);assert result[0]==0,result
    st=json.loads(state_file().read_bytes())
    assert st['evidence']['entries'][0]==before and Path(before['log']).read_bytes()==log
    ref=st['evidence']['supersessions'][0]
    record=json.loads(Path(ref['stored_path']).read_bytes())
    assert record['original']['status']=='FAIL' and record['replacement']['id']=='E2'
    assert cli(*args)[0]==0
    assert len(json.loads(state_file().read_bytes())['evidence']['supersessions'])==1
    unchanged=state_file().read_bytes()
    assert cli(*args[:-1],'Different explanation')[0]!=0
    assert state_file().read_bytes()==unchanged
    (repo/'svc/app.py').write_text('x = 1\n')
    assert cli('evidence','run','--area','repair','--fresh')[0]!=0
    unchanged=state_file().read_bytes()
    assert cli(*args)[0]!=0 and state_file().read_bytes()==unchanged
    assert cli('evidence','require')[0]!=0


def test_foreign_command_or_corrupt_original_cannot_replace(cli,repo,state_file):
    cli('start','--spec','Reject forged replacement selections without editing historical evidence.')
    cli('evidence','plan','--area','check','--kind','check','--scope','svc','--',sys.executable,'-c','raise SystemExit(1)')
    cli('evidence','run','--area','check')
    cli('evidence','plan','--area','check','--kind','check','--scope','svc','--',sys.executable,'-c','pass')
    cli('evidence','run','--area','check')
    before=state_file().read_bytes()
    assert cli('evidence','supersede','--id','E1','--replaced-by','E2','--reason','Changed commands cannot assert their own equivalence.')[0]!=0
    assert state_file().read_bytes()==before
