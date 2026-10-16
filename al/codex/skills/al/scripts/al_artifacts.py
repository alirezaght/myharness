"""Immutable content-addressed runtime artifacts."""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
import al_store
from al_schema import refuse

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def snapshot_bytes(sid, raw, kind, source):
    sha = digest(raw)
    directory = al_store.RUNTIME_DIR / sid / 'artifacts'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / sha
    if target.exists() and target.read_bytes() != raw:
        refuse('artifact collision/tampering')
    if not target.exists():
        fd, tmp = tempfile.mkstemp(dir=directory)
        with os.fdopen(fd, 'wb') as out: out.write(raw)
        os.replace(tmp, target)
    return {'id': sha, 'kind': kind, 'sha256': sha, 'stored_path': str(target),
            'source': str(source), 'captured_at': time.time()}

def snapshot_artifact(sid, path, kind):
    return snapshot_bytes(sid, Path(path).read_bytes(), kind, path)

def read(ref):
    raw = Path(ref['stored_path']).read_bytes()
    if digest(raw) != ref['sha256']: refuse('immutable artifact was altered: ' + ref['kind'])
    return raw

def read_json(ref):
    return json.loads(read(ref))
