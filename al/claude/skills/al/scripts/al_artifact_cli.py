"""Record immutable supporting bytes and named agent reports without editing source."""
import json
import os
from pathlib import Path
import re
import stat
import sys

import al_artifacts as A
import al_store
from al_schema import refuse

LIMIT = 16 * 1024 * 1024


def read_input(a):
    if getattr(a, 'stdin', False):
        raw = sys.stdin.buffer.read(LIMIT + 1)
        source = 'explicit standard input'
    else:
        path = Path(a.file).resolve(strict=True)
        if Path(a.file).is_symlink(): refuse('artifact input cannot be a symlink')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                refuse('artifact input must be an ordinary file with one link')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(LIMIT + 1)
        finally: os.close(fd)
        source = str(path)
    if not raw or len(raw) > LIMIT: refuse('artifact input must be nonempty and at most 16 MiB')
    return raw, source


def cmd_artifact(a, sid):
    st = al_store.load(sid)
    if a.action == 'show':
        record = st.get('agent_outputs', {}).get(a.name)
        if not record: refuse('unknown named agent output: ' + a.name)
        A.read(record['artifact'])
        print(json.dumps(record, sort_keys=True))
        return
    if a.action == 'record':
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}', a.kind):
            refuse('artifact kind must be a short lowercase identifier')
        raw, source = read_input(a)
        ref = A.snapshot_bytes(sid, raw, a.kind, source)
        st.setdefault('recorded_artifacts', []).append(ref)
        al_store.save(sid, st)
        print(json.dumps(ref, sort_keys=True))
        return
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.(?:json|md|txt)', a.name):
        refuse('named output must be a simple JSON, Markdown or text basename')
    raw, source = read_input(a)
    if a.name.endswith('.json'):
        import al_proof_json as J
        J.loads(raw)
    prior = st.get('agent_outputs', {}).get(a.name)
    if prior:
        if A.digest(raw) == prior['artifact']['sha256']:
            A.read(prior['artifact']);print(json.dumps(prior, sort_keys=True));return
        if a.expected_sha256 != prior['artifact']['sha256']:
            refuse('named output already exists; provide its current --expected-sha256 to publish a new version')
    elif a.expected_sha256:
        refuse('named output has no previous version for --expected-sha256')
    ref = A.snapshot_bytes(sid, raw, 'agent-output', source)
    directory = al_store.RUNTIME_DIR / sid / 'outputs'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = Path(a.name)
    path = directory / (name.stem + '-' + ref['sha256'][:12] + name.suffix)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != raw: refuse('named output path conflicts')
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
    record = {'name': a.name, 'path': str(path), 'artifact': ref}
    if prior: st.setdefault('agent_output_history', []).append(prior)
    st.setdefault('agent_outputs', {})[a.name] = record
    al_store.save(sid, st)
    print(json.dumps(record, sort_keys=True))


def register(sub):
    children = sub.add_parser('artifact', help='immutable references and named agent output files').add_subparsers(dest='action', required=True)
    p = children.add_parser('record');p.add_argument('--file', required=True)
    p.add_argument('--kind', required=True);p.set_defaults(fn=cmd_artifact)
    p = children.add_parser('output');p.add_argument('--name', required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--file');source.add_argument('--stdin', action='store_true')
    p.add_argument('--expected-sha256');p.set_defaults(fn=cmd_artifact)
    p = children.add_parser('show');p.add_argument('--name', required=True);p.set_defaults(fn=cmd_artifact)
