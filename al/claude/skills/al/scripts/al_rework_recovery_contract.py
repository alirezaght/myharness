"""Versioned, append-only authority for bounded rework recovery."""
import copy
import math
import al_proof_json as J
import al_scope as S
import al_task
from al_schema import refuse

FIELDS={'schema_version','kind','origin','grants','occurrences','decisions','continuations','pending','stop','automatic_authorizations'}

def origin(st):
    return {'session_id':st['session_id'],'run_id':st['run_id'],'identity':st['identity'],
            'original_task_hash':st['proof_admission']['original_task_hash'],'runtime_pin':st['runtime_origin']['pin']}

def grant(kind, authorization, index):
    import time
    return {'id':'G'+str(index),'kind':kind,'limit':2,'authorization':authorization,'at':time.time()}

def initial_state(st):
    return dict(schema_version=1,kind='rework-recovery-v1',origin=copy.deepcopy(origin(st)),
                grants=[grant('initial',None,1)],occurrences=[],decisions=[],continuations=[],pending=None,stop=None,automatic_authorizations=0)

def require(st):
    if 'rework_recovery' not in st:refuse('immutable runtime lacks rework recovery capability; use its original issuer, never migrate this run')
    validate(st)
    return st['rework_recovery']


def require_current(st):
    """Require capability only after the executing issuer's origin is validated."""
    import sys
    validator=sys.modules.get('_al_validator_context')
    legacy={'repository':st.get('identity',{}).get('root'),
            'session_id':st.get('session_id'),'run_id':st.get('run_id')}
    if (not {'runtime_origin','proof_admission','rework_recovery'} & set(st)
            and '_al_run_pin_context' not in sys.modules
            and validator is not None
            and getattr(validator,'legacy_readonly',None)==legacy
            and getattr(getattr(validator,'readonly',None),'enabled',False) is True):
        # Current bundled policy may inspect an original legacy issuer as data.
        # This grants no recovery capability; require() still refuses that run.
        return None
    from al_new_origin import context
    transaction=context().transaction
    protected={key:transaction[key] for key in (
        'kind','schema_version','run_id','publication_transaction','pin','initialization')}
    if transaction['schema_version']==2:
        protected.update({key:transaction[key] for key in ('runtime_layout','runtime_directory')})
    if (st.get('session_id')!=transaction['session_id'] or st.get('run_id')!=transaction['run_id']
            or st.get('runtime_origin')!=protected
            or any(st.get('identity',{}).get(key)!=value for key,value in transaction['repository_identity'].items())):
        refuse('recovery current-run identity differs from protected issuer')
    return require(st)


def history(st):
    r=st['rework_recovery']
    return J.digest({k:r[k] for k in ('grants','decisions','continuations')} | {'history':st['rework']['history']})

def binding(st, occurrence=None):
    return {'origin':origin(st),'candidate_hash':S.capture_candidate(st)['fingerprint'],
            'task_hash':al_task.task_hash(st),'history_hash':history(st),
            'occurrence_id':occurrence['id'] if occurrence else None}

def guard(st):
    r=st.get('rework_recovery')
    if r and r['pending']:refuse('rework Architect reassessment pending; use rework-recovery packet/decide')

def lineage(st):
    if 'rework_recovery' not in st:return None
    validate(st)
    r=st['rework_recovery']
    return {k:r[k] for k in ('origin','grants','occurrences','decisions','continuations','automatic_authorizations')}

def required_hashes(st):
    r=st.get('rework_recovery',{})
    return {row[key]['sha256'] for name in ('decisions','continuations') for row in r.get(name,[]) for key in ('report','receipt')}

def integer(value,label,minimum=0):
    if type(value) is not int or value<minimum:refuse('invalid recovery '+label)
    return value


def timestamp(value):
    if type(value) not in {int,float} or not math.isfinite(value) or value<0:refuse('invalid recovery timestamp')


def sha(value):
    if not isinstance(value,str) or not J.SHA.fullmatch(value):refuse('invalid recovery digest')


