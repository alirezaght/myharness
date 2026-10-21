"""Typed approved inputs for the supervisor's comparison invocation."""
from pathlib import Path
import al_artifacts as A
import al_task
from al_schema import refuse


def capture(st,sid,a):
    if not a.plan:refuse('comparison requires explicit --plan approved artifact')
    if st.get('designer_required') and not a.pseudocode:refuse('comparison requires --pseudocode for this Designer route')
    inputs={}
    for name,path in [('plan',a.plan),('pseudocode',a.pseudocode)]:
        if not path:continue
        raw=Path(path).read_bytes()
        if not raw.strip():refuse('approved '+name+' content is empty')
        inputs[name]=(path,raw)
    result={'task_hash':al_task.task_hash(st),'provenance':{'actor':'supervisor','source':a.approval_source or 'CLI approved input designation',
                                                        'review_id':st['review']['id']}}
    if 'proof_admission' in st:
        from al_verification_authority import authority_binding
        authority=authority_binding(st)
        result.update(schema_version=2,kind='comparison-inputs-v2',verification_authority={'hash':authority['hash'],'artifact':authority['artifact']},document_hash=result['task_hash'])
    for name,(path,raw) in inputs.items():
        result[name]=A.snapshot_bytes(sid,raw,'approved-'+name,path)
        result['provenance'][name]={'source':str(path),'sha256':result[name]['sha256']}
    return result


def required(st):
    data=st['review'].get('comparison_inputs')
    if not data:refuse('comparison approved inputs missing for task')
    if 'proof_admission' in st:
        from al_verification_authority import authority_binding,document_binding
        from al_task_editorial import editorial_bridge
        authority=authority_binding(st)
        if data.get('schema_version')!=2 or data.get('kind')!='comparison-inputs-v2' or data.get('verification_authority')!={'hash':authority['hash'],'artifact':authority['artifact']} or data.get('task_hash')!=data.get('document_hash'):refuse('comparison input authority/provenance differs')
        editorial_bridge(st,data['document_hash'],document_binding(st)['hash'],authority['hash'])
    elif data.get('task_hash')!=al_task.task_hash(st):refuse('comparison approved inputs missing/stale for task')
    provenance=data.get('provenance') or {}
    if provenance.get('actor')!='supervisor' or not provenance.get('source') or provenance.get('review_id')!=st['review']['id']:refuse('approved input supervisor provenance missing')
    hashes=[]
    for name in ['plan']+(['pseudocode'] if st.get('designer_required') else []):
        ref=data.get(name)
        if not ref or ref.get('kind')!='approved-'+name or not A.read(ref).strip():refuse('required approved '+name+' artifact missing')
        if provenance.get(name)!={'source':ref['source'],'sha256':ref['sha256']}:refuse('approved input provenance mismatch')
        hashes.append(ref['sha256'])
    return hashes


def packet(st):
    required(st);data=st['review']['comparison_inputs']
    return {'references':data,'contents':{name:A.read(data[name]).decode(errors='replace') for name in ('plan','pseudocode') if name in data}}
