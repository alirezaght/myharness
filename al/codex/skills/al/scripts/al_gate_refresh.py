"""A planned edit to a rule file refreshes the gate manifest in place instead of a replan.

When every stale reason names a path the plan already covers (a planned package.json, CI file
or new manifest) or a rule file the manifest already sources (a unit's nested AGENTS.md reached
from a planned file), AL rediscovers the same planned paths itself, at every writer finish and
phase step and before `gates run` materialises (`settle`). Base runs, decisions and exceptions of
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

REFRESHED=('gate_manifests','gate_runs','gate_decisions','gate_refreshes','gate_recovery')
SAME=('category','applicability','argv','cwd','scope','env_names','sources','units','execution_kind','review_state')


def sourced(m):
    """Rule files the manifest already sources: its rules and every gate's own source."""
    return {r['path'] for r in m['rules']}|{g['source']['path'] for g in m['gates'] if g.get('source')}


SUBJECT=('source rule changed: ','source rule missing: ','new repository rule source: ')


def refreshable(st,currency):
    if currency['current'] or not currency.get('paths') or not st.get('gate_manifests'):return False
    m=A.read_json(st['gate_manifests'][-1])
    declared=Coverage.Coverage(m.get('paths',[]));known=sourced(m)
    # Rediscovering the planned paths is deterministic, so a changed or removed rule the plan
    # already sources refreshes too; only a new rule outside the planned paths needs a plan.
    if not all(declared(p) or p in known for p in currency['paths']):return False
    if not currency.get('pathless') or currency.get('removed_sourced_rules') is True:return True
    # An edited sourced rule outside the planned paths also joins the task's changed paths. When
    # that is the only other change, the refresh adds exactly those rule files to the planned paths.
    other=[r for r in currency['reasons'] if not r.startswith(SUBJECT)]
    widened=currency.get('widened_paths') or []
    return other==[Currency.REQUIREMENTS] and bool(widened) and all(p in known and p in currency['paths'] for p in widened)


def _keep_planned(old,new):
    """A gate the plan resolved by hand stays resolved; only its source hash is renewed."""
    olds={g['id']:g for g in old['gates']};kept=[]
    for index,g in enumerate(new['gates']):
        o=olds.get(g['id'])
        if o and o['applicability']=='APPLICABLE' and g['applicability']=='UNRESOLVED' and o['category']==g['category']:
            new['gates'][index]=dict(copy.deepcopy(o),source=g.get('source') or o.get('source'));kept.append(g['id'])
    return kept


def _candidates(st,old,widened=()):
    paths=sorted(set(old['paths'])|set(widened)) if widened else old['paths']
    fresh=D.discover(st,paths,old.get('ci_context'),bounded=old.get('discovery_mode')=='bounded')
    merged=copy.deepcopy(fresh);kept=_keep_planned(old,merged)
    out=[(merged,kept)] if kept else []
    return out+[(fresh,[])]


def refresh(st):
    """Current gate currency, after refreshing the manifest when only planned paths went stale.

    Currency, rediscovery and both validations ask about one unchanged worktree within
    this call, so identical inventory questions are answered once."""
    import al_gate_inventory as V
    with V.reuse_inventories():
        return _refresh(st)


def _refresh(st):
    currency=Currency.check(st)
    if not refreshable(st,currency):return currency
    old_ref=st['gate_manifests'][-1];old=A.read_json(old_ref)
    widened=currency.get('widened_paths',[]) if currency.get('pathless') else []
    for m,kept in _candidates(st,old,widened):
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
                                                   'carried':carried,'kept_planned':kept,'at':time.time(),
                                                   **({'added_paths':widened} if widened else {})})
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


def _unsettled(st):
    """Why a refresh cannot run now, or None: rediscovery needs inputs no one is editing."""
    active=sorted(w['id'] for w in st.get('writers',{}).values() if w.get('state')=='ACTIVE')
    if active:return 'writer(s) still active: '+', '.join(active)
    running=sorted(e['id'] for e in st.get('evidence',{}).get('entries',[]) if e.get('status')=='RUNNING' and not e.get('reconciliation'))
    if running:return 'check(s) still running: '+', '.join(running)
    return None


def _stale(st,changed=()):
    """Sourced rule files whose bytes changed, plus rule files among `changed` the plan does not source yet."""
    import al_gates as G
    import al_store
    m=A.read_json(st['gate_manifests'][-1]);known=sourced(m)
    fresh=[p for p in changed if Currency.rule_source(p) and p not in known and (al_store.REPO_ROOT/p).is_file()]
    return sorted(set(G.stale_sources(m))|set(fresh))


def settle(st,*,changed=()):
    """Refresh the gate manifest at a writer or phase step when a rule source changed (issue #32).

    Cheap when nothing changed: only the sourced rule files are hashed. The refresh runs on a
    copy and is kept only when it made the manifest current, so a refused or partial
    rediscovery never changes state. Returns {'stale': [...], 'refreshed': bool, 'blocked': why}."""
    if not st.get('gate_manifests'):return {'stale':[],'refreshed':False,'blocked':None}
    stale=_stale(st,changed)
    if not stale:return {'stale':[],'refreshed':False,'blocked':None}
    blocked=_unsettled(st)
    if blocked:return {'stale':stale,'refreshed':False,'blocked':blocked}
    from al_repository_capture import CaptureDrift,CaptureUnavailable
    trial=copy.deepcopy(st);before=len(st.get('gate_refreshes',[]))
    try:currency=refresh(trial)
    except (SystemExit,CaptureDrift,CaptureUnavailable) as exc:
        return {'stale':stale,'refreshed':False,'blocked':'rediscovery refused: '+str(getattr(exc,'code',None) or exc)}
    if not currency.get('current') or len(trial.get('gate_refreshes',[]))==before:
        return {'stale':stale,'refreshed':False,'blocked':'; '.join(currency.get('reasons') or ['gate obligations need a replacement plan'])}
    # Only the refresh's own records move over; callers keep their references to the rest of the state.
    for key in REFRESHED:
        if key in trial:st[key]=trial[key]
    print('AL · gates refreshed: rule source changed: '+', '.join(stale)+' · manifest revision '
          +str(len(st['gate_manifests']))+' (unchanged gates keep their base runs and decisions)',flush=True)
    return {'stale':stale,'refreshed':True,'blocked':None}


def recovery_message(st,sid,stale,blocked):
    """One refusal naming the changed rule sources and the exact commands that replan them."""
    import shlex
    import al_ledger
    m=A.read_json(st['gate_manifests'][-1])
    file=al_ledger.log_dir(sid)/'gate-refresh-paths.json'
    file.write_text(json.dumps(stale,indent=2)+'\n')
    discover=['gates','discover','--extend','current','--paths-file',str(file)]
    if m.get('discovery_mode')=='bounded':discover.append('--bounded')
    return ('gates run stopped before any gate: rule sources changed since the gate plan: '+', '.join(stale)
            +'\nAL could not refresh the gate manifest automatically ('+blocked+').'
            +'\nRecover (the paths file already lists the changed rule sources):'
            +'\n  '+shlex.join(discover)
            +'\n  gates plan --file <manifest>   (only when the discovered manifest needs resolving)'
            +'\nthen rerun gates run.')
