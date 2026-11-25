"""The alctl command-line interface."""
import argparse
import json
import os
import subprocess
from pathlib import Path
from .bootstrap import bootstrap_cli
from .doctor import doctor, prerequisites
from .guards import ActiveRuns
from .operations import rollback_previous, run_operation, update_registered
from .registry import legacy_records, read_json, records
from .targets import resolve_target
from .source import git_env
from .payload_provenance import display_version


def parser():
    ap = argparse.ArgumentParser(prog='alctl', description='Install and manage verified, Git-tagged AL releases.')
    sub = ap.add_subparsers(dest='command', required=True)
    pinned = sub.add_parser('run', help='run AL through its protected immutable runtime')
    pinned.add_argument('--repository', required=True)
    pinned.add_argument('--platform', choices=('codex', 'claude'), required=True)
    pinned.add_argument('--existing-run', action='store_true')
    pinned.add_argument('--session-directory', action='store_true')
    pinned.add_argument('--session', help='AL run id; inside a Claude conversation it may be omitted')
    pinned.add_argument('arguments', nargs=argparse.REMAINDER)
    storage = sub.add_parser('storage', help='managed scratch storage shared with Harness (HARNESS_HOME/managed-storage)')
    storage_actions = storage.add_subparsers(dest='storage_action', required=True)
    prune = storage_actions.add_parser('prune', help='reclaim provably settled retained scratch (dry run unless --apply)')
    prune.add_argument('--root', help='managed-storage namespace (default: HARNESS_HOME/managed-storage)')
    prune.add_argument('--apply', action='store_true', help='delete; the default is a dry run')
    prune.add_argument('--seconds', type=float, default=60.0, help='overall time budget')
    prune.add_argument('--json', action='store_true')
    service = sub.add_parser('service', help='status or stop the resident AL services of this user')
    service.add_argument('action', choices=('status', 'stop'))
    service.add_argument('--repository', help='only the service of this repository')
    successor = sub.add_parser('runtime-successor', help='trusted operator successor preparation only')
    child = successor.add_subparsers(dest='successor_action', required=True)
    for action in ('prepare', 'activate', 'inputs', 'recovery-inputs', 'origin', 'inspect', 'distribution'):
        command = child.add_parser(action)
        command.add_argument('--platform', choices=('codex', 'claude'), required=True)
        if action == 'origin':
            command.add_argument('--allow-absent', action='store_true')
        if action not in {'distribution', 'origin'}:
            command.add_argument('--request-id', required=True)
        if action in {'prepare', 'activate', 'inputs', 'recovery-inputs', 'origin'}:
            command.add_argument('--repository', required=True)
            if action not in {'inputs', 'recovery-inputs', 'origin'}:
                command.add_argument('--coordinator-fd', type=int, required=True)
            else:
                command.add_argument('--session', required=True)
                if action != 'origin':
                    command.add_argument('--authority-ref', required=True)
                if action == 'recovery-inputs':
                    command.add_argument('--handoff-ref', required=True)
    for name in ('source-proof','planning-proof','replan-proof','compatibility-readiness'):
        proof = sub.add_parser(name, help='read-only protected source policy validation')
        proof.add_argument('--repository', required=True)
        proof.add_argument('--session', required=True)
        proof.add_argument('--run', required=True)
        proof.add_argument('--platform', choices=('codex','claude'), required=True)
        if name not in {'source-proof','planning-proof'}:
            proof.add_argument('--issuer-binding',required=True)
            proof.add_argument('--issuer-binding-sha256',required=True)
        if name=='replan-proof':
            proof.add_argument('--proposal');proof.add_argument('--proposal-sha256')
        if name=='compatibility-readiness':
            proof.add_argument('--expected-manifest',required=True)
            proof.add_argument('--replan-proof',required=True);proof.add_argument('--replan-proof-sha256',required=True)
            proof.add_argument('--self-area')
    for command in ('install', 'list', 'update', 'doctor', 'rollback', 'bootstrap', 'prune-stale-runs'):
        p = (sub.add_parser(command, help='archive run bindings whose repository no longer exists (dry run unless --apply)')
             if command == 'prune-stale-runs' else sub.add_parser(command))
        p.add_argument('--state-root', default=os.environ.get('AL_DISTRIBUTION_STATE') or str(Path.home() / '.local/state/al-distribution/registry'),
                       help='ownership registry root (explicit paths are used directly; default: ~/.local/state/al-distribution/registry)')
        p.add_argument('--home', default=str(Path.home()), help='global installation home (also useful for isolated tests)')
        p.add_argument('--json', action='store_true')
        if command in {'install', 'update', 'rollback', 'doctor'}:
            scope = p.add_mutually_exclusive_group()
            scope.add_argument('--global', dest='global_scope', action='store_true')
            scope.add_argument('--repo')
            p.add_argument('--runtime-root', action='append', default=[])
        if command in {'install', 'update', 'rollback'}:
            p.add_argument('--platform', default=None)
            p.add_argument('--reviews', help='explicit instruction/config review JSON artifact')
            p.add_argument('--adopt', help='reviewed prior-managed/resource adoption JSON artifact')
        if command in {'install', 'update'}:
            p.add_argument('--source', help='explicitly trusted local Git repository or HTTPS URL')
            p.add_argument('--source-subdir', default=None)
            p.add_argument('--version')
            p.add_argument('--policy', choices=('follow', 'pinned'))
            p.add_argument('--status-line', action='store_true', help='explicitly install the optional Claude status line')
        if command == 'update':
            p.add_argument('--all', action='store_true')
            p.add_argument('--allow-running', action='store_true',
                           help='explicit disruptive global package update with exact operator/source reviews')
            p.add_argument('--documentation-only', action='store_true',
                           help='reviewed, hash-bound documentation update of an enrolled global target')
            p.add_argument('--reviewed-runtime-update', action='store_true',
                           help='exact independently reviewed global 0.1.7 to 0.1.8 payload update for future runs')
        if command == 'rollback':
            p.add_argument('--previous', action='store_true', required=True)
        if command == 'prune-stale-runs':
            p.add_argument('--apply', action='store_true',
                           help='move the stale records into runtime-archive/<timestamp>/ (never deletes)')
        if command == 'bootstrap':
            p.add_argument('--source-root', default=str(Path(__file__).resolve().parents[1]))
            p.add_argument('--destination', default=str(Path.home() / '.local/bin/alctl'))
    return ap


