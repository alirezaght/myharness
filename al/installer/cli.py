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
    for command in ('install', 'list', 'update', 'doctor', 'rollback', 'bootstrap'):
        p = sub.add_parser(command)
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
        if command == 'rollback':
            p.add_argument('--previous', action='store_true', required=True)
        if command == 'bootstrap':
            p.add_argument('--source-root', default=str(Path(__file__).resolve().parents[1]))
            p.add_argument('--destination', default=str(Path.home() / '.local/bin/alctl'))
    return ap


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
        elif args.command == 'doctor':
            selection = None
            if args.repo or args.global_scope:
                target = resolve_target(args.repo, args.home)
                selection = {target['target_id']}
            result = doctor(selection, state_root, runtime_roots=args.runtime_root)
        else:
            errors = prerequisites()
            if errors:
                raise ValueError('; '.join(errors))
            reviews = read_json(args.reviews) if args.reviews else {}
            adoption = read_json(args.adopt) if args.adopt else {}
            options = {'reviews': reviews, 'adoption': adoption, 'runtime_roots': args.runtime_root}
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
