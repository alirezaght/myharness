"""Fresh committed-state observation for fixture artifact construction only."""
import json
from pathlib import Path
import re

import al_schema
import al_store


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:raise ValueError('duplicate committed-state key')
        result[key] = value
    return result


def nonfinite(_value):raise ValueError('nonfinite committed state')


def stamp(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class StateObserver:
    def __init__(self, default_session=None):
        self.default_session = default_session
        self.runs = {}

    def _read(self, sid):
        if not al_schema.valid_id(sid):raise ValueError('invalid observation session')
        runtime = al_store.RUNTIME_DIR.resolve();path = runtime / (sid + '.json')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('missing or unsafe committed state')
        before = stamp(path);raw = path.read_bytes()
        state = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
        if not isinstance(state, dict) or state.get('session_id') != sid:
            raise ValueError('foreign observation session')
        identity = state.get('identity')
        if not isinstance(identity, dict) or not identity.get('root') or Path(identity['root']).resolve() != al_store.REPO_ROOT.resolve():
            raise ValueError('foreign observation repository')
        if path.is_symlink() or path.read_bytes() != raw or stamp(path) != before:
            raise ValueError('committed state changed during observation')
        return state

    def bind_started(self, stdout):
        sessions = re.findall(r'^AL session ([A-Za-z0-9_-]+)$', stdout, re.M)
        if len(sessions) != 1:raise ValueError('missing actual public start identity')
        state = self._read(sessions[0])
        if not al_schema.valid_id(state.get('run_id')):raise ValueError('invalid started run identity')
        self.runs[sessions[0]] = state['run_id']

    def read(self, session=None):
        pointer = None
        if session is None:
            session = self.default_session
            if session is None:
                current = al_store.CURRENT
                if current.is_symlink() or not current.is_file():raise ValueError('missing observation current pointer')
                pointer = current.read_bytes();session = pointer.decode().strip()
        state = self._read(session)
        if session not in self.runs or state.get('run_id') != self.runs[session]:
            raise ValueError('foreign or unbound observation run')
        try:al_schema.validate_state(state)
        except SystemExit as exc:raise ValueError('corrupt committed state: ' + str(exc)) from exc
        if self._read(session) != state:raise ValueError('committed state changed during validation')
        if pointer is not None and al_store.CURRENT.read_bytes() != pointer:
            raise ValueError('current pointer changed during observation')
        return state
