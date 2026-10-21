"""Reviewed residual al_store.locked(sid) artifacts, never synthetic sessions."""
import hashlib
import os
from pathlib import Path
import re
import stat

from .activity_profiles import decode
from .engine import R
from .registry import digest

REVIEW_SHA = '3e1550a21f01b42ad364f0b655b729f25d226215106e819d391c122ce9977ede'
PROFILE = {'id': 'orphan-al-session-lock-v1', 'version': 1}
NAME = re.compile(r'\.(al-\d{8}T\d{6}Z-[0-9a-f]{6})\.lock\Z')
EMPTY = hashlib.sha256(b'').hexdigest()


def physical(path, descriptor=None):
    path = R.safe_absolute(path)
    own = descriptor is None
    if own:descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        disk = path.stat()
        if (info.st_dev, info.st_ino) != (disk.st_dev, disk.st_ino):raise ValueError('orphan lock inode changed')
        if not stat.S_ISREG(info.st_mode) or info.st_size or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ValueError('unsupported orphan lock physical identity')
        if os.pread(descriptor, 1, 0):raise ValueError('nonempty orphan lock')
        return {'device': info.st_dev, 'inode': info.st_ino, 'mode': stat.S_IMODE(info.st_mode),
                'uid': info.st_uid, 'gid': info.st_gid, 'nlink': info.st_nlink, 'size': info.st_size,
                'type': 'file', 'sha256': EMPTY}
    finally:
        if own:os.close(descriptor)


def review(reference):
    if not reference:return []
    if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256'} or reference['sha256'] != REVIEW_SHA):
        raise ValueError('unsupported reviewed orphan lock inventory')
    raw = R.safe_absolute(reference['path']).read_bytes()
    if digest(raw) != REVIEW_SHA:raise ValueError('reviewed orphan lock inventory changed')
    value = decode(raw)
    if value.get('schema_version') != 1 or value.get('kind') != 'reviewed-orphan-session-lock-inventory':
        raise ValueError('unsupported reviewed orphan inventory profile')
    rows = value['records']
    if not isinstance(rows, list) or len({r['path'] for r in rows}) != len(rows):raise ValueError('duplicate orphan lock review')
    return rows


def subjects(root, rows, invoking, reference):
    result = []
    for row in rows:
        if row['runtime_root'] != root['runtime_root']:continue
        path = Path(row['path']);match = NAME.fullmatch(path.name)
        if (not match or path.parent != Path(root['runtime_root']) or row['repository'] != root['repository']
                or row['platform'] != root['platform'] or match[1] != row['session_id'] or row['profile'] != PROFILE
                or row['associated_state_absent'] is not True or row['associated_session_directory_absent'] is not True
                or row['associated_pointers'] or row['attributed_members'] != [path.name]):
            raise ValueError('unsupported reviewed orphan lock subject')
        sid = row['session_id']
        if root['repository'] == invoking['repository'] and sid == invoking['session_id']:
            raise ValueError('orphan lock collides with invoking run')
        if (any(r['identity']['session_id'] == sid for r in root['records'])
                or any(p['session_id'] == sid for p in root['pointers'])
                or any(sid in m['relative_path'] and m['relative_path'] != path.name for m in root['members'])):
            raise ValueError('orphan session material appeared')
        observed = physical(path)
        if observed != row['fingerprint'] or observed['mode'] != 0o644:raise ValueError('reviewed orphan lock fingerprint changed')
        member = next((m for m in root['members'] if m['relative_path'] == path.name), None)
        if member != row['pilot_membership']:raise ValueError('reviewed orphan lock membership changed')
        identity = {k: root[k] for k in ('repository', 'platform', 'runtime_root')}
        identity.update(lock_path=str(path), session_id=sid)
        result.append({'identity': identity, 'profile': PROFILE, 'fingerprint': observed,
                       'reviewed_provenance': reference})
    return result


def process_subjects(inventory):
    return [('session', r['identity']) for root in inventory['roots'] for r in root['records']
            if r['classification'] != 'nonstate-artifact'] + [
                ('orphan-lock', subject['identity']) for root in inventory['roots'] for subject in root.get('orphan_locks', [])] + [
                    ('runtime-root', {k: root[k] for k in ('repository', 'platform', 'runtime_root')}) for root in inventory['roots']]
