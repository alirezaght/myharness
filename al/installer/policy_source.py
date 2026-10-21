"""Protected current-policy queries through the same immutable validator chain."""
import os
from pathlib import Path
import subprocess
import sys
from .terminal_source import _context,_request,_binding,query_environment,validate_runtime,validator_binding
from .validator_boot import CHILD
from .validator_origin import canonical,decode,digest,safe
from .validator_observation import target_inventory,verify_read_set
from .current_policy import binding
from .readiness_reservation import current_reservation


COMMON={'schema_version','kind','repository','session_id','run_id','platform','state_sha256','task_hash','base_oid',
        'candidate_fingerprint','issuer_capture_digest','source','validator','observation_digest'}
EXTRA={'replan':{'previous_manifest_digest','proposed_manifest_digest','expected_revision','proposal','coverage_semantic_digest',
                 'required_obligations','retained_broad_plan_binding','budget_binding','prior_artifacts','environment_binding','completed_phases'},
       'readiness':{'stored_manifest_digest','coverage_semantic_digest','required_obligations','gate_receipts','retained_broad_plan_binding','reservation_observation'}}


def response_fields(value,operation):
    if not isinstance(value,dict) or set(value)!=COMMON|EXTRA[operation]:raise ValueError('current-policy response field mismatch')
    kind='al-current-policy-'+operation+'-proof'
    if value['kind']!=kind or type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('current-policy response schema mismatch')
    if value['observation_digest']!=digest(canonical({k:v for k,v in value.items() if k!='observation_digest'})):
        raise ValueError('current-policy observation digest differs')


def policy_source(repository,*,session,run,platform,operation,issuer_binding,issuer_binding_sha256,
                  proposal=None,proposal_sha256=None,expected_manifest=None,replan_proof=None,replan_proof_sha256=None,self_area=None):
    if operation not in {'replan','readiness'} or platform not in {'codex','claude'} or not session or not run:
        raise ValueError('explicit current-policy query identity required')
    repository=safe(Path(repository).resolve());captured=_context();before=_binding(captured)
    issuer=binding(issuer_binding,issuer_binding_sha256,repository,session,run,platform)
    if operation=='replan' and self_area is not None:raise ValueError('replan cannot exempt an unfinished check')
    self_reservation=current_reservation(repository,session,run,platform,issuer,self_area) if operation=='readiness' else None
    request=_request(captured);request['self_reservation']=self_reservation
    environment=query_environment();inventory_before=target_inventory(repository,environment)
    argv=[sys.executable,'-I','-S','-B','-c',CHILD,'--repository',str(repository),'--session',session,'--run',run,
          '--platform',platform,'--operation',operation,'--issuer-binding',str(issuer_binding),'--issuer-binding-sha256',issuer_binding_sha256]
    options={'proposal':proposal,'proposal-sha256':proposal_sha256,'expected-manifest':expected_manifest,
             'replan-proof':replan_proof,'replan-proof-sha256':replan_proof_sha256}
    for name,value in options.items():
        if value is not None:argv.extend(['--'+name,str(value)])
    result=subprocess.run(argv,input=canonical(request),cwd=repository,env=environment,capture_output=True,timeout=120)
    if len(result.stdout)>16*1024*1024 or len(result.stderr)>65536:raise ValueError('current-policy response exceeds bounds')
    try:value=decode(result.stdout)
    except (ValueError,UnicodeError) as exc:raise ValueError('invalid current-policy response') from exc
    kind='al-current-policy-'+('replan' if operation=='replan' else 'readiness')+'-proof'
    if result.returncode or not isinstance(value,dict) or value.get('kind')!=kind:
        raise ValueError('current-policy query refused: '+str(value.get('error','validator failed') if isinstance(value,dict) else 'invalid response'))
    response_fields(value,operation)
    if operation=='readiness' and value['reservation_observation']!=self_reservation:raise ValueError('current-policy native reservation observation differs')
    if (value.get('schema_version'),value.get('repository'),value.get('session_id'),value.get('run_id'),value.get('platform'),value.get('issuer_capture_digest'))!=(1,str(repository),session,run,platform,issuer['capture_digest']):
        raise ValueError('current-policy response identity mismatch')
    if value.get('observation_digest')!=digest(canonical({k:v for k,v in value.items() if k!='observation_digest'})):
        raise ValueError('current-policy observation digest differs')
    if any(value.get('validator',{}).get(k)!=v for k,v in before.items()):raise ValueError('current-policy validator binding changed')
    validate_runtime(value['validator'],captured,platform)
    if validator_binding()!=before:raise ValueError('current-policy trusted validator changed')
    verify_read_set(value['validator']['read_set'])
    if target_inventory(repository,environment)!=inventory_before:raise ValueError('current-policy target/Git membership changed')
    if operation=='readiness' and current_reservation(repository,session,run,platform,issuer,self_area)!=self_reservation:
        raise ValueError('current-policy running reservation changed during query')
    verify_read_set(value['validator']['read_set'])
    return value


def validate_replan(repository,*,session,run,platform,proposal,issuer_binding,issuer_binding_sha256,proposal_sha256=None):
    return policy_source(repository,session=session,run=run,platform=platform,operation='replan',proposal=proposal,
                         proposal_sha256=proposal_sha256,issuer_binding=issuer_binding,issuer_binding_sha256=issuer_binding_sha256)


def compatibility_readiness(repository,*,session,run,platform,expected_manifest,issuer_binding,issuer_binding_sha256,
                            replan_proof,replan_proof_sha256,self_area=None):
    return policy_source(repository,session=session,run=run,platform=platform,operation='readiness',expected_manifest=expected_manifest,
                         issuer_binding=issuer_binding,issuer_binding_sha256=issuer_binding_sha256,replan_proof=replan_proof,
                         replan_proof_sha256=replan_proof_sha256,self_area=self_area)