def storage_report(home=None, *, seconds=3.0):
    """Doctor's bounded, read-only managed-storage summary (never fails doctor)."""
    from .storage_inventory import retained_summary
    from .storage_lifecycle import StorageManager, default_root
    try:
        return retained_summary(StorageManager(default_root(home), create=False), seconds=seconds)
    except (OSError, ValueError) as exc:
        return {'schema_version': 1, 'error': str(exc)}


def _target(args, state_root):
    target = resolve_target(args.repo, args.home, (args.platform or 'codex,claude').split(','))
    enrolled = next((row for row in records(state_root) if row['target_id'] == target['target_id']), None)
    if enrolled and args.platform is None:
        target = enrolled['target']
    return target


def main(argv=None):
    args = parser().parse_args(argv)
    # The bundled engine inherits its environment for read-only Git identity calls.
    clean = git_env()
    for key in list(os.environ):
        if key.startswith(('GIT_', 'SSH_', 'DYLD_', 'LD_')):
            os.environ.pop(key)
    os.environ.update(clean)
    try:
        if args.command == 'runtime-successor':
            from .runtime_successor import prepare_successor, inspect_successor, distribution
            if args.successor_action == 'prepare':
                result = prepare_successor(args.repository, args.platform, args.request_id, args.coordinator_fd)
            elif args.successor_action == 'inputs':
                from .runtime_successor import preparation_inputs
                result = preparation_inputs(args.repository,args.platform,args.session,args.request_id,json.loads(args.authority_ref))
            elif args.successor_action == 'recovery-inputs':
                from .runtime_successor_recovery import recovery_inputs
                result = recovery_inputs(args.repository,args.platform,args.session,args.request_id,
                    json.loads(args.authority_ref),json.loads(args.handoff_ref))
            elif args.successor_action == 'origin':
                from .runtime_harness_start import inspect_origin
                result = inspect_origin(
                    args.repository, args.platform, args.session, allow_absent=args.allow_absent,
                )
            elif args.successor_action == 'activate':
                from .runtime_successor_activation import activate_successor
                result = activate_successor(args.repository, args.platform, args.request_id, args.coordinator_fd)
            elif args.successor_action == 'inspect':
                distribution(args.platform)  # Requires the genuine managed issuer context.
                result = inspect_successor(args.request_id)
            else:
                _, verified = distribution(args.platform)
                result = {'distribution_identity': verified['manifest']['closure_digest']}
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == 'storage':
            from .storage_lifecycle import StorageManager, default_root, describe_prune, prune
            if not 0 < args.seconds <= 3600:
                raise ValueError('--seconds must be in (0, 3600]')
            root = Path(args.root).expanduser().absolute() if args.root else default_root()
            result = prune(StorageManager(root, create=False), apply=args.apply, seconds=args.seconds)
            print(json.dumps(result, indent=2, sort_keys=True) if args.json else '\n'.join(describe_prune(result)))
            return 0
        if args.command == 'service':
            from .runtime_pin_transaction import storage
            from .runtime_service_boot import control
            try:
                root = storage(readonly=True)
            except (OSError, ValueError):
                result = {'action': args.action, 'services': []}
            else:
                result = control(root, args.action, args.repository)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == 'run':
            from .runtime_run import run
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            return run(args.repository, args.platform, args.session, arguments, existing_run=args.existing_run, private_session=args.session_directory)
        if args.command in {'source-proof','planning-proof'}:
            from .terminal_source import terminal_source,planning_source
            query=terminal_source if args.command=='source-proof' else planning_source
            result = query(args.repository, session=args.session, run=args.run, platform=args.platform)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command in {'replan-proof','compatibility-readiness'}:
            from .policy_source import policy_source
            values={name:getattr(args,name,None) for name in ('proposal','proposal_sha256','expected_manifest','replan_proof','replan_proof_sha256','self_area')}
            result=policy_source(args.repository,session=args.session,run=args.run,platform=args.platform,
                operation='replan' if args.command=='replan-proof' else 'readiness',issuer_binding=args.issuer_binding,
                issuer_binding_sha256=args.issuer_binding_sha256,**values)
            print(json.dumps(result,sort_keys=True));return 0
        state_root = Path(args.state_root).expanduser()
        if args.command == 'list':
            rows = records(state_root)
            result = {'installations': [{'target_id': row['target_id'], 'target': row['target'],
                       'version': display_version(row['current']['release']) if row.get('current') else None,
                       'policy': row['policy'], 'pending': row.get('pending'), 'source': row.get('source')}
                      for row in rows], 'legacy': legacy_records(state_root)}
        elif args.command == 'bootstrap':
            errors = prerequisites()
            if errors:
                raise ValueError('; '.join(errors))
            result = bootstrap_cli(args.source_root, args.destination, state_root)
        elif args.command == 'prune-stale-runs':
            from .runtime_stale_runs import prune_stale_runs
            result = prune_stale_runs(state_root, apply=args.apply)
        elif args.command == 'doctor':
            selection = None
            if args.repo or args.global_scope:
                target = resolve_target(args.repo, args.home)
                selection = {target['target_id']}
            result = doctor(selection, state_root, runtime_roots=args.runtime_root)
            result['storage'] = storage_report(args.home)
            if result['storage'].get('retained'):
                storage = result['storage']
                result['warnings'].append(
                    f"{storage['retained']} managed scratch allocation(s) retained "
                    f"({storage['retained_allocated_bytes_estimate']} bytes measured in "
                    f"{storage['measured_retained']}; estimate {storage['estimated_retained_bytes']}); "
                    'preview reclaimable ones with `alctl storage prune`')
        else:
            errors = prerequisites()
            if errors:
                raise ValueError('; '.join(errors))
            reviews = read_json(args.reviews) if args.reviews else {}
            adoption = read_json(args.adopt) if args.adopt else {}
            options = {'reviews': reviews, 'adoption': adoption, 'runtime_roots': args.runtime_root}
            if args.command == 'update' and args.allow_running:
                if (not args.global_scope or args.all or args.repo or args.adopt or args.documentation_only
                        or args.reviewed_runtime_update or not args.reviews):
                    raise ValueError('--allow-running requires --global/--reviews and forbids batch, repo, adoption and mixed modes')
                from .operator_update import reference
                from .registry import digest
                from .validator_origin import capture, decode
                review_path = Path(args.reviews)
                if not review_path.is_absolute():
                    raise ValueError('--allow-running --reviews must be an absolute path')
                artifact = {'path': str(review_path), 'sha256': digest(capture(review_path)[0])}
                reviews = decode(reference(artifact))
                options.update(allow_running=True, reviews=reviews, operator_review_artifact=artifact)
            if args.command == 'update' and args.reviewed_runtime_update:
                if not args.global_scope or args.all or args.repo or args.adopt or args.documentation_only or not args.reviews:
                    raise ValueError('--reviewed-runtime-update requires --global/--reviews and forbids batch, repo, adoption and mixed modes')
                from .registry import digest
                from .validator_origin import capture
                review_path = Path(args.reviews).expanduser().absolute()
                options.update(reviewed_runtime_update=True,
                               runtime_review_artifact={'path': str(review_path), 'sha256': digest(capture(review_path)[0])})
            if args.command == 'update' and args.documentation_only:
                if not args.global_scope or args.all or args.repo or args.adopt:
                    raise ValueError('--documentation-only requires --global and forbids --all, --repo and --adopt')
                options['documentation_only'] = True
            if args.command in {'install', 'update'}:
                options.update(source=args.source, source_subdir=args.source_subdir, version=args.version,
                               policy=args.policy, status_line=args.status_line)
            if args.command == 'update' and args.all:
                if args.repo or args.global_scope or args.version or args.platform or args.policy or args.source or args.source_subdir:
                    raise ValueError('--all uses enrolled sources/platforms/policies; select a target to override a version or policy')
                result = update_registered(None, state_root, **options)
            else:
                target = _target(args, state_root)
                if args.command == 'rollback':
                    result = rollback_previous(target, state_root, **options)
                else:
                    result = run_operation(dict(options, target=target, kind=args.command), state_root)
        print(json.dumps(result, indent=2, sort_keys=True))
        if isinstance(result, list):
            return 1 if any(row.get('status') in {'failed', 'deferred'} for row in result) else 0
        return 1 if result.get('ok') is False else 0
    except ActiveRuns as exc:
        print(json.dumps({'status': 'deferred', 'error': str(exc)}, indent=2))
        return 3
    except (ValueError, OSError, KeyError, TypeError, SyntaxError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, indent=2))
        return 1
