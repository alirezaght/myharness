"""Read-only original-history compatibility diagnostic; never deployment authority."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ''}:sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from al.installer import activity_history as H
from al.installer.activity_history import reviewed_history
from al.installer.activity_inventory import inventory_activity, repository_identity
from al.installer.activity_observation import locked_activity, observe_activity
from al.installer.activity_profiles import decode
from al.installer.registry import digest
from al.verification_profile import write_json

SOURCE = Path(__file__).resolve().parent.parent
PROTECTED = ContextVar('pilot_readonly_roots', default=())


def _audit(event, args):
    paths = PROTECTED.get()
    if not paths:return
    writing = event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.rename', 'os.chmod', 'os.chown', 'os.truncate', 'os.utime', 'os.link', 'os.symlink'}
    if event == 'open':writing = bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if not writing:return
    candidates = args[:2] if event in {'os.rename', 'os.link', 'os.symlink'} else args[:1]
    for raw in candidates:
        if not isinstance(raw, (str, bytes, os.PathLike)):continue
        path = Path(os.fsdecode(raw)).absolute()
        if any(path == root or root in path.parents for root in paths):
            raise PermissionError('read-only activity pilot refused a runtime mutation')


sys.addaudithook(_audit)


@contextmanager
def readonly(roots):
    token = PROTECTED.set(tuple(Path(root).resolve() for root in roots))
    try:yield
    finally:PROTECTED.reset(token)


def _index(repository):
    dot = Path(repository) / '.git'
    if dot.is_symlink():raise ValueError('unsafe Git metadata boundary')
    if dot.is_dir():directory = dot
    elif dot.is_file():
        raw = dot.read_text().strip()
        if not raw.startswith('gitdir: ') or '\n' in raw:raise ValueError('unsupported worktree metadata')
        directory = (dot.parent / raw[8:]).resolve()
    else:raise ValueError('missing Git worktree metadata')
    path = directory / 'index'
    if path.is_symlink():raise ValueError('unsafe Git index boundary')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
            'mode': path.stat().st_mode & 0o777 if path.exists() else None}


def inspect_history(scope_reference, history_references, *, session, run, platform='codex', orphan_review=None):
    raw = Path(scope_reference['path']).read_bytes()
    if scope_reference['sha256'] != H.ANCHORS['scope'] or digest(raw) != H.ANCHORS['scope']:
        raise ValueError('pilot requires the original complete runtime scope')
    scope = decode(raw)
    requests = {(row['repo'], row['platform'], str(Path(row['path']).resolve().parent)) for row in scope['rows']}
    indexes_before = {repo: _index(repo) for repo, _, _ in requests}
    protected = [runtime for _, _, runtime in requests]
    with readonly(protected):
        roots = [{'repository': repository_identity(repo), 'platform': host, 'runtime_root': runtime}
                 for repo, host, runtime in sorted(requests)]
        repo = repository_identity(SOURCE)
        invoking = {'repository': repo, 'platform': platform, 'runtime_root': str(SOURCE / ('.' + platform) / 'al-runtime'),
                    'state_path': str(SOURCE / ('.' + platform) / 'al-runtime' / (session + '.json')),
                    'session_id': session, 'run_id': run}
        inventory = inventory_activity(roots, invoking_run=invoking, orphan_review=orphan_review)
        errors = [error for root in inventory['roots'] for error in root['errors']]
        records = [record for root in inventory['roots'] for record in root['records']]
        errors.extend(error for record in records for error in record['errors'])
        history = None;observation = None
        try:history = reviewed_history(inventory, history_references)
        except (OSError, ValueError, KeyError, TypeError) as exc:errors.append(str(exc))
        if inventory['stable']:
            try:
                with locked_activity(inventory) as (locks, limitations):
                    observation = observe_activity(inventory, locks=locks, limitations=limitations)
                    errors.extend(observation['errors'])
            except (OSError, ValueError, KeyError, TypeError) as exc:errors.append(str(exc))
        current = [record for record in records if record['identity'] == invoking]
        current_blocked = bool(len(current) == 1 and (current[0]['historical_status'] != 'APPROVED' or current[0]['historical_phase'] != 'DONE'))
        other_process_blockers = [] if observation is None else [row for row in observation['processes']
            if row['record_identity'] != invoking and row['result'] not in {'absent', 'different-process'}]
        historical_operations = []
        for record in records:
            if record['identity'] == invoking:continue
            facts = record['operation_facts']
            if any(w['state'] in {'ACTIVE', 'UNRESOLVED'} for w in facts.get('writers', {}).values()):
                historical_operations.append({'identity': record['identity'], 'reason': 'active/unresolved writer'})
            from al.installer.activity_operations import check_blockers
            for check in facts.get('checks', []):
                for reason in check_blockers(check, facts.get('check_reconciliations', {}).get(check['id'])):
                    historical_operations.append({'identity': record['identity'], 'reason': reason})
        if observation:
            historical_operations.extend(o for o in observation['operations'] if o['identity'] != invoking and o['result'] != 'quiescent')
        if historical_operations:errors.append('historical unresolved operation remains blocking')
        if other_process_blockers:errors.append('noncurrent live/unknown recorded process remains blocking')
        if observation and observation['leases']:errors.append('live/unknown lease remains blocking')
        if not current_blocked:errors.append('pilot requires the known nonterminal current-run exclusion; it grants no terminal eligibility')
        if len(records) != len(scope['rows']):errors.append('original top-level runtime artifact count changed')
        indexes_after = {repo: _index(repo) for repo, _, _ in requests}
        if indexes_after != indexes_before:errors.append('Git index changed during read-only pilot')
    return {'schema_version': 1, 'kind': 'read-only-original-history-pilot', 'status': 'PASS' if not errors and inventory['stable'] else 'FAIL',
            'eligibility_granted': False, 'acknowledgements_published': False, 'runtime_mutations_authorized': False,
            'expected_current_run_blocker': current_blocked, 'records': len(records),
            'sessions': sum(r['classification'] in {'current-session', 'legacy-session'} for r in records),
            'orphan_locks': sum(len(root['orphan_locks']) for root in inventory['roots']),
            'nonstate': sum(r['classification'] == 'nonstate-artifact' for r in records),
            'unknown': sum(r['classification'] == 'unknown' for r in records), 'errors': errors,
            'inventory': inventory, 'observation': observation, 'historical_operation_blockers': historical_operations, 'reviewed_history_candidate': history,
            'indexes_before': indexes_before, 'indexes_after': indexes_after,
            'limitations': ['Diagnostic observations are not reusable quiescence or deployment admission authority.',
                           'The supervising ledger legitimately updates its own current-run state/log outside the read-only pilot process.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope-file', required=True);parser.add_argument('--states-file', required=True)
    parser.add_argument('--nonstate-file', required=True);parser.add_argument('--session', required=True)
    parser.add_argument('--orphan-locks-file');parser.add_argument('--expect-run', required=True);parser.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    report = Path(args.report).resolve()
    if report == SOURCE or SOURCE in report.parents:raise ValueError('pilot report must be outside source')
    references = {key: {'path': getattr(args, name), 'sha256': H.ANCHORS[key]}
                  for key, name in [('scope', 'scope_file'), ('states', 'states_file'), ('nonstate', 'nonstate_file')]}
    from al.installer.activity_orphans import REVIEW_SHA
    orphan_review = {'path': args.orphan_locks_file, 'sha256': REVIEW_SHA} if args.orphan_locks_file else None
    result = inspect_history(references['scope'], references, session=args.session, run=args.expect_run, orphan_review=orphan_review)
    if any(report == Path(root['repository']['root']) or Path(root['repository']['root']) in report.parents for root in result['inventory']['roots']):
        raise ValueError('pilot report must be outside target repositories')
    write_json(report, result)
    print(json.dumps({k: result[k] for k in ('status', 'records', 'sessions', 'nonstate', 'orphan_locks', 'unknown', 'eligibility_granted', 'expected_current_run_blocker')}))
    print(json.dumps({'report': str(report), 'sha256': digest(report.read_bytes()), 'errors': result['errors'][:8]}))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':raise SystemExit(main())
