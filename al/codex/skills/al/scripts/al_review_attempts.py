"""Immutable model-policy provenance for independently invoked review stages."""
import json
import secrets
import al_artifacts as A
import al_models as M
from al_schema import refuse

ROLE='al-architect'


def policy_digest():return A.digest(json.dumps(M.POLICY[ROLE],sort_keys=True).encode())


def slot_fields(slot):return {k:slot.get(k,slot['model']) for k in ('model','slug','effort')}


def event(st,slot):
    entry=M.POLICY[ROLE]
    if slot_fields(slot)==slot_fields(entry['primary']):return None
    matches=[e for e in st.get('model_fallbacks',[]) if e['role']==ROLE and e['to']==M.label(slot) and e['reason']]
    if not matches:refuse('fallback attempt requires recorded unavailability event')
    return matches[-1]


def issue(st,sid,stage,replace=False):
    r=st['review'];old=r.get('attempts',{}).get(stage);slot=M.resolve(st,ROLE)
    if slot['model'] in st.get('unavailable_models',[]):refuse('Architect model unavailable')
    fallback=event(st,slot)
    if replace:
        if not old or ('fresh' if stage=='fresh' else 'comparison') in r:refuse('only an issued unsaved attempt may be replaced')
        previous=A.read_json(old)
        if previous['model'] not in st.get('unavailable_models',[]) or slot_fields(previous)==slot_fields(slot) or not fallback:refuse('replacement requires newly recorded approved model unavailability')
    elif old:return A.read_json(old),False
    binding={'attempt_id':secrets.token_hex(12),'stage':stage,'role':ROLE,**slot_fields(slot),
             'policy_digest':policy_digest(),'fallback_event_reference':A.snapshot_bytes(sid,json.dumps(fallback,sort_keys=True).encode(),'model-fallback-event','runtime') if fallback else None}
    if old:r.setdefault('abandoned_attempts',[]).append({'attempt':old,'packet':r['packets'][stage]})
    r.setdefault('attempts',{})[stage]=A.snapshot_bytes(sid,json.dumps(binding,sort_keys=True).encode(),'review-attempt','runtime')
    return binding,True


def validate(st,report,stage,packet):
    r=st['review'];ref=r.get('attempts',{}).get(stage)
    if not ref:refuse('review stage has no immutable model attempt')
    binding=A.read_json(ref);contents=A.read_json(packet)
    if contents.get('attempt')!=binding or binding['stage']!=stage or binding['role']!=ROLE or binding['policy_digest']!=policy_digest():refuse('review attempt/policy provenance mismatch')
    approved=[s for s in (M.POLICY[ROLE]['primary'],M.POLICY[ROLE]['fallback']) if s]
    if not any(slot_fields(binding)==slot_fields(s) for s in approved):refuse('unapproved review model binding')
    fallback=binding['fallback_event_reference']
    if slot_fields(binding)!=slot_fields(M.POLICY[ROLE]['primary']):
        saved=A.read_json(fallback) if fallback else None
        if not saved or saved not in st.get('model_fallbacks',[]) or saved['role']!=ROLE or saved['to']!=M.label(binding):refuse('fallback event provenance invalid')
    elif fallback is not None:refuse('primary review cannot claim fallback provenance')
    if report.get('model') not in {binding['model'],binding['slug']}:refuse('review model does not match issued stage attempt')
    if 'attempt_id' in report and report['attempt_id']!=binding['attempt_id']:refuse('abandoned or unknown review attempt')
    for name in ('effort','policy_digest'):
        if name in report and report[name]!=binding[name]:refuse('report attempt provenance mismatch: '+name)
    return binding
