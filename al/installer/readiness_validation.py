"""Exact append-only current-policy handoff validation before old-issuer review."""
from .current_policy import artifact,broader,budgets,environment_binding,declared_interface_gaps
from .validator_origin import canonical,digest


def readiness(state,args,issuer):
    import al_artifacts as A
    import al_gate_discovery as Discovery
    import al_gates as Gates
    import al_ledger as Ledger
    from al_transitions import workflow_gaps
    unsupported=declared_interface_gaps(state)
    if unsupported:raise ValueError('unsupported declared check interface: '+', '.join(unsupported))
    proof=artifact(args.replan_proof,args.replan_proof_sha256)
    if proof.get('kind')!='al-current-policy-replan-proof' or proof.get('schema_version')!=1:
        raise ValueError('protected current-policy replan proof required')
    claimed=proof.get('observation_digest')
    if claimed!=digest(canonical({k:v for k,v in proof.items() if k!='observation_digest'})):
        raise ValueError('corrupt current-policy observation proof')
    import al_task as Task
    import al_scope as Scope
    binding={'repository':str(Scope.al_store.REPO_ROOT.resolve()),'session_id':state['session_id'],'run_id':state['run_id'],
             'platform':args.platform,'task_hash':Task.task_hash(state),'base_oid':state['identity']['merge_base'],
             'candidate_fingerprint':Scope.capture_candidate(state)['fingerprint'],'issuer_capture_digest':issuer['capture_digest']}
    if any(proof.get(k)!=v for k,v in binding.items()):raise ValueError('current-policy replan run/task/base/candidate binding changed')
    refs=state['gate_manifests']
    if [r['sha256'] for r in refs[:-1]]!=proof['prior_artifacts'] or refs[-2]['sha256']!=proof['previous_manifest_digest']:
        raise ValueError('issuing manifest history was not preserved append-only')
    stored=A.read_json(refs[-1])
    if refs[-1]['sha256']!=args.expected_manifest or stored!=proof['proposal'] or stored['revision']!=proof['expected_revision']:
        raise ValueError('issuer lost/altered current-policy replacement metadata')
    Discovery.validate_manifest(state,stored)
    if environment_binding(state,stored)!=proof['environment_binding']:raise ValueError('current-policy proposal environment changed')
    import sys
    captured=sys.modules['_al_validator_context'].captured
    expected=dict(captured['expected'],closure_digest=captured['origin']['closure_digest'],interpreter_identity=captured['origin']['interpreter'])
    for name in ('snapshot_identity','origin_digest','closure_digest','interpreter_identity'):
        if proof.get('validator',{}).get(name)!=expected[name]:raise ValueError('replan proof used another validator origin')
    assessment=stored['service_coverage']['assessment']
    if assessment['outcome']=='incomplete' or any(g['applicability']=='UNRESOLVED' for g in stored['gates']):
        raise ValueError('stored current-policy requirements are incomplete')
    if stored['service_coverage']['semantic_sha256']!=proof['coverage_semantic_digest'] or assessment['check_discovery']['obligation_ids']!=proof['required_obligations']:
        raise ValueError('stored coverage obligations changed')
    for area,spec in proof['retained_broad_plan_binding'].items():
        if broader(state).get(area)!=spec:raise ValueError('prior broader required plan changed: '+area)
    if budgets(state)!=proof['budget_binding']:raise ValueError('issuing workflow budgets changed')
    completed=[p for p in state['phase_occurrences'] if p.get('completed')]
    if any(p not in completed for p in proof['completed_phases']):raise ValueError('completed phase history changed')
    gaps=workflow_gaps(state)+Gates.candidate_gaps(state)+Ledger.gaps(state,'broad')
    if gaps:raise ValueError('current-policy readiness requires current base/candidate/broad proof: '+str(gaps))
    receipts=[r for r in state.get('gate_runs',[]) if r['manifest']==refs[-1]['sha256']]
    return {'stored_manifest_digest':refs[-1]['sha256'],'coverage_semantic_digest':stored['service_coverage']['semantic_sha256'],
            'required_obligations':proof['required_obligations'],'gate_receipts':receipts,'retained_broad_plan_binding':proof['retained_broad_plan_binding']}
