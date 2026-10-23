"""Checkpoint-bound replacement gates for recovered unfinished phases."""
import json
import sys
import time
import pytest
from hook_regression_helpers import invoke
from batch5_helpers import state
from batch6_helpers import unfinished,replacement,install_replacement,pause,changed_recovery
from launcher_context import admit_planning


@pytest.mark.parametrize('phase',['PSEUDOCODE','IMPLEMENTATION'])
@pytest.mark.parametrize('route',['resume','retry'])
def test_recovered_unfinished_phase_replaces_gates_before_advancing(cli,tmp_path,repo,state_file,phase,route):
    old=changed_recovery(cli,tmp_path,repo,phase,route)
    destination='IMPLEMENTATION' if phase=='PSEUDOCODE' else 'FINALIZATION'
    before=state_file().read_bytes();invoke(cli,'phase',destination,ok=False);assert state_file().read_bytes()==before
    if phase=='IMPLEMENTATION':
        invoke(cli,'writer','start','--id','blocked','--role','al-engineer','--files','svc/app.py',ok=False)
        assert state_file().read_bytes()==before
    command=[sys.executable,'-c',"print('verified base')"]
    install_replacement(cli,tmp_path,repo,command)
    s=state(cli);assert s['phase']==phase and s['completed_phases']==old
    assert s['gate_recovery']['replacement_manifest']==s['gate_manifests'][-1]['sha256']
    before=state_file().read_bytes();invoke(cli,'phase',destination,ok=False);assert state_file().read_bytes()==before
    if phase=='IMPLEMENTATION':
        invoke(cli,'writer','start','--id','blocked','--role','al-engineer','--files','svc/app.py',ok=False)
        assert state_file().read_bytes()==before
    invoke(cli,'gates','run','--stage','base')
    invoke(cli,'phase',destination)
    s=state(cli);assert 'gate_recovery' not in s and s['gate_recovery_history'][-1]['evidence']
    if route=='retry':assert s['retries']['al-engineer']==1


def test_failed_base_blocks_writer_then_valid_replacement_allows_new_lifetime(cli,tmp_path,repo,state_file):
    changed_recovery(cli,tmp_path,repo,'IMPLEMENTATION')
    install_replacement(cli,tmp_path,repo,[sys.executable,'-c','import sys;sys.exit(1)'])
    invoke(cli,'gates','run','--stage','base','--id','loc',ok=False)
    before=state_file().read_bytes()
    invoke(cli,'writer','start','--id','writer','--role','al-engineer','--files','svc/app.py',ok=False)
    invoke(cli,'phase','FINALIZATION',ok=False);assert state_file().read_bytes()==before
    install_replacement(cli,tmp_path,repo,[sys.executable,'-c',"print('base passes')"])
    invoke(cli,'gates','run','--stage','base')
    invoke(cli,'writer','start','--id','writer','--role','al-engineer','--files','svc/app.py')
    assert not state(cli).get('gate_recovery') and state(cli)['writers']['writer']['state']=='ACTIVE'


@pytest.mark.parametrize('phase',['PSEUDOCODE','IMPLEMENTATION'])
def test_ordinary_stale_obligations_need_ready_spec_and_base_before_advancing(cli,tmp_path,repo,state_file,phase):
    unfinished(cli,tmp_path,repo,phase);completed=state(cli)['completed_phases'][:]
    (repo/'Makefile').write_text('lint-imports:\n\ttrue # changed obligation\n')
    command=[sys.executable,'-c',"print('replacement base gate passed')"]
    file=replacement(cli,tmp_path,repo,command)
    invoke(cli,'gates','plan','--file',str(file))
    planned=state(cli)
    assert planned['phase']==phase and planned['completed_phases']==completed
    assert planned['gate_recovery']['replacement_manifest']==planned['gate_manifests'][-1]['sha256']
    before=state_file().read_bytes()
    if phase=='IMPLEMENTATION':
        invoke(cli,'writer','start','--id','replacement','--role','al-engineer','--files','svc/app.py',ok=False)
    else:
        admit_planning(cli)
        invoke(cli,'phase','IMPLEMENTATION',ok=False)
    assert state_file().read_bytes()==before
    invoke(cli,'gates','run','--stage','base')
    if phase=='IMPLEMENTATION':
        invoke(cli,'writer','start','--id','replacement','--role','al-engineer','--files','svc/app.py')
        invoke(cli,'writer','finish','--id','replacement','--outcome','success')
        invoke(cli,'phase','VERIFICATION')
    else:
        admit_planning(cli)
        invoke(cli,'phase','IMPLEMENTATION')
    finished=state(cli)
    assert finished['phase']==('IMPLEMENTATION' if phase=='PSEUDOCODE' else 'VERIFICATION')
    assert finished['completed_phases'][:len(completed)]==completed and not finished.get('gate_recovery')


@pytest.mark.parametrize('defect',['phase','checkpoint','corrupt-checkpoint'])
def test_mismatched_recovery_marker_cannot_admit_replacement(cli,tmp_path,repo,state_file,defect):
    changed_recovery(cli,tmp_path,repo,'IMPLEMENTATION');s=state(cli)
    if defect=='phase':s['gate_recovery']['phase']='PSEUDOCODE'
    elif defect=='checkpoint':s['gate_recovery']['checkpoint']='missing'
    else:
        from pathlib import Path
        Path(s['checkpoints'][-1]['stored_path']).write_bytes(b'corrupt')
    state_file().write_text(json.dumps(s));file=replacement(cli,tmp_path,repo);before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(file),ok=False);assert state_file().read_bytes()==before


def test_actual_running_check_blocks_recovery_replacement(cli,cli_process,tmp_path,repo,state_file):
    changed_recovery(cli,tmp_path,repo,'IMPLEMENTATION');file=replacement(cli,tmp_path,repo)
    invoke(cli,'evidence','plan','--area','running','--tier','focused','--kind','check','--scope','docs','--',sys.executable,'-c','import time;time.sleep(5)')
    proc=cli_process('evidence','run','--area','running')
    try:
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            if any(e['status']=='RUNNING' and e.get('pid') for e in state(cli)['evidence']['entries']):break
            time.sleep(.02)
        else:pytest.fail('check never reached its real running lifetime')
        before=state_file().read_bytes();invoke(cli,'gates','plan','--file',str(file),ok=False)
        assert state_file().read_bytes()==before
    finally:
        proc.terminate();proc.communicate(timeout=5)
