"""Retain actual native reservation observations emitted by the guarded check."""
import json
from pathlib import Path


def retain_readiness(flow,entry):
    log=Path(entry['log']).read_text();decoder=json.JSONDecoder();proof=None
    for index,char in enumerate(log):
        if char!='{':continue
        try:record,_=decoder.raw_decode(log[index:])
        except ValueError:continue
        if isinstance(record,dict) and record.get('kind')=='al-current-policy-readiness-proof':proof=record;break
    assert proof is not None and proof['reservation_observation']['entry_id']==entry['id']
    receipt=proof['reservation_observation']
    assert receipt['reservation']==entry['reservation'] and receipt['pid']==entry['pid'] and receipt['owner_pid']==entry['owner_pid']
    assert receipt['owner_observation']['argv'][1:]==[str(flow.issuer/'scripts/al_state.py'),'--session',flow.session,'evidence','run','--area',entry['area']]
    assert receipt['caller_observation']['parent_pid']==receipt['owner_pid']
    (flow.artifacts/'readiness-proof.json').write_text(json.dumps(proof,indent=2))
