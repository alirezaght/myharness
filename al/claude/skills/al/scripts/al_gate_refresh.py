"""A planned edit to a rule file refreshes the gate manifest in place instead of a replan.

When every stale reason names a path the plan already covers (a planned package.json, CI file
or new manifest), AL rediscovers the same paths itself. Base runs, decisions and exceptions of
gates whose definition did not change carry over: the base checkout and the command are the same.
Only a change reaching outside the planned paths still needs the Architect."""
import copy
import json
import time
import al_artifacts as A
import al_gate_coverage as Coverage
import al_gate_currency as Currency
import al_gate_discovery as D
import al_ledger as L

SAME=('category','applicability','argv','cwd','scope','env_names','sources','units','execution_kind','review_state')


def refreshable(st,currency):
    if currency['current'] or currency.get('pathless') or not currency.get('paths') or not st.get('gate_manifests'):return False
    m=A.read_json(st['gate_manifests'][-1])
    return all(Coverage.covered(m.get('paths',[]),p) for p in currency['paths'])


def _keep_planned(old,new):
    """A gate the plan resolved by hand stays resolved; only its source hash is renewed."""
    olds={g['id']:g for g in old['gates']};kept=[]
    for index,g in enumerate(new['gates']):
        o=olds.get(g['id'])
        if o and o['applicability']=='APPLICABLE' and g['applicability']=='UNRESOLVED' and o['category']==g['category']:
            new['gates'][index]=dict(copy.deepcopy(o),source=g.get('source') or o.get('source'));kept.append(g['id'])
    return kept


def _candidates(st,old):
    fresh=D.discover(st,old['paths'],old.get('ci_context'))
    merged=copy.deepcopy(fresh);kept=_keep_planned(old,merged)
    out=[(merged,kept)] if kept else []
    return out+[(fresh,[])]


def refresh(st):
    """Current gate currency, after refreshing the manifest when only planned paths went stale."""
    currency=Currency.check(st)
    if not refreshable(st,currency):return currency
    old_ref=st['gate_manifests'][-1];old=A.read_json(old_ref)
    for m,kept in _candidates(st,old):
        try:D.validate_manifest(st,m)
        except SystemExit:continue
        m['id']=old.get('id','G1');m['revision']=len(st['gate_manifests'])+1
        ref=A.snapshot_bytes(st['session_id'],json.dumps(m,sort_keys=True).encode(),'gate-manifest','gate refresh')
        st['gate_manifests'].append(ref)
        after=Currency.check(st)
        if not after['current']:
            st['gate_manifests'].pop();continue
        carried=_carry(st,old,old_ref['sha256'],m,ref['sha256'])
        recovery=st.get('gate_recovery')
        if recovery and recovery.get('replacement_manifest')==old_ref['sha256']:recovery['replacement_manifest']=ref['sha256']
        st.setdefault('gate_refreshes',[]).append({'superseded':old_ref['sha256'],'manifest':ref['sha256'],'reasons':currency['reasons'],
                                                   'carried':carried,'kept_planned':kept,'at':time.time()})
        return after
    return currency


def _carry(st,old,old_sha,new,new_sha):
    olds={g['id']:g for g in old['gates']}
    same=[g['id'] for g in new['gates'] if g['id'] in olds and all(olds[g['id']].get(k)==g.get(k) for k in SAME)]
    for r in list(st.get('gate_runs',[])):
        if r['manifest']!=old_sha or r['stage']!='base' or r['gate'] not in same:continue
        e=L.find(st,r['evidence'])
        # Reused and associated executions are bound to their manifest; those gates simply rerun.
        if e and not e.get('reuse') and not e.get('execution_association'):
            st['gate_runs'].append(dict(r,manifest=new_sha,carried_from=r.get('carried_from',old_sha)))
    for gate,decision in st.get('gate_decisions',{}).items():
        if gate in same and decision.get('manifest')==old_sha:
            decision.update(manifest=new_sha,carried_from=decision.get('carried_from',old_sha))
    return same
