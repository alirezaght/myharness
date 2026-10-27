"""Fresh read-only proof of standalone planning, never implementation approval."""

import hashlib
import json
import subprocess
from pathlib import Path


def evaluate(session, run, repository):
    import al_artifacts as artifacts
    import al_planning as planning
    import al_proof_json as proof
    import al_scope as scope
    import al_store as store
    from al_analysis_cache import ObservationContext
    from al_runtime_resources import binding_identity
    from al_task_attribution import observe
    from al_writers import operation_gaps

    if not session or not run or Path(repository).resolve() != store.REPO_ROOT.resolve():
        raise ValueError('planning proof requires explicit repository/session/run')
    store._sanitise(session)
    path = store.state_path(session)
    before = path.read_bytes()
    with ObservationContext({'planning_export': hashlib.sha256(before).hexdigest(),
                             'runtime': binding_identity()}, readonly_git=True):
        state = store.load(session)
        if state['run_id'] != run or state['session_id'] != session or 'integration' in state:
            raise ValueError('planning transfer requires the exact standalone AL run')
        if state['status'] != 'RUNNING' or state['phase'] not in {'PLANNING','PSEUDOCODE'}:
            raise ValueError('planning transfer requires an active planning occurrence')
        if operation_gaps(state) or any(e['status']=='RUNNING' for e in state['evidence']['entries']):
            raise ValueError('settle source writers and evidence before planning transfer')
        current = planning.validate_admitted(state)
        _, contents, _ = planning.packet(state)
        observed = proof.read(contents['source_observation'])
        if observe(state, expected=observed) != observed:
            raise ValueError('planning source is stale; inspect current implementation and replan')
        candidate = scope.capture_candidate(state)
        task_raw = artifacts.read(state['task_package'])
        package = json.loads(task_raw)
        retained = {}
        for key in ('packet','report','obligations','attempt'):
            reference = current[key]
            raw = artifacts.read(reference)
            retained[key] = {'reference':reference, 'sha256':hashlib.sha256(raw).hexdigest(),
                             'content':json.loads(raw)}
        head = subprocess.check_output(['git','rev-parse','HEAD'], cwd=repository,
                                      text=True).strip()
        if path.read_bytes()!=before or scope.capture_candidate(state)!=candidate:
            raise ValueError('planning source moved during protected validation')
    return {'schema_version':2,'kind':'planning-workspace-source-proof',
            'repository':str(store.REPO_ROOT.resolve()),'session_id':session,'run_id':run,
            'state_sha256':hashlib.sha256(before).hexdigest(),
            'task_package':{'sha256':hashlib.sha256(task_raw).hexdigest(),'content':package},
            'planning':retained,
            'source':{'head':head,'base':candidate['base'],'fingerprint':candidate['fingerprint'],
                      'files':candidate['files']},
            'authority':'REFERENCE_ONLY_CURRENT_HARNESS_ARCHITECT_REVALIDATION_REQUIRED'}