def validate_binding(st,value,occurrence_id):
    J.closed(value,{'origin','candidate_hash','task_hash','history_hash','occurrence_id'},'recovery binding')
    if value['origin']!=origin(st) or value['occurrence_id']!=occurrence_id:refuse('foreign recovery binding')
    for k in ('candidate_hash','task_hash','history_hash'):sha(value[k])


def validate_request(value):
    J.closed(value,{'arguments','source','findings','files','label','repro','reasons','route'},'pending rework request')
    from al_rework import TRIGGERS
    args=J.closed(value['arguments'],{'source','findings','files','label','repro'}|{k for k,_,_ in TRIGGERS},'pending rework arguments')
    if value['source'] not in {'finalization','verification','core-flaw'}:refuse('invalid rework source')
    integer(value['findings'],'finding count',1);J.strings(value['files'],'rework files');J.strings(value['reasons'],'route reasons')
    if not isinstance(value['label'],str) or value['repro'] is not None and not isinstance(value['repro'],str):refuse('invalid rework label/repro')
    if any(args[k] not in {'yes','no'} for k,_,_ in TRIGGERS):refuse('invalid pending route flags')
    expected=[why for k,bad,why in TRIGGERS if args[k]==bad]
    if value['reasons']!=expected or value['route']!=('architect' if expected else 'fast'):refuse('pending route mismatch')
    if any(args[k]!=value[k] for k in ('source','findings','label')) or args['files']!=','.join(value['files']) or (args['repro'] or None)!=value['repro']:refuse('pending request arguments mismatch')


