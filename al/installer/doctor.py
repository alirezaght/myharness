"""Side-effect-free installation diagnostics; never calls engine verify/locks."""
import re
import shutil
import subprocess
import sys
from pathlib import Path
from .engine import R
from .guards import activity_roots, scan_activity
from .operations import check_current
from .registry import legacy_records, namespace, read_json, records, reject_pending
from .source import git_env
from .payload_provenance import display_version
from .targets import validate_target


def prerequisites():
    errors = []
    if sys.version_info < (3, 11):
        errors.append('Python 3.11 or later required')
    if not shutil.which('git'):
        errors.append('Git 2.30 or later required')
    else:
        result = subprocess.run(['git', '--version'], env=git_env(), capture_output=True, text=True, timeout=10)
        match = re.search(r'git version (\d+)\.(\d+)', result.stdout)
        if result.returncode or not match or tuple(map(int, match.groups())) < (2, 30):
            errors.append('Git 2.30 or later required')
    return errors


def doctor(selection=None, state_root=None, *, runtime_roots=(), invoking=None):
    state_root = R.safe_absolute(state_root)
    checks, errors = [], prerequisites()
    try:
        rows = records(state_root)
        selected = rows if selection is None else [row for row in rows if row['target_id'] in selection]
        if selection is not None and len(selected) != len(selection):
            errors.append('selected target is not enrolled')
        legacy = legacy_records(state_root)
        selected_ids = {row['target_id'] for row in selected}
        for path in (namespace(state_root) / 'operations').glob('*/journal.json'):
            journal = read_json(path)
            if (selection is None or journal.get('target_id') in selected_ids or journal.get('target_id') in (selection or set())) and journal.get('status') != 'COMPLETE':
                errors.append('incomplete/orphan installer journal: ' + str(path))
        for row in selected:
            try:
                validate_target(row['target'], state_root, (row.get('source') or {}).get('identity'))
                reject_pending(row, state_root)
                if not row.get('current'):
                    raise ValueError('installation has no completed version')
                check_current(row, state_root)
                checks.append({'target_id': row['target_id'], 'status': 'PASS', 'version': display_version(row['current']['release'])})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                checks.append({'target_id': row['target_id'], 'status': 'FAIL', 'error': str(exc)})
                errors.append(str(exc))
        activity = scan_activity(activity_roots(rows, explicit=runtime_roots, invoking=invoking))
        errors.extend(item['path'] + ': ' + item['reason'] for item in activity['blockers'])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        errors.append(str(exc))
        activity = {'roots': [], 'blockers': [], 'limitation': 'Registry unreadable; activity coverage unavailable.'}
        legacy = []
    return {'ok': not errors, 'checks': checks, 'errors': errors, 'legacy': legacy, 'activity': activity}
