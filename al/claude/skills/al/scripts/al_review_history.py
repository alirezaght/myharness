"""Consolidate immutable review rounds with validated fix provenance."""
import json
import time
import al_artifacts as A
import al_ledger as L
import al_traceability as T
from al_schema import refuse


def fixes(fresh,comparison):
    if comparison.get('stage')!='compare':refuse('historical clearance requires comparison report stage')
    return [{'stage':'compare','kind':'prior','id':r['id'],'source':r['source'],'resolution':r}
            for r in comparison.get('prior_resolutions',[]) if r.get('status')=='fixed']


def receipt(st,sid,fresh_ref,comparison_ref):
    approved=fixes(A.read_json(fresh_ref),A.read_json(comparison_ref))
    evidence={}
    for fix in approved:
        resolution=fix['resolution'];T.locations(resolution.get('locations'));T.evidence(st,resolution.get('evidence'))
        for eid in resolution['evidence']:evidence[eid]=dict(L.find(st,eid))
    data={'schema_version':2,'stage':'compare','report_hash':comparison_ref['sha256'],'fresh_sha256':fresh_ref['sha256'],'comparison_sha256':comparison_ref['sha256'],
          'validated_fixes':approved,'evidence_at_validation':evidence,'at':time.time()}
    return A.snapshot_bytes(sid,json.dumps(data,sort_keys=True).encode(),'review-resolution-validation','runtime')


def historical_fixes(st,item,fresh,comparison):
    expected=fixes(fresh,comparison)
    ref=item.get('resolution_validation')
    data=A.read_json(ref) if ref else None
    if data and (data.get('fresh_sha256')!=item['fresh']['sha256'] or data.get('comparison_sha256')!=item['comparison']['sha256']):refuse('historical fix report binding mismatch')
    if data and data.get('schema_version')==2:
        if data.get('stage')!='compare' or data.get('report_hash')!=item['comparison']['sha256'] or data.get('validated_fixes')!=expected:
            refuse('historical fix comparison stage/hash provenance mismatch')
        for fix in expected:
            for eid in fix['resolution'].get('evidence',[]):
                entry=data.get('evidence_at_validation',{}).get(eid)
                if not entry or entry.get('status')!='PASS' or entry.get('id')!=eid:refuse('historical fix evidence provenance missing')
    else:
        if data and data.get('schema_version')!=1:refuse('unknown historical validation receipt schema')
        for fix in expected:
            r=fix['resolution']
            if not r.get('rationale'):refuse('historical fixed resolution lacks rationale')
            T.locations(r.get('locations'));T.evidence(st,r.get('evidence'))
    return expected


def findings(st):
    history=[]
    for item in st.get('review_history',[]):
        if not item.get('fresh') or not item.get('comparison'):refuse('historical review requires both immutable reports')
        fresh=A.read_json(item['fresh']);comparison=A.read_json(item['comparison'])
        if fresh.get('stage')!='fresh' or comparison.get('stage')!='compare':refuse('historical review stage mismatch')
        for report in (fresh,comparison):
            ids=[f.get('id') for f in report['findings']]
            if len(ids)!=len(set(ids)) or any(not i for i in ids):refuse('ambiguous historical finding IDs')
        prior=T.obligations(history,fresh['findings'],fresh=True)
        resolutions=comparison.get('prior_resolutions',[]);ids=[r.get('id') for r in resolutions]
        if len(ids)!=len(set(ids)):refuse('duplicate historical resolutions')
        for resolution in resolutions:
            old=next((f for f in prior if f['id']==resolution.get('id')),None)
            if not old or old['source']!=resolution.get('source'):refuse('historical resolution changed source identity')
            if resolution.get('status') not in {'fixed','unresolved','deferred','disputed'}:refuse('historical resolution status missing/unknown')
        approved=historical_fixes(st,item,fresh,comparison)
        history=T.obligations(prior,comparison['findings'])
        for fix in approved:
            if fix['kind']!='prior' or fix['stage']!='compare':continue
            for finding in history:
                if finding['id']==fix['id']:
                    if finding['source']!=fix['source']:refuse('historical resolution changed source identity')
                    finding['resolution']=fix['resolution']
    return history