def validate(st):
    if 'rework_recovery' not in st:return
    r=J.closed(st['rework_recovery'],FIELDS,'rework recovery')
    if type(r['schema_version']) is not int or r['schema_version']!=1 or r['kind']!='rework-recovery-v1' or r['origin']!=origin(st):refuse('recovery capability/origin mismatch')
    for name in ('grants','occurrences','decisions','continuations'):
        if not isinstance(r[name],list):refuse('invalid recovery '+name)
    if integer(r['automatic_authorizations'],'automatic count')>2:refuse('automatic recovery cap invalid')
    ids=[]
    for i,g in enumerate(r['grants'],1):
        J.closed(g,{'id','kind','limit','authorization','at'},'recovery grant');timestamp(g['at'])
        if type(g['limit']) is not int or g['limit']!=2 or g['kind'] not in {'initial','automatic','user'} or g['id']!='G'+str(i):refuse('invalid recovery grant')
        ids.append(g['id'])
    if not ids or r['grants'][0]['kind']!='initial' or sum(g['kind']=='initial' for g in r['grants'])!=1 or r['grants'][0]['authorization'] is not None:refuse('invalid initial recovery grant')
    occurrences={}
    for i,o in enumerate(r['occurrences'],1):
        J.closed(o,{'id','request','checkpoint','phase_occurrence','binding','packets','dispositions'},'recovery occurrence')
        if o['id']!='RR'+str(i):refuse('invalid recovery occurrence sequence')
        occurrences[o['id']]=o;validate_request(o['request']);validate_binding(st,o['binding'],o['id'])
        cp=J.read(o['checkpoint']);index=integer(o['phase_occurrence'],'phase occurrence')
        if index>=len(st['phase_occurrences']) or cp['run_id']!=st['run_id'] or cp['identity']!=st['identity'] or cp['task_hash']!=o['binding']['task_hash'] or cp['candidate_hash']!=o['binding']['candidate_hash'] or cp['prior_phase']!=st['phase_occurrences'][index]['phase'] or cp['prior_phase'] not in {'FINALIZATION','VERIFICATION'}:refuse('unsafe recovery checkpoint')
        if o['checkpoint'] not in st.get('checkpoints',[]):refuse('unretained safe checkpoint')
        if not isinstance(o['packets'],list) or not isinstance(o['dispositions'],list) or len(o['dispositions'])>1:refuse('invalid recovery packet/disposition list')
        for packet in o['packets']:
            J.closed(packet,{'attempt','packet'},'recovery packet reference')
            import al_rework_recovery_attempts as B
            attempt=B.validate(st,packet['attempt'],o['binding'],available=False)
            data=J.read(packet['packet'])
            J.closed(data,{'schema_version','kind','occurrence_id','binding','request','attempt','task_package','amendments','recovery_lineage','rework','evidence','gates','readiness'},'reassessment packet')
            if type(data['schema_version']) is not int or data['schema_version']!=1 or data['kind']!='rework-recovery-packet-v1' or data['occurrence_id']!=o['id'] or data['binding']!=o['binding'] or data['attempt']!=attempt or data['request']!=o['request']:refuse('reassessment packet binding mismatch')
    accepted={}
    from al_rework_recovery import DECISION,CONTINUATION
    for name in ('decisions','continuations'):
        for row in r[name]:
            J.closed(row,{'request_id','request_hash','report','receipt'},'recovery authorization');J.text(row['request_id'],'request id');sha(row['request_hash'])
            if row['request_id'] in accepted:refuse('duplicate recovery authorization id')
            report=J.read(row['report']);receipt=J.read(row['receipt'])
            J.closed(report,DECISION if name=='decisions' else CONTINUATION,'retained recovery request')
            J.closed(receipt,{'schema_version','kind','request_id','request_hash','grant_id','consumption','binding'},'recovery receipt')
            if type(report['schema_version']) is not int or report['schema_version']!=1 or report['kind']!=('rework-recovery-decision-v1' if name=='decisions' else 'rework-recovery-continuation-v1') or type(receipt['schema_version']) is not int or receipt['schema_version']!=1 or receipt['kind']!='rework-recovery-receipt-v1':refuse('unsupported retained recovery schema')
            if J.digest(report)!=row['request_hash'] or report['request_id']!=row['request_id'] or receipt['request_hash']!=row['request_hash'] or receipt['request_id']!=row['request_id'] or receipt['binding']!=report['binding']:refuse('recovery receipt binding mismatch')
            J.text(report['next_action'],'retained next action')
            if name=='decisions':
                o=occurrences.get(report['occurrence_id'])
                if not o or report['binding']!=o['binding'] or not o['packets']:refuse('decision lacks bound occurrence')
                packet=o['packets'][-1];attempt=J.read(packet['attempt'])
                if report['packet_hash']!=packet['packet']['sha256'] or any(report[k]!=attempt[k] for k in ('attempt_id','effort','policy_digest')) or report['model'] not in {attempt['model'],attempt['slug']}:refuse('retained decision attempt mismatch')
                J.text(report['invocation_id'],'retained Architect invocation');J.text(report['rationale'],'retained rationale')
                J.strings(report['remaining_criteria'],'retained criteria',nonempty=True);J.strings(report['inspected_artifacts'],'retained inspections',nonempty=True)
                if not {packet['packet']['sha256'],packet['attempt']['sha256']}<=set(report['inspected_artifacts']):refuse('retained decision inspections missing')
                if report['decision'] not in {'allow','decline'} or report['basis'] not in ({'verified-progress','new-independent-defect','revised-diagnosis'} if report['decision']=='allow' else {'no-useful-next-step'}):refuse('invalid retained decision')
                if not isinstance(report['evidence'],list) or report['decision']=='allow' and not report['evidence']:refuse('retained decision evidence missing')
                # Reports bind their original immutable ledger bytes; later currency may change.
                for claim in report['evidence']:
                    J.closed(claim,{'id','entry_hash','claim'},'retained evidence claim');sha(claim['entry_hash'])
                    if claim['claim'] not in {'historical-red','current-success'}:refuse('invalid retained evidence claim')
                    packet_entry=next((e for e in J.read(packet['packet'])['evidence']['entries'] if e['id']==claim['id']),None)
                    # Evidence run after packet issue is allowed and retained by hash in the live ledger.
                    live=next((e for e in st['evidence']['entries'] if e['id']==claim['id']),None)
                    bound=next((e for e in (packet_entry,live) if e and J.digest(e)==claim['entry_hash'] and e['run']==st['run_id']),None)
                    if not bound:refuse('retained evidence provenance missing')
                    import al_ledger as L
                    if L.assess(bound)[0] in {'LOG_MISSING','LOG_ALTERED','RUNNING','INTERRUPTED'}:refuse('retained recovery evidence bytes unavailable/corrupt')
            else:
                validate_binding(st,report['binding'],None)
                if type(report['grant_size']) is not int or report['grant_size']!=2:refuse('invalid retained continuation grant')
                auth=J.read(report['user_authorization'])
                J.closed(auth,{'schema_version','kind','request_id','binding','message','provenance'},'retained user authorization')
                if type(auth['schema_version']) is not int or auth['schema_version']!=1 or auth['kind']!='explicit-user-continuation-v1' or auth['binding']!=report['binding'] or auth['request_id']!=row['request_id']:refuse('retained user authorization mismatch')
                J.text(auth['message'],'retained user message');J.text(auth['provenance'],'retained user provenance')
                if not any(d.get('checkpoint_id')==report['checkpoint_id'] for o in occurrences.values() for d in o['dispositions']):refuse('continuation lacks retained stopping checkpoint')
            accepted[row['request_id']]=(name,report,receipt)
    for g in r['grants'][1:]:
        item=accepted.get(g['authorization'])
        if not item or item[0]!=('decisions' if g['kind']=='automatic' else 'continuations') or item[2]['grant_id']!=g['id'] or (g['kind']=='automatic' and item[1]['decision']!='allow'):refuse('grant lacks accepted authorization')
    auto=sum(g['kind']=='automatic' for g in r['grants']);users=sum(g['kind']=='user' for g in r['grants'])
    if auto!=r['automatic_authorizations'] or auto!=sum(v[0]=='decisions' and v[1]['decision']=='allow' for v in accepted.values()) or users!=len(r['continuations']):refuse('authorization count mismatch')
    rw=J.closed(st['rework'],{'batch','used','max','total','history'},'rework counters');counts=dict.fromkeys(ids,0);last=-1
    if not isinstance(rw['history'],list):refuse('invalid consumption history')
    for row in rw['history']:
        base={'source','findings','files','label','repro','reasons','route','grant_id','correction_iteration','batch','n','phase','at'}
        J.closed(row,base|({'resolution'} if 'resolution' in row else set()),'consumed rework return')
        if row['grant_id'] not in counts:refuse('consumption lacks grant attribution')
        integer(row['findings'],'consumed finding count',1);J.strings(row['files'],'consumed files');J.strings(row['reasons'],'consumed route reasons')
        if row['source'] not in {'finalization','verification','core-flaw'} or row['route']!=('architect' if row['reasons'] else 'fast') or not isinstance(row['label'],str) or row['phase'] not in {'FINALIZATION','VERIFICATION','REWORK_REASSESSMENT'}:refuse('invalid consumed route/source')
        index=ids.index(row['grant_id']);integer(row['correction_iteration'],'correction attribution');integer(row['batch'],'batch');integer(row['n'],'batch consumption',1);timestamp(row['at'])
        if index<last:refuse('consumption grant order rollback')
        last=index;counts[row['grant_id']]+=1
        if row['n']!=counts[row['grant_id']] or row['batch']!=index:refuse('consumption order mismatch')
        if 'resolution' in row:
            J.closed(row['resolution'],{'reason','artifact'},'rework resolution');J.text(row['resolution']['reason'],'resolution reason');J.reference(row['resolution']['artifact'])
    for k in ('total','used','max','batch'):integer(rw[k],k)
    if any(counts[g]!=2 for g in ids[:-1]):refuse('new grant before previous grant exhaustion')
    if any(v>2 for v in counts.values()) or rw['total']!=len(rw['history']) or rw['total']>2+2*auto+2*users or rw['used']!=counts[ids[-1]] or rw['max']!=2 or rw['batch']!=len(ids)-1:refuse('recovery consumption counters mismatch')
    for name,report,receipt in accepted.values():
        gid=receipt['grant_id'];consumption=receipt['consumption']
        if name=='decisions' and report['decision']=='decline':
            if gid is not None or consumption is not None:refuse('decline created credit/consumption')
        elif gid not in ids:refuse('receipt grant missing')
        elif name=='continuations':
            if consumption is not None:refuse('continuation cannot consume a return')
        elif type(consumption) is not int or not 1<=consumption<=len(rw['history']) or rw['history'][consumption-1]['grant_id']!=gid or rw['history'][consumption-1]['n']!=1:refuse('automatic receipt consumption mismatch')
    for o in occurrences.values():
        for d in o['dispositions']:
            if d.get('kind')=='allowed':
                J.closed(d,{'kind','request_id','receipt'},'allowed recovery disposition');receipt=J.read(d['receipt']);item=accepted.get(d['request_id'])
                if not item or item[0]!='decisions' or item[1]['occurrence_id']!=o['id'] or item[1]['decision']!='allow' or receipt!=item[2]:refuse('allowed disposition lacks accepted decision')
            else:
                J.closed(d,{'occurrence_id','checkpoint_id','kind','reason','checkpoint'},'stopping recovery disposition')
                if d['kind'] not in {'cap','declined','model'} or d['occurrence_id']!=o['id'] or d['checkpoint_id']!=d['checkpoint']['id']:refuse('invalid recovery stop disposition')
                cp=J.read(d['checkpoint']);J.text(d['reason'],'stop reason')
                if d['checkpoint'] not in st.get('checkpoints',[]) or cp['run_id']!=st['run_id'] or cp['identity']!=st['identity'] or cp['reason']!=d['reason']:refuse('unretained/foreign stopping checkpoint')
                if d['kind']=='declined' and not any(v[0]=='decisions' and v[1]['occurrence_id']==o['id'] and v[1]['decision']=='decline' for v in accepted.values()):refuse('decline disposition lacks decision')
    if r['pending'] is not None:
        o=occurrences.get(r['pending'])
        if not o or o['dispositions'] or st['phase']!='REWORK_REASSESSMENT':refuse('pending recovery state mismatch')
    if r['stop'] is not None:
        stop=J.closed(r['stop'],{'occurrence_id','checkpoint_id','kind','reason'},'active recovery stop');o=occurrences.get(stop['occurrence_id'])
        if not o or not any({k:v for k,v in d.items() if k!='checkpoint'}==stop for d in o['dispositions']) or st.get('stopping_checkpoint')!=stop['checkpoint_id'] or st['status']!='STOPPED_WITH_BLOCKERS' or r['pending'] is not None:refuse('active stopping checkpoint mismatch')


