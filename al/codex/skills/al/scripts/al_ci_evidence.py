"""Supervisor-recorded remote CI results for sourced jobs AL cannot run locally (#17).

A CI job AL could not map stays an UNRESOLVED, unverified gate: it never blocks review. The
supervisor may record the pipeline's own result for it (pipeline/job URL or IDs, status and
commit). The record is a reviewer-visible note bound to the current manifest and candidate; AL
does not contact the provider, so it is never AL-verified evidence and never a passing gate."""
import re
import time
import al_store
import al_scope as S
from al_schema import refuse

STATUSES=('success','failed','canceled','skipped','manual','running','pending','created')


def eligible(g):
    """A sourced CI gate whose job the pipeline verifies: unmapped (UNRESOLVED) or mapped to its verbatim recipe."""
    job=str((g.get('source') or {}).get('job') or '')
    return (g.get('category')=='ci' and bool(job) and not job.startswith('<unresolved-')
            and (g.get('applicability')=='UNRESOLVED' or 'ci_recipe' in g))


def _positive(value,label):
    if value is None:return None
    if not re.fullmatch(r'[1-9][0-9]{0,18}',value):refuse(label+' must be a positive integer')
    return int(value)


def record(st,sid,m,a):
    gate=next((g for g in m['gates'] if g['id']==a.id),None) if m else None
    if not gate:refuse('unknown gate ID')
    if not eligible(gate):refuse('gate '+a.id+' is not a sourced CI job left to the pipeline; remote CI evidence applies only to such jobs')
    if not re.fullmatch(r'https?://[^\s]{1,2000}',a.url or ''):refuse('remote CI evidence needs the pipeline or job URL (http/https)')
    if a.commit is not None and not re.fullmatch(r'[0-9a-f]{7,64}',a.commit):refuse('commit must be a hexadecimal object ID')
    head=S.git_bytes('rev-parse','HEAD').decode().strip()
    candidate=S.capture_candidate(st)['fingerprint']
    value={'gate':gate['id'],'source':{k:gate['source'][k] for k in ('path','job')},'status':a.ci_status,'url':a.url,
           'pipeline_id':_positive(a.pipeline_id,'pipeline ID'),'job_id':_positive(a.job_id,'job ID'),
           'commit':a.commit,'head':head,'commit_matches_head':bool(a.commit) and head.startswith(a.commit),
           'candidate_fingerprint':candidate,'manifest':st['gate_manifests'][-1]['sha256'],
           'note':a.note or '','recorded_by':'supervisor','verified_by_al':False,'at':time.time()}
    st.setdefault('ci_evidence',{})[gate['id']]=value
    al_store.save(sid,st)
    return value


def current(st,gate,fingerprint):
    """The recorded result for this gate, marked stale when the manifest or candidate changed since."""
    value=(st.get('ci_evidence') or {}).get(gate['id'])
    if not value:return None
    stale=value['manifest']!=st['gate_manifests'][-1]['sha256'] or value['candidate_fingerprint']!=fingerprint
    return dict(value,current=not stale)


def describe(value):
    where=value['url']+(' pipeline '+str(value['pipeline_id']) if value.get('pipeline_id') else '')+(' job '+str(value['job_id']) if value.get('job_id') else '')
    commit=('commit '+value['commit']+(' (HEAD)' if value['commit_matches_head'] else ' (not HEAD)')) if value.get('commit') else 'commit not given'
    state='' if value['current'] else ' [stale: manifest or candidate changed since it was recorded]'
    return 'remote CI reported '+value['status']+' at '+where+', '+commit+'; supervisor-recorded, not verified by AL'+state


def rows(st,m,fingerprint):
    """Unverified notes for pipeline-verified jobs and services (gates status, review packet)."""
    out=[]
    for g in m['gates']:
        if not eligible(g):continue
        value=current(st,g,fingerprint)
        if g['applicability']=='APPLICABLE':
            out.append({'gate':g['id'],'category':'ci','reason':'pipeline applicability or runner environment unresolved; '
                        'verified locally with the job\'s verbatim script ('+g['ci_recipe']['semantics']+')',
                        'verified_by':'local verbatim CI recipe','ci_recipe':True,**({'remote_ci':value} if value else {})})
        elif value:
            out.append({'gate':g['id'],'category':'ci','reason':describe(value),'verified_by':'remote CI evidence (supervisor-recorded)','remote_ci':value})
    checks=(m.get('service_coverage') or {}).get('semantic_requirements',{}).get('checks',{})
    for item in checks.get('pipeline_verified',[]):
        jobs=', '.join(j['source_path']+':'+j['job'] for j in item['jobs'])
        out.append({'gate':None,'obligation':item['obligation_id'],'unit':item['unit'],'category':'ci','pipeline_verified':True,
                    'reason':'service verification is left to the pipeline: AL cannot run '+jobs+' locally',
                    'verified_by':'not verified'})
    return out


def register(children,handler):
    p=children.add_parser('ci-evidence',help='record the pipeline result of a sourced CI job AL cannot run locally (a reviewer-visible note, never AL proof)')
    p.add_argument('--id',required=True,help='the UNRESOLVED (or recipe-mapped) CI gate ID from `gates status`')
    p.add_argument('--status',dest='ci_status',choices=STATUSES,required=True)
    p.add_argument('--url',required=True,help='pipeline or job URL')
    p.add_argument('--pipeline-id');p.add_argument('--job-id')
    p.add_argument('--commit',help='the commit the pipeline ran on')
    p.add_argument('--note')
    p.set_defaults(fn=handler)
