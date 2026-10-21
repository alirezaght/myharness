"""Isolated read-only terminal approval validation using bundled AL code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def evaluate(session, run, repository):
    import al_store as store
    import al_scope as scope
    import al_task as task
    import al_review as review
    from al_execution_tasks import completion_gaps
    from al_analysis_cache import ObservationContext

    if not session or not run or Path(repository).resolve() != store.REPO_ROOT.resolve():
        raise ValueError('terminal approval requires explicit repository/session/run')
    store._sanitise(session)
    path = store.state_path(session)
    before = path.read_bytes()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate terminal state key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('nonfinite terminal state value')
    json.loads(before, object_pairs_hook=pairs, parse_constant=invalid_constant)
    from al_runtime_resources import binding_identity
    with ObservationContext({'terminal': hashlib.sha256(before).hexdigest(), 'runtime': binding_identity()}, readonly_git=True):
        state = store.load(session)
        if state['run_id'] != run or state['session_id'] != session:
            raise ValueError('terminal approval run binding mismatch')
        if state['status'] != 'APPROVED' or state['phase'] != 'DONE' or state.get('pending_phases'):
            raise ValueError('source run is not terminal APPROVED/DONE')
        index_path = Path(state['identity']['git_dir'])/'index'
        if index_path.exists():index_path.read_bytes()
        candidate = scope.capture_candidate(state)
        approved = state.get('approval', {})
        reviewed = state.get('review', {})
        expected = {'review': reviewed.get('id'), 'fingerprint': candidate['fingerprint'],
                    'task_hash': task.task_hash(state), 'fresh': reviewed.get('fresh'),
                    'comparison': reviewed.get('comparison')}
        if any(approved.get(key) != value or value is None for key, value in expected.items()):
            raise ValueError('terminal approval artifact/source binding mismatch')
        gaps = completion_gaps(state) + review.approval_gaps(state)
        if gaps:
            raise ValueError('terminal approval is no longer valid: ' + str(gaps))
        if path.read_bytes() != before or scope.capture_candidate(state)['fingerprint'] != candidate['fingerprint']:
            raise ValueError('terminal state/source changed during validation')
    return {'schema_version': 1, 'kind': 'terminal-workspace-source-proof',
            'repository': str(store.REPO_ROOT.resolve()), 'session_id': session, 'run_id': run,
            'state_sha256': hashlib.sha256(before).hexdigest(), 'approval': expected,
            'source': {'base': candidate['base'], 'fingerprint': candidate['fingerprint'],
                       'files': candidate['files']}}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--platform', choices=('codex', 'claude'), required=True)
    parser.add_argument('--operation',choices=('terminal','replan','readiness'),default='terminal')
    parser.add_argument('--issuer-binding');parser.add_argument('--issuer-binding-sha256')
    parser.add_argument('--proposal');parser.add_argument('--proposal-sha256')
    parser.add_argument('--expected-manifest');parser.add_argument('--replan-proof');parser.add_argument('--replan-proof-sha256')
    args = parser.parse_args(argv)
    context = sys.modules.get('_al_validator_context')
    if context is None:
        raise ValueError('terminal probe requires trusted captured context')
    captured = context.captured
    def utility(name):
        import types
        filename = str(Path(captured['snapshot'])/'installer'/name)
        module = types.ModuleType('_verified_'+Path(name).stem)
        module.__file__ = filename
        exec(compile(captured['bytes']['installer/'+name],filename,'exec'),module.__dict__)
        return module
    origin = utility('validator_origin.py')
    loader = utility('validator_loader.py')
    readonly = utility('validator_readonly.py').Readonly()
    origin.verify_origin_data(captured['origin'],captured['bytes'])
    runtime = loader.install_closed_loader(captured,args.platform,installers=True)
    import al_runtime_resources as resources
    mapping = captured['origin']['platforms'][args.platform]['resources']
    os.environ['AL_RUNTIME_DIR'] = str(Path(args.repository).resolve()/('.'+args.platform)/'al-runtime')
    readonly.start()
    try:
        pin_context=None
        if args.operation=='terminal':
            from installer.validator_run_pin import capture_run_pin,install_run_context
            pin_context=capture_run_pin(captured,args.repository,args.session,args.run,args.platform)
            if pin_context!=context.request.get('run_pin_context'):
                raise ValueError('terminal protected run pin moved before validation')
            install_run_context(pin_context)
            from installer.validator_environment import install_environment
            install_environment(context.request.get('caller_environment') if pin_context else None)
        identities=pin_context['resource_identities'] if pin_context else {name:str(Path(captured['origin']['source']['al_root'])/path) for name,path in mapping.items()}
        resource_context={'platform':args.platform,
            'closure_digest':captured['origin']['closure_digest'], 'origin_digest':captured['origin']['origin_digest'],
            'bytes':{name:captured['bytes'][path] for name,path in mapping.items()},'identities':identities}
        if pin_context is not None:resource_context['original_execution_origin']=pin_context['execution_origin']
        resources._bind_verified_context(resource_context)
        if args.operation=='terminal':result = evaluate(args.session,args.run,args.repository)
        else:
            from installer.current_policy import evaluate_operation
            args.self_reservation=context.request.get('self_reservation')
            result=evaluate_operation(args,captured)
        loaded = loader.verify_loaded_modules(runtime)
        origin.verify_origin_data(captured['origin'],captured['bytes'])
        if captured['mode']=='snapshot':origin.verify_snapshot(captured['snapshot'],captured['expected'])
        else:
            fresh = origin.capture_canonical(captured['snapshot'])
            if fresh['origin']!=captured['origin']:raise ValueError('canonical validator changed during query')
        readonly.verify()
        result.update(schema_version=2 if args.operation=='terminal' else 1,platform=args.platform)
        result['validator'] = dict(captured['expected'],closure_digest=captured['origin']['closure_digest'],
            interpreter_identity=captured['origin']['interpreter'],platform=args.platform,
            loaded_module_origins_digest=origin.digest(origin.canonical(loaded)),loaded_modules=loaded,
            read_set={str(path):None if item is None else {'metadata':list(item[0]),'sha256':item[1]} for path,item in readonly.reads.items()},
            resource_bindings={name:{'identity':resources.resource_identity(name),'sha256':origin.digest(resources.resource_bytes(name))}
                               for name in ('al_architecture_check.py',)})
        if pin_context is not None:
            result['validator']['run_pin_context_digest']=origin.digest(origin.canonical(pin_context))
            result['validator']['caller_environment_digest']=origin.digest(origin.canonical(context.request['caller_environment']))
        if args.operation!='terminal':result['observation_digest']=origin.digest(origin.canonical(result))
    except (OSError, ValueError, KeyError, TypeError, SystemExit, ImportError) as exc:
        print(json.dumps({'schema_version': 1, 'error': str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
