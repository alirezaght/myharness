"""Operator-only managed issuer and installation metadata checks; never pin authority."""
import os
from pathlib import Path
import stat
import sys
import threading

from .engine import R
from .registry import Held, namespace, record_path
from .targets import package_path
from .validator_origin import safe, verify_snapshot


def owned(path, *, directory=False):
    path = safe(path)
    info = path.stat()
    if (info.st_uid != os.getuid() or info.st_mode & 0o022
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        raise ValueError('operator installation metadata ownership/type/alias differs: ' + str(path))
    return info


def selected_registry(state_root):
    context = sys.modules.get('_al_validator_context')
    root = Path(__file__).resolve().parents[1]
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
            or context is None or context.request.get('mode') != 'snapshot'
            or sys.modules.get('_al_run_pin_context') is not None or Path(context.request['root']) != root):
        raise ValueError('--allow-running requires the genuine isolated managed installer launcher')
    if (root.parent.name != 'snapshots' or root.parent.parent.name != 'cli'
            or root.parent.parent.parent.name != 'installer' or root.parents[3] != R.safe_absolute(state_root)):
        raise ValueError('operator registry differs from the selected managed launcher')
    trusted = verify_snapshot(root, context.request['expected'])
    for path in (root, *root.parents[:4]):
        owned(path, directory=True)
    for name in [*trusted['origin']['closure'], 'origin.json', 'receipt.json']:
        owned(root / name)
    return {'snapshot': str(root), 'binding': trusted['expected']}


def lock_identity(state_root, held=None):
    path = namespace(state_root) / 'coordination.lock'
    before = owned(path)
    keys = ('st_dev', 'st_ino', 'st_uid', 'st_nlink', 'st_mode')
    if held is not None:
        if (type(held) is not Held or not held.active or held.root != namespace(state_root)
                or held.pid != os.getpid() or held.thread != threading.get_ident() or type(held.fd) is not int):
            raise ValueError('operator update requires the actual held coordination descriptor')
        opened = os.fstat(held.fd)
        if any(getattr(opened, key) != getattr(before, key) for key in keys):
            raise ValueError('operator held coordination lock differs from its owned path')
    return {key: getattr(before, key) for key in keys}


def installation_metadata(state_root, row):
    # Reuse the exact18 reader's bounded race/owner/0600/no-alias checks, not its
    # history observer or selected_storage (which would require a pin directory).
    from .reviewed_runtime_pins import metadata
    roots = [Path(state_root), namespace(state_root)]
    paths = [record_path(state_root, row['target_id'])]
    source = namespace(state_root) / 'sources'
    for directory in (namespace(state_root) / 'records', source):
        roots.append(directory)
        paths.extend(directory.glob('*.json'))
    from .registry import canonical, digest
    paths.append(source / (digest(canonical(row['source'])) + '.json'))
    current = row['current']
    for platform in row['target']['platforms']:
        expected = R.record_path(Path(state_root), str(package_path(row['target'], platform)), platform)
        if current['packages'][platform]['path'] != str(expected):
            raise ValueError('operator package ownership record escapes the selected registry')
        paths.append(expected)
    for name, role in current['roles'].items():
        expected = Path(state_root) / ('role-' + R.key(name, 'role') + '.json')
        if not role.get('sentinel') or role['sentinel']['path'] != str(expected):
            raise ValueError('operator role sentinel escapes the selected registry')
        paths.append(expected)
    for snapshot in (current, row.get('previous')):
        if snapshot:
            journal = R.safe_absolute(snapshot['journal'])
            if namespace(state_root) / 'operations' not in journal.parents:
                raise ValueError('operator completed journal escapes the selected registry')
            paths.append(journal)
    for path in set(paths):
        path = R.safe_absolute(path)
        if Path(state_root) not in path.parents:
            raise ValueError('operator metadata is outside the selected registry')
        metadata(path)
        roots.extend(parent for parent in path.parents if parent == Path(state_root) or Path(state_root) in parent.parents)
    for root in set(roots):
        owned(root, directory=True)


def preflight(state_root):
    selected = selected_registry(state_root)
    for path in (Path(state_root), namespace(state_root)):
        owned(path, directory=True)
    return {'issuer': selected, 'lock': lock_identity(state_root)}


def revalidate(state_root, held, row, prepared):
    if selected_registry(state_root) != prepared['issuer'] or lock_identity(state_root, held) != prepared['lock']:
        raise ValueError('operator issuer or coordination lock identity changed')
    installation_metadata(state_root, row)
    return prepared['issuer']
