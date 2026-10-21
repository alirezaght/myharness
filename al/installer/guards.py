"""Read-only ownership and bounded, conservative runtime inspection."""
import os
import re
from pathlib import Path
from .engine import D, R, T
from .registry import read_json
from .targets import package_path, review_for, roles_path, target_descriptor

LIMITATION = ('Activity coverage is bounded to registered/invoking repositories and explicit runtime roots; '
              'unregistered legacy runs may be outside coverage. Legacy runtime starts do not share the installer lock.')


class ActiveRuns(ValueError):
    pass


def scan_activity(roots):
    inspected, blockers = [], []
    for entry in roots:
        root, expected_repo = entry if isinstance(entry, tuple) else (entry, None)
        root = Path(root).expanduser().absolute()
        if str(root) in inspected:
            continue
        inspected.append(str(root))
        try:
            R.safe_absolute(root)
            if not root.exists():
                continue
            if not root.is_dir():
                raise ValueError('runtime root is not a directory')
            paths = sorted(root.glob('*.json'))
            if len(paths) > 10000:
                raise ValueError('runtime session scan limit exceeded')
            current = root / 'current'
            R.safe_absolute(current)
            if current.exists():
                if not current.is_file():
                    raise ValueError('invalid runtime current pointer')
                sid = current.read_text().strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]+', sid) or not (root / (sid + '.json')).is_file():
                    raise ValueError('malformed or dangling runtime current pointer')
            for path in paths:
                try:
                    st = read_json(path)
                    if not isinstance(st, dict) or st.get('schema_version') != 2:
                        raise ValueError('unknown runtime schema')
                    if st.get('session_id') != path.stem or not re.fullmatch(r'[A-Za-z0-9_-]+', st.get('run_id', '')):
                        raise ValueError('invalid session/run identity')
                    identity = st.get('identity')
                    if not isinstance(identity, dict) or not identity.get('root') or not Path(identity['root']).is_absolute():
                        raise ValueError('missing repository identity')
                    if expected_repo and R.safe_absolute(identity['root']) != R.safe_absolute(expected_repo):
                        raise ValueError('runtime repository identity mismatch')
                    for key in ('review_iteration', 'correction_iteration', 'handoffs'):
                        if type(st.get(key)) is not int or st[key] < 0:
                            raise ValueError('malformed runtime counter: ' + key)
                    for key in ('task_package', 'phase_timings'):
                        if not isinstance(st.get(key), dict):
                            raise ValueError('malformed runtime field: ' + key)
                    if not re.fullmatch(r'[0-9a-f]{64}', st['task_package'].get('sha256', '')):
                        raise ValueError('malformed task package identity')
                    for key in ('completed_phases', 'phase_occurrences', 'review_history', 'amendments'):
                        if not isinstance(st.get(key), list):
                            raise ValueError('malformed runtime field: ' + key)
                    for occurrence in st['phase_occurrences']:
                        if not isinstance(occurrence, dict) or type(occurrence.get('completed')) is not bool or (occurrence['completed'] and occurrence.get('ended') is None):
                            raise ValueError('malformed phase completion proof')
                    writers = st.get('writers')
                    evidence = st.get('evidence')
                    if not isinstance(writers, dict) or not isinstance(evidence, dict) or not isinstance(evidence.get('entries'), list):
                        raise ValueError('malformed writer/evidence state')
                    if not isinstance(evidence.get('plan'), dict) or type(evidence.get('seq')) is not int or evidence['seq'] < len(evidence['entries']):
                        raise ValueError('malformed evidence ledger')
                    if any(not isinstance(w, dict) or w.get('id') != wid or w.get('state') not in {'FINISHED', 'RECONCILED'} for wid, w in writers.items()):
                        raise ValueError('active or unresolved writer')
                    if any(not isinstance(e, dict) or e.get('status') not in {'PASS', 'FAIL', 'EMPTY', 'INFRA', 'TORN'} for e in evidence['entries']):
                        raise ValueError('running or malformed evidence')
                    for entry in evidence['entries']:
                        if entry.get('run') != st['run_id'] or not re.fullmatch(r'E[1-9][0-9]*', entry.get('id', '')) or not isinstance(entry.get('argv'), list) or not entry.get('reservation'):
                            raise ValueError('malformed evidence identity')
                    if st.get('status') != 'APPROVED' or st.get('phase') != 'DONE':
                        raise ValueError('potentially resumable run: ' + str(st.get('status')) + '/' + str(st.get('phase')))
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    blockers.append({'path': str(path), 'reason': str(exc)})
        except (ValueError, OSError, TypeError) as exc:
            blockers.append({'path': str(root), 'reason': str(exc)})
    return {'roots': inspected, 'blockers': blockers, 'limitation': LIMITATION}


def activity_roots(rows, target=None, explicit=(), invoking=None):
    repos = {row['target']['root'] for row in rows if row['target']['scope'] == 'repo'}
    if target and target['scope'] == 'repo':
        repos.add(target['root'])
    identity = D.repo_identity(Path(invoking or Path.cwd()))
    if identity.get('git_dir'):
        repos.add(identity['root'])
    roots = [(Path(repo) / ('.' + p) / 'al-runtime', repo) for repo in sorted(repos) for p in ('codex', 'claude')]
    roots.extend((Path(path), None) for row in rows for path in row.get('runtime_roots', []))
    roots.extend((Path(path), None) for path in explicit)
    if os.environ.get('AL_RUNTIME_DIR'):
        roots.append((Path(os.environ['AL_RUNTIME_DIR']), None))
    return roots


def require_inactive(roots):
    report = scan_activity(roots)
    if report['blockers']:
        raise ActiveRuns('active/unknown AL runtime state: ' + '; '.join(x['path'] + ': ' + x['reason'] for x in report['blockers']))
    return report


def strict_package(target, platform, desired, state_root, adoption=None):
    descriptor = target_descriptor(target, platform)
    root = package_path(target, platform)
    existing = D.managed(root) if root.exists() else {}
    owned, _, record, _ = R.prior(Path(state_root), descriptor, existing, adoption if adoption and 'targets' in adoption else None)
    for name, item in owned.items():
        if T.state(R.safe_absolute(root / R.relative(name))) != item:
            raise ValueError('managed resource drift: ' + str(root / name))
    for name in desired:
        path = root / R.relative(name)
        R.safe_absolute(path)
        if T.state(path) is not None and name not in owned:
            raise ValueError('unmanaged resource collision; reviewed adoption required: ' + str(path))
    return descriptor, owned, record


def check_instructions(target, reviews):
    identity = dict(target['identity'])
    results = {}
    for platform in target['platforms']:
        package = package_path(target, platform)
        for path in (package, roles_path(target, platform), Path(target['root']) / ('.' + platform)):
            results[str(path)] = review_for(path, identity, reviews)
    return results


def resource_adoption(path, actual, adoption):
    row = (adoption or {}).get('resources', {}).get(str(path))
    if not isinstance(row, dict) or not row.get('reviewed') or not row.get('reason') or row.get('state') != actual:
        raise ValueError('unmanaged resource collision; reviewed adoption required: ' + str(path))
    return row