def prepare_save(st, previous):
    if previous and 'rework_recovery' in previous:
        validate(previous)
        if 'rework_recovery' not in st:refuse('recovery capability cannot be removed')
        old=previous['rework_recovery'];new=st['rework_recovery']
        for name in ('origin','schema_version','kind'):
            if new[name]!=old[name]:refuse('recovery origin is immutable')
        for name in ('grants','decisions','continuations'):
            if new[name][:len(old[name])]!=old[name]:refuse('recovery history is append-only')
        for i,o in enumerate(old['occurrences']):
            if i>=len(new['occurrences']):refuse('recovery occurrence removed')
            n=new['occurrences'][i]
            if {k:v for k,v in o.items() if k not in {'packets','dispositions'}}!={k:v for k,v in n.items() if k not in {'packets','dispositions'}} or n['packets'][:len(o['packets'])]!=o['packets'] or n['dispositions'][:len(o['dispositions'])]!=o['dispositions']:refuse('recovery occurrence binding is immutable')
        before=previous['rework']['history'];after=st['rework']['history']
        if len(after)<len(before):refuse('rework consumption removed')
        for a,b in zip(before,after):
            if a==b:continue
            original={k:v for k,v in b.items() if k not in {'repro','resolution'}}
            if a.get('repro') or 'resolution' in a or original!={k:v for k,v in a.items() if k!='repro'} or not b.get('repro') or not b.get('resolution'):refuse('rework consumption is immutable')
            J.reference(b['resolution']['artifact'])
            import al_ledger as L
            red=L.find(st,b['repro'])
            if not red or red['status']!='FAIL' or L.assess(red)[0] in {'LOG_MISSING','LOG_ALTERED','RUNNING','INTERRUPTED'}:refuse('rework resolution requires intact recorded red evidence')
    elif previous and 'rework_recovery' in st:refuse('legacy run cannot acquire recovery capability')
    validate(st)
