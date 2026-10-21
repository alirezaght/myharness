"""Exact own-check exemption; fault injection never supplies approval policy."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from al.installer import readiness_reservation as reservation
from al.installer.execution_process import observe
from al.installer.issuer_provenance import capture_issuer,retain_issuer
from al.tests.test_issuer_provenance import fixture


@pytest.fixture
def running(tmp_path,monkeypatch):
    installation,args,_=fixture(tmp_path)
    binding=retain_issuer(capture_issuer(installation,**args,execution_profile=True),tmp_path/'capture')
    root=args['repository'];pid=os.getpid();parent=os.getppid();area='readiness'
    entry={'id':'E1','area':area,'status':'RUNNING','pid':pid,'owner_pid':parent,'run':'known-run','reservation':'a'*32,
           'argv':[sys.executable,*sys.argv],'cwd':'.','scope':['.'],'kind':'check','tier':'broad','preflight':None,'needs':[],
           'sources':[],'env':['HOME','PATH']}
    spec={k:deepcopy(entry[k]) for k in ('argv','cwd','scope','kind','tier','preflight','needs','sources','env')}
    state={'session_id':'known-session','run_id':'known-run','evidence':{'plan':{area:spec},'entries':[entry]}}
    path=root/'.codex/al-runtime/known-session.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(state))
    caller=observe(pid);owner=observe(parent)
    caller.update(cwd=str(root),argv=[caller['argv'][0],*entry['argv'][1:]])
    owner.update(cwd=str(root),argv=[owner['argv'][0],str(installation/'scripts/al_state.py'),'--session','known-session','evidence','run','--area',area])
    frames={pid:caller,parent:owner};monkeypatch.setattr(reservation,'observe',lambda subject:deepcopy(frames[subject]))
    def query():return reservation.current_reservation(root,'known-session','known-run','codex',binding,area)
    return state,path,frames,query


def test_exact_self_exclusion_preserves_other_running_operation(running):
    state,path,frames,query=running;before=path.read_bytes();receipt=query()
    other=deepcopy(state['evidence']['entries'][0]);other.update(id='E2',area='other',reservation='b'*32)
    state['evidence']['entries'].append(other)
    # The child policy process is an actual child of this caller in production.
    original=reservation.os
    try:
        reservation.os=SimpleNamespace(getppid=lambda:os.getpid())
        reservation.exclude_self(state,receipt)
    finally:reservation.os=original
    assert state['evidence']['entries']==[other] and path.read_bytes()==before


@pytest.mark.parametrize('change',['extra-flag','script','session','area','caller-argv','owner-cwd','caller-cwd','parent','foreign-image'])
def test_exact_native_dispatcher_and_caller_frames_are_required(running,change):
    state,path,frames,query=running;caller=frames[os.getpid()];owner=frames[os.getppid()]
    if change=='extra-flag':owner['argv'].insert(1,'-B')
    elif change=='script':owner['argv'][1]='/different/al_state.py'
    elif change=='session':owner['argv'][3]='foreign'
    elif change=='area':owner['argv'][-1]='other'
    elif change=='caller-argv':caller['argv'].append('--unexpected')
    elif change=='owner-cwd':owner['cwd']='/different'
    elif change=='caller-cwd':caller['cwd']='/different'
    elif change=='parent':caller['parent_pid']=999999
    else:
        for frame in frames.values():frame['image']['path']='/different/same-python'
    before=path.read_bytes()
    with pytest.raises(ValueError):query()
    assert path.read_bytes()==before


@pytest.mark.parametrize('change',['run','owner','reservation','scope','env','registered-argv','status','duplicate'])
def test_registered_state_mismatches_refuse_atomically(running,change):
    state,path,frames,query=running;entry=state['evidence']['entries'][0]
    if change=='run':entry['run']='foreign'
    elif change=='owner':entry['owner_pid']=999999
    elif change=='reservation':entry['reservation']='not-a-token'
    elif change=='scope':entry['scope']=['unrelated']
    elif change=='env':entry['env']=[]
    elif change=='registered-argv':state['evidence']['plan']['readiness']['argv'].append('extra')
    elif change=='status':entry['status']='PASS'
    else:state['evidence']['entries'].append(deepcopy(entry))
    path.write_text(json.dumps(state));before=path.read_bytes()
    with pytest.raises(ValueError):query()
    assert path.read_bytes()==before


@pytest.mark.parametrize('change',['reservation','owner_pid','pid','entry_digest'])
def test_child_rejects_changed_or_foreign_self_receipt(running,monkeypatch,change):
    state,path,frames,query=running;receipt=query();receipt[change]='forged' if change in {'entry_digest','reservation'} else 999999
    monkeypatch.setattr(reservation,'os',SimpleNamespace(getppid=lambda:os.getpid()))
    before=deepcopy(state)
    with pytest.raises(ValueError):reservation.exclude_self(state,receipt)
    assert state==before


@pytest.mark.parametrize("area",["readiness",None])
def test_v1_capture_is_not_implicitly_augmented_for_readiness(tmp_path,running,area):
    state,path,frames,query=running
    installation,args,_=fixture(tmp_path/'legacy')
    binding=retain_issuer(capture_issuer(installation,**args),tmp_path/'v1')
    # Bind its real invocation to its own disposable repository/record.
    target=args['repository']/'.codex/al-runtime/known-session.json';target.parent.mkdir(parents=True);target.write_bytes(path.read_bytes())
    before=(Path(binding['root'])/'provenance.json').read_bytes()
    with pytest.raises(ValueError,match='requires protected'):
        reservation.current_reservation(args['repository'],'known-session','known-run','codex',binding,area)
    assert (Path(binding['root'])/'provenance.json').read_bytes()==before


@pytest.mark.parametrize('subject',['owner','caller'])
def test_native_frames_are_reobserved_before_receipt(running,monkeypatch,subject):
    state,path,frames,query=running;calls={}
    target=os.getppid() if subject=='owner' else os.getpid()
    def moving(pid):
        calls[pid]=calls.get(pid,0)+1;frame=deepcopy(frames[pid])
        if pid==target and calls[pid]>1:frame['identity']['start']=[999]
        return frame
    monkeypatch.setattr(reservation,'observe',moving)
    with pytest.raises(ValueError,match='changed'):query()


def test_no_self_query_still_requires_actual_protected_caller_image(tmp_path,monkeypatch):
    installation,args,_=fixture(tmp_path)
    binding=retain_issuer(capture_issuer(installation,**args,execution_profile=True),tmp_path/'capture')
    before=(Path(binding['root'])/'provenance.json').read_bytes()
    def query():return reservation.current_reservation(args['repository'],'known-session','known-run','codex',binding,None)
    assert query() is None  # No reservation is removed or manufactured.
    actual=observe(os.getpid());actual['image']['path']='/different/same-python'
    monkeypatch.setattr(reservation,'observe',lambda pid:deepcopy(actual))
    with pytest.raises(ValueError,match='protected kernel image'):query()
    assert (Path(binding['root'])/'provenance.json').read_bytes()==before
