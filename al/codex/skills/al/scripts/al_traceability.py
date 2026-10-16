"""Validate review links against real artifacts, locations and ledger entries."""
from pathlib import Path
import al_artifacts as A
import al_ledger as L
import al_store
from al_schema import refuse

def locations(items):
    if not isinstance(items,list) or not items: refuse('missing concrete implementation/fix locations')
    for item in items:
        if isinstance(item,dict):
            if not item.get('explanation') or not item.get('artifact'): refuse('non-code link requires hashed artifact and explanation')
            A.read(item['artifact']); continue
        if not isinstance(item,str): refuse('invalid location')
        path,sep,line=item.partition(':')
        p=Path(path)
        if p.is_absolute() or '..' in p.parts or not (al_store.REPO_ROOT/p).is_file(): refuse('dangling implementation location '+item)
        if sep and (not line.isdigit() or not 1 <= int(line) <= len((al_store.REPO_ROOT/p).read_bytes().splitlines())): refuse('invalid line location '+item)

def evidence(st,ids):
    if not isinstance(ids,list) or not ids: refuse('traceability requires verification IDs')
    for eid in ids:
        entry=L.find(st,eid)
        if not entry or L.entry_verdict(st,entry)[0]!='VALID': refuse('invalid linked evidence '+str(eid))

def consolidate(findings):
    by_id={}
    for finding in findings:
        old=by_id.get(finding['id'])
        if old and old['source']!=finding['source']:refuse('historical finding changed source identity')
        current=dict(finding)
        if old and old['severity']=='BLOCKING' and old.get('resolution',{}).get('status')!='fixed' and current['severity']!='BLOCKING':
            current['severity']='BLOCKING'
        by_id[current['id']]=current
    return list(by_id.values())


def obligations(prior,current,fresh=False):
    inherited={f['id'] for f in prior if f['severity']=='BLOCKING' and f.get('resolution',{}).get('status')!='fixed'}
    incoming=[]
    for finding in current:
        copy=dict(finding)
        if fresh or copy['id'] in inherited:copy.pop('resolution',None)
        incoming.append(copy)
    return consolidate(prior+incoming)


def validate(st,report,prior=()):
    package=A.read_json(st['task_package'])
    criteria=report.get('criteria'); findings=report.get('findings')
    if not isinstance(criteria,list) or not isinstance(findings,list): refuse('report requires criteria and findings arrays')
    ids=[c.get('id') for c in criteria]
    if set(ids)!=set(package['criteria']) or len(ids)!=len(set(ids)): refuse('missing/duplicate/unknown criterion coverage')
    for c in criteria:
        if c.get('status') not in {'SATISFIED','UNSATISFIED'} or not c.get('rationale'): refuse('criterion requires status and rationale')
        locations(c.get('locations')); evidence(st,c.get('evidence'))
    if report.get('stage')=='fresh' and (report.get('prior_resolutions') or any('resolution' in f for f in findings)):
        refuse('fresh reports cannot supply finding resolutions or prior resolutions')
    fids=[f.get('id') for f in findings]
    if len(fids)!=len(set(fids)) or any(not isinstance(i,str) or not i for i in fids): refuse('missing/duplicate finding IDs')
    anchors=set(package['criteria']+package['invariants'])
    for f in findings:
        if f.get('source') not in anchors or f.get('severity') not in {'BLOCKING','NON_BLOCKING'} or not f.get('description'): refuse('finding requires criterion/invariant anchor and severity')
        if f.get('resolution'):
            r=f['resolution']
            if r.get('status') not in {'fixed','disputed','deferred'} or not r.get('rationale'): refuse('finding resolution requires status/rationale')
            if r['status']=='fixed': locations(r.get('locations')); evidence(st,r.get('evidence'))
            elif f['severity']=='BLOCKING': refuse('blocking finding cannot be deferred or disputed without Architect fix evidence')
    for criterion in criteria:
        if criterion['status']=='UNSATISFIED' and not any(f['source']==criterion['id'] and f['severity']=='BLOCKING' and f.get('resolution',{}).get('status')!='fixed' for f in findings):
            refuse('UNSATISFIED criterion requires linked blocking finding: '+criterion['id'])
    prior=consolidate(prior)
    consolidate(prior+findings)
    resolutions=report.get('prior_resolutions',[])
    if not isinstance(resolutions,list):refuse('prior resolutions must be an array')
    ids=[r.get('id') for r in resolutions]
    prior_ids={f['id'] for f in prior}
    if len(ids)!=len(set(ids)) or any(i not in prior_ids for i in ids):refuse('duplicate/unknown prior resolution IDs')
    unresolved=0
    for r in resolutions:
        if r.get('status') not in {'fixed','unresolved','deferred','disputed'}:refuse('prior resolution status missing/unknown')
        source=next(f['source'] for f in prior if f['id']==r['id'])
        if r.get('source')!=source or not r.get('rationale'):refuse('prior resolution lacks matching source/rationale')
        if r['status']=='fixed':locations(r.get('locations'));evidence(st,r.get('evidence'))
    for f in prior:
        if f['severity']!='BLOCKING' or f.get('resolution',{}).get('status')=='fixed':continue
        matching=[r for r in resolutions if r['id']==f['id']]
        if matching and matching[0]['status']=='fixed':continue
        current=next((x for x in findings if x['id']==f['id']),None)
        current_open=current and current['severity']=='BLOCKING' and current.get('resolution',{}).get('status')!='fixed'
        if not matching and not current_open:refuse('unresolved prior blocking finding '+f['id']+' requires explicit comparison prior resolution')
        if not current_open:unresolved+=1
    blocking=sum(f['severity']=='BLOCKING' and f.get('resolution',{}).get('status')!='fixed' for f in findings)
    blocking+=unresolved
    notes=sum(f['severity']=='NON_BLOCKING' and not f.get('resolution') for f in findings)
    if report.get('blocking',blocking)!=blocking or report.get('nonblocking',notes)!=notes: refuse('supplied counts contradict derived findings')
    if report.get('status') not in {'APPROVED','CHANGES_REQUIRED'} or (report['status']=='APPROVED' and blocking): refuse('contradictory review verdict')
    return blocking,notes
