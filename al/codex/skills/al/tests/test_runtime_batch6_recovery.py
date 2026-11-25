"""Checkpoint-bound replacement gates for recovered unfinished phases."""
import copy
import json
import sys
import time
import pytest
import al_gate_recovery as GateRecovery
from hook_regression_helpers import invoke
from batch5_helpers import state
from batch6_helpers import unfinished,replacement,install_replacement,pause,changed_recovery
from launcher_context import admit_planning


def review_guard_refuses(st,match):
    """The gate-recovery guard review-start and approval run (al_transitions.require_transition)."""
    with pytest.raises(SystemExit,match=match):GateRecovery.ready(copy.deepcopy(st))


def recovery_completes_at_review(st):
    """A current replacement satisfies the review guard, which completes the recovery with its base evidence."""
    work=copy.deepcopy(st);GateRecovery.ready(work);GateRecovery.complete(work)
    assert 'gate_recovery' not in work and work['gate_recovery_history'][-1]['evidence']


@pytest.mark.parametrize('phase',['PSEUDOCODE','IMPLEMENTATION'])
@pytest.mark.parametrize('route',['resume','retry'])
def test_recovered_unfinished_phase_replaces_gates_before_review(cli,tmp_path,repo,state_file,phase,route):
    old=changed_recovery(cli,tmp_path,repo,phase,route)
    destination='IMPLEMENTATION' if phase=='PSEUDOCODE' else 'FINALIZATION'
    # Since #9 (6677112) and b5735a9 stale obligations are implementation debt: a writer is
    # admitted and records them; review and approval still require the replacement plan.
    review_guard_refuses(state(cli),'replanning')
    if phase=='IMPLEMENTATION':
        invoke(cli,'writer','start','--id','debt','--role','al-engineer','--files','svc/app.py')
        s=state(cli);assert s['writers']['debt']['state']=='ACTIVE' and s['gate_recovery']['replacement_manifest'] is None
        assert any('AGENTS.md' in o for o in s['implementation_readiness']['obligations'])
        invoke(cli,'writer','finish','--id','debt','--outcome','success')
        review_guard_refuses(state(cli),'replanning')
    command=[sys.executable,'-c',"print('verified base')"]
    install_replacement(cli,tmp_path,repo,command)
    s=state(cli);assert s['phase']==phase and s['completed_phases']==old
    assert s['gate_recovery']['replacement_manifest']==s['gate_manifests'][-1]['sha256']
    # With a current replacement plan, base gates run alongside the work (review requires them).
    invoke(cli,'gates','run','--stage','base')
    invoke(cli,'phase',destination)
    # The implementation edge continues the recovery; the review guard completes it.
    s=state(cli);assert s['phase']==destination and s['gate_recovery']['continuation_phase']==destination
    recovery_completes_at_review(s)
    if route=='retry':assert s['retries']['al-engineer']==1


def test_failed_base_admits_writer_but_holds_review(cli,tmp_path,repo,state_file):
    changed_recovery(cli,tmp_path,repo,'IMPLEMENTATION')
    install_replacement(cli,tmp_path,repo,[sys.executable,'-c','import sys;sys.exit(1)'])
    invoke(cli,'gates','run','--stage','base','--id','loc',ok=False)
    # A failed base gate no longer blocks the writer; review requires a passing run or a decision.
    invoke(cli,'writer','start','--id','writer','--role','al-engineer','--files','svc/app.py')
    st=state(cli);assert st['writers']['writer']['state']=='ACTIVE'
    # The recovery stays open until review (b5735a9); its replacement is the current manifest.
    assert st['gate_recovery']['replacement_manifest']==st['gate_manifests'][-1]['sha256']
    import al_transitions
    assert any('base gate loc' in gap for gap in al_transitions.workflow_gaps(state(cli)))


@pytest.mark.parametrize('phase',['PSEUDOCODE','IMPLEMENTATION'])
def test_ordinary_stale_obligations_need_a_replacement_plan_before_advancing(cli,tmp_path,repo,state_file,phase):
    unfinished(cli,tmp_path,repo,phase);completed=state(cli)['completed_phases'][:]
    (repo/'Makefile').write_text('lint-imports:\n\ttrue # changed obligation\n')
    command=[sys.executable,'-c',"print('replacement base gate passed')"]
    file=replacement(cli,tmp_path,repo,command)
    invoke(cli,'gates','plan','--file',str(file))
    planned=state(cli)
    assert planned['phase']==phase and planned['completed_phases']==completed
    assert planned['gate_recovery']['replacement_manifest']==planned['gate_manifests'][-1]['sha256']
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
    assert finished['completed_phases'][:len(completed)]==completed
    # Implementation edges continue the recovery (b5735a9); the review guard completes it.
    assert finished['gate_recovery']['continuation_phase']==finished['phase']
    recovery_completes_at_review(finished)


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
