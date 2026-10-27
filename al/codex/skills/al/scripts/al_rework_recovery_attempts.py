"""Immutable approved Architect attempts, separate from final review."""
import secrets
import al_models as M
import al_proof_json as J
import al_review_attempts as B
from al_schema import refuse

def issue(st,sid,binding,previous=None,*,replace=False):
    slot=M.resolve(st,B.ROLE)
    if slot['model'] in st.get('unavailable_models',[]):refuse('Architect model unavailable')
    fallback=B.event(st,slot)
    if previous:
        old=J.read(previous)
        if not replace:return previous
        if old['model'] not in st.get('unavailable_models',[]) or B.slot_fields(old)==B.slot_fields(slot) or not fallback:refuse('replacement requires recorded approved model unavailability')
    elif replace:refuse('no recovery attempt to replace')
    data={'schema_version':1,'kind':'rework-recovery-attempt-v1','attempt_id':secrets.token_hex(12),'role':B.ROLE,
          **B.slot_fields(slot),'policy_digest':B.policy_digest(),'binding':binding,
          'fallback_event_reference':J.snapshot(sid,fallback,'model-fallback-event') if fallback else None}
    return J.snapshot(sid,data,'rework-recovery-attempt')

def validate(st,reference,binding,*,available=True):
    v=J.read(reference)
    J.closed(v,{'schema_version','kind','attempt_id','role','model','slug','effort','policy_digest','binding','fallback_event_reference'},'recovery attempt')
    if type(v['schema_version']) is not int or v['schema_version']!=1 or v['kind']!='rework-recovery-attempt-v1' or v['role']!=B.ROLE or v['binding']!=binding or v['policy_digest']!=B.policy_digest():refuse('recovery attempt binding mismatch')
    if not any(B.slot_fields(v)==B.slot_fields(s) for s in (M.POLICY[B.ROLE]['primary'],M.POLICY[B.ROLE]['fallback']) if s):refuse('unapproved recovery model')
    if available and v['model'] in st.get('unavailable_models',[]):refuse('issued recovery model unavailable; replace explicitly')
    fallback=v['fallback_event_reference']
    if B.slot_fields(v)!=B.slot_fields(M.POLICY[B.ROLE]['primary']):
        event=J.read(fallback) if fallback else None
        if not event or event not in st.get('model_fallbacks',[]) or event['role']!=B.ROLE or event['to']!=M.label(v):refuse('invalid recovery fallback provenance')
    elif fallback is not None:refuse('primary cannot claim fallback')
    return v
