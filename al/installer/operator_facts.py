"""Verified standalone facts dispatch, advisory only and independent of AL runs."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys

from . import operator_facts_source as source
from .validator_origin import interpreter_identity, verify_snapshot
from .validator_loader import verify_loaded_modules

MAX_OUTPUT = 32 << 20


def managed_context():
    context = sys.modules.get('_al_validator_context')
    root = Path(__file__).resolve().parents[1]
    if (context is None or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
            or context.request.get('mode') != 'snapshot' or Path(context.request['root']) != root):
        raise ValueError('operator facts require the genuine isolated managed snapshot')
    verified = verify_snapshot(root, context.request['expected'])
    captured = context.captured
    if (captured['bytes'] != verified['bytes'] or captured['origin'] != verified['origin']
            or interpreter_identity() != verified['origin']['interpreter']
            or context.request['interpreter'] != verified['origin']['interpreter']['executable']):
        raise ValueError('operator facts managed interpreter or source binding changed')
    loader = sys.modules[__name__].__loader__
    if (getattr(loader, 'captured', None) is not captured or loader.platform != 'codex'
            or not loader.installers or loader.root != root):
        raise ValueError('operator facts require the installed captured-byte loader')
    verify_loaded_modules(loader)
    return captured, loader


@contextmanager
def private_execution(captured_source):
    """Install routing before any shared import (al_store queries Git on import)."""
    previous = dict(os.environ)
    cwd = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.environ.clear()
        os.environ.update(captured_source.environment)
        os.chdir(captured_source.work)
        yield
    finally:
        try:
            os.fchdir(cwd)
        finally:
            os.close(cwd)
            os.environ.clear()
            os.environ.update(previous)


def canonical_output(value):
    chunks, size = [], 1
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    for text in encoder.iterencode(value):
        raw = text.encode('utf-8')
        size += len(raw)
        if size > MAX_OUTPUT:
            raise ValueError('operator facts output capacity exceeded')
        chunks.append(raw)
    return b''.join(chunks) + b'\n'


def run(repository, action, *, paths=(), target=None, commit=None, limit=200):
    captured, loader = managed_context()
    source.revision(commit)
    repository = Path(repository).expanduser()
    if not repository.is_absolute() or '..' in repository.parts:
        raise ValueError('operator repository must be absolute without traversal')
    if len(paths) > 4096:
        raise ValueError('too many operator facts paths')
    paths = tuple(source.relative(path) for path in paths)
    if action == 'consumers':
        if (not isinstance(target, str) or not target.strip() or len(target.encode()) > 4096
                or any(ord(c) < 32 for c in target) or '\\' in target or '..' in target.split('/')):
            raise ValueError('invalid operator facts consumer target')
        if target.startswith('file:'):
            source.relative(target[5:])
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('operator facts limit must be between 1 and 10000')
    elif action != 'edges':
        raise ValueError('unsupported operator facts action')
    with source.capture(repository, commit) as captured_source:
        with private_execution(captured_source):
            import al_runtime_resources as resources
            mapping = captured['origin']['platforms']['codex']['resources']
            resources._bind_verified_context({
                'identities': {name: str(Path(captured['origin']['source']['al_root']) / path)
                               for name, path in mapping.items()},
                'bytes': {name: captured['bytes'][path] for name, path in mapping.items()},
                'platform': 'codex', 'closure_digest': captured['origin']['closure_digest'],
                'origin_digest': captured['origin']['origin_digest']})
            import al_snapshot
            import al_code_facts
            import al_analysis_index
            with al_snapshot.using_operator(captured_source):
                try:
                    view = al_code_facts.View(repository, commit)
                    result = (al_code_facts.edge_rows(view, paths) if action == 'edges'
                              else al_code_facts.consumers(view, target, limit))
                    raw = canonical_output(result)
                    al_analysis_index.flush()
                    verify_loaded_modules(loader)
                    verify_snapshot(captured['snapshot'], captured['expected'])
                finally:
                    # Operator stores never register atexit flushes. Settle while routing is live.
                    al_analysis_index.reset()
                captured_source.fence()
    # No success bytes are published until fencing and owned scratch cleanup both succeed.
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
    return 0
