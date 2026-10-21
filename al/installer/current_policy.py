"""Current bundled policy validates proposals and readiness; issuer stays data."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from .issuer_provenance import verify_issuer
from .validator_origin import canonical, capture, decode, digest


def artifact(path,expected):
    raw,_=capture(path)
    if digest(raw)!=expected:raise ValueError('protected compatibility artifact changed')
    return decode(raw)


def binding(path,expected,repository,session,run,platform):
    from .validator_origin import safe
    target=Path(repository).resolve()
    if safe(path).resolve(strict=True).is_relative_to(target):raise ValueError('issuer binding must be independently external to target')
    value=artifact(path,expected)
    if safe(value['root']).resolve(strict=True).is_relative_to(target):raise ValueError('retained issuer must be external to target')
    observed=verify_issuer(value['root'],value)
    subject=observed['run']
    if subject['repository_identity']['root']!=str(Path(repository).resolve()) or subject['session_id']!=session or subject['run_id']!=run or observed['installation']['platform']!=platform:
        raise ValueError('issuer provenance is foreign to current-policy query')
    return value


def runtime(repository,session,run,platform):
    import al_store as Store
    import al_scope as Scope
    import al_task as Task
    context=sys.modules.get('_al_validator_context')
    if context is None:raise ValueError('current policy requires captured validator execution')
    scripts=context.captured['origin']['platforms'][platform]['modules']['al_store']
    if Store.__file__!=str(Path(context.captured['snapshot'])/scripts):raise ValueError('foreign current-policy runtime')
    if Store.REPO_ROOT.resolve()!=Path(repository).resolve():raise ValueError('current-policy repository mismatch')
    before=Store.state_path(session).read_bytes();decode(before);state=Store.load(session)
    if state['session_id']!=session or state['run_id']!=run:raise ValueError('current-policy run mismatch')
    if state['status']!='RUNNING' or state['phase']!='VERIFICATION':raise ValueError('current-policy handoff requires ordinary VERIFICATION')
    index=Path(state['identity']['git_dir'])/'index'
    if index.exists():index.read_bytes()
    candidate=Scope.capture_candidate(state)
    return state,before,candidate,Task.task_hash(state)


def broader(state):
    return {name:spec for name,spec in state['evidence']['plan'].items() if spec['tier']=='broad'}


def budgets(state):
    names=('rework','retries','retry_history','correction_iteration','max_correction_iterations')
    return {name:state[name] for name in names if name in state}


def environment_binding(state,manifest):
    import al_ledger as Ledger
    names=sorted({name for gate in manifest['gates'] for name in gate.get('env_names',[])}|
                 {name for spec in broader(state).values() for name in spec.get('env',[])})
    return {'names':names,'fingerprint':Ledger.scope_fingerprint([],names)}


def declared_interface_gaps(state):
    import al_store as Store
    names={'.pre-commit-config.yaml','.pre-commit-config.yml','lefthook.yml','lefthook.yaml','.lefthook.yml',
           'Makefile','justfile','Justfile','Taskfile.yml','Taskfile.yaml','buf.yaml','buf.gen.yaml','codegen.yml','codegen.yaml'}
    roots={'.'}
    import al_artifacts as A
    if state.get('gate_manifests'):roots.update(A.read_json(state['gate_manifests'][-1])['units'])
    return sorted(str(Path(unit)/name) for unit in roots for name in names if (Store.REPO_ROOT/unit/name).exists())


def complete_manifest(state,proposal=None):
    import al_artifacts as A
    import al_gate_discovery as Discovery
    import al_gate_inventory as Inventory
    unsupported=declared_interface_gaps(state)
    if unsupported:raise ValueError('unsupported declared check interface needs independent mapping: '+', '.join(unsupported))
    previous=A.read_json(state['gate_manifests'][-1])
    if proposal is None:
        value=Discovery.discover(state,previous['paths'],previous.get('ci_context'))
        fallback=value['rules'][0] if value['rules'] else None
        for gate in value['gates']:
            if gate['applicability']!='UNRESOLVED':continue
            category=gate['category'];root=Path(Inventory.al_store.REPO_ROOT)
            absent=(category=='loc' and not (root/'scripts/check-loc.py').exists() or
                    category=='pre-commit' and not any((root/p).exists() for p in ('.husky/pre-commit','.githooks/pre-commit')) or
                    category=='contracts' and not any((root/u/'package.json').exists() for u in value['units']))
            if absent:gate.update(applicability='NOT_APPLICABLE',required=False,reason='Current complete discovery proves no '+category+' declaration',source=fallback)
        by_id={g['id']:g for g in value['gates']}
        for old in previous['gates']:
            if old['applicability']=='NOT_APPLICABLE':continue
            if old['id'] in by_id:
                new=by_id[old['id']]
                new['scope']=sorted(set(new['scope']+old['scope']))
                new['env_names']=sorted(set(new.get('env_names',[])+old.get('env_names',[])))
                new['sources']=new.get('sources',[])+[s for s in old.get('sources',[]) if s not in new.get('sources',[])]
            else:value['gates'].append(copy.deepcopy(old))
        # Binding signatures cover minimum sourced execution; conservative broader scope is permitted.
        value['revision']=len(state['gate_manifests'])+1;value['id']='G'+str(value['revision'])
    else:value=copy.deepcopy(proposal)
    Discovery.validate_manifest(state,value)
    if value['service_coverage']['assessment']['outcome']=='incomplete' or any(g['applicability']=='UNRESOLVED' for g in value['gates']):
        raise ValueError('current-policy proposal remains incomplete')
    if value.get('revision')!=len(state['gate_manifests'])+1:raise ValueError('proposal revision is not the next immutable revision')
    for old in previous['gates']:
        if old['applicability']=='NOT_APPLICABLE':continue
        new=next((g for g in value['gates'] if g['id']==old['id']),None)
        if not new or new['argv']!=old['argv'] or new['category']!=old['category'] or new['cwd']!=old['cwd'] or not new['required']:
            raise ValueError('proposal drops or substitutes prior required gate '+old['id'])
        from al_ci_inventory import covered
        if not all(covered(new['scope'],p) for p in old['scope']) or not set(old.get('env_names',[]))<=set(new.get('env_names',[])):
            raise ValueError('proposal narrows prior conservative gate '+old['id'])
    return value


def evaluate_operation(args,captured):
    import al_store as Store
    import al_scope as Scope
    import al_artifacts as A
    from al_analysis_cache import ObservationContext
    from al_runtime_resources import binding_identity
    from al_writers import operation_gaps
    issuer=binding(args.issuer_binding,args.issuer_binding_sha256,args.repository,args.session,args.run,args.platform)
    with ObservationContext({'policy':args.operation,'runtime':binding_identity()},readonly_git=True):
        state,before,candidate,task_hash=runtime(args.repository,args.session,args.run,args.platform)
        view=copy.deepcopy(state)
        if args.operation=='readiness':
            from .readiness_reservation import exclude_self
            exclude_self(view,args.self_reservation)
        gaps=operation_gaps(view)
        for entry in view['evidence']['entries']:
            cleanup=entry.get('cleanup',{})
            if cleanup.get('unresolved') or cleanup.get('errors'):gaps.append('owned check cleanup remains uncertain: '+entry['id'])
        if gaps:raise ValueError('current-policy unresolved operations: '+str(gaps))
        refs=state.get('gate_manifests',[])
        if not refs:raise ValueError('existing issuing manifest required')
        previous=A.read_json(refs[-1]);result={'schema_version':1,'kind':'al-current-policy-'+('replan' if args.operation=='replan' else 'readiness')+'-proof',
            'repository':str(Store.REPO_ROOT.resolve()),'session_id':args.session,'run_id':args.run,'platform':args.platform,
            'state_sha256':hashlib.sha256(before).hexdigest(),'task_hash':task_hash,'base_oid':state['identity']['merge_base'],
            'candidate_fingerprint':candidate['fingerprint'],'issuer_capture_digest':issuer['capture_digest'],
            'source':{'base':candidate['base'],'fingerprint':candidate['fingerprint'],'files':candidate['files']}}
        if args.operation=='replan':
            proposal=artifact(args.proposal,args.proposal_sha256) if args.proposal else None
            value=complete_manifest(state,proposal)
            result.update(previous_manifest_digest=refs[-1]['sha256'],proposed_manifest_digest=digest(json.dumps(value,sort_keys=True).encode()),
                expected_revision=len(refs)+1,proposal=value,coverage_semantic_digest=value['service_coverage']['semantic_sha256'],
                required_obligations=value['service_coverage']['assessment']['check_discovery']['obligation_ids'],
                retained_broad_plan_binding=broader(state),budget_binding=budgets(state),prior_artifacts=[r['sha256'] for r in refs],
                environment_binding=environment_binding(state,value),
                completed_phases=[p for p in state['phase_occurrences'] if p.get('completed')])
        else:
            from .readiness_validation import readiness
            result.update(readiness(view,args,issuer))
            result['reservation_observation']=args.self_reservation
        if Store.state_path(args.session).read_bytes()!=before or Scope.capture_candidate(state)['fingerprint']!=candidate['fingerprint']:
            raise ValueError('current-policy state/candidate changed during observation')
        return result
