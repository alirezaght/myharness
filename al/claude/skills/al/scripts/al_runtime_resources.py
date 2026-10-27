"""Closed runtime resource identities, separate from physical module origin."""
import hashlib
import os
from pathlib import Path
from types import MappingProxyType

_CONTEXT = None
_USED = False


def _bind_verified_context(context):
    global _CONTEXT
    if _CONTEXT is not None or _USED:
        raise ValueError('runtime resources already initialized')
    required = {'identities', 'bytes', 'platform', 'closure_digest', 'origin_digest'}
    if set(context) not in (required, required | {'original_execution_origin'}) or context['platform'] not in {'codex', 'claude'}:
        raise ValueError('invalid verified runtime resource context')
    if set(context['identities']) != set(context['bytes']):
        raise ValueError('incomplete verified resource context')
    original = context.get('original_execution_origin')
    if original is not None and (not isinstance(original, dict) or set(original) != {'platform','closure_digest','origin_digest','physical_scripts_root'} or original['platform'] != context['platform']):
        raise ValueError('invalid verified original execution origin')
    if original is not None:
        context = dict(context, original_execution_origin=MappingProxyType(dict(original)))
    _CONTEXT = dict(context, identities=MappingProxyType(dict(context['identities'])),
                    bytes=MappingProxyType(dict(context['bytes'])))


def _ordinary():
    root = Path(__file__).resolve().parent
    paths = {p.name:p for p in root.glob('*.py')}
    paths.update({'reference/'+p.relative_to(root.parent/'reference').as_posix():p
                  for p in (root.parent/'reference').rglob('*') if p.is_file()})
    return paths


def _require(name):
    global _USED
    _USED = True
    names = _CONTEXT['bytes'] if _CONTEXT else _ordinary()
    if name not in names or not isinstance(name, str):
        raise ValueError('unknown runtime resource')
    return names[name]


def resource_identity(name):
    value = _require(name)
    return _CONTEXT['identities'][name] if _CONTEXT else str(value.resolve())


def resource_bytes(name):
    value = _require(name)
    return value if _CONTEXT else value.read_bytes()


_DIGEST = None


def implementation_digest():
    """Digest of the runtime's al_*.py modules; recomputed whenever any module's file identity changes."""
    global _USED, _DIGEST
    _USED = True
    if _CONTEXT:
        # Verified context bytes are immutable for the life of the process.
        key = ('context', id(_CONTEXT['bytes']))
        names = _CONTEXT['bytes']
    else:
        root = Path(__file__).resolve().parent
        with os.scandir(root) as listing:
            found = sorted(entry.name for entry in listing if entry.name.startswith('al_') and entry.name.endswith('.py'))
        names = {name: root / name for name in found}
        stats = []
        for name in found:
            state = os.stat(os.path.join(root, name))
            stats.append((name, state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns))
        key = ('ordinary', tuple(stats))
    if _CONTEXT and _DIGEST is not None and _DIGEST[0] == key:
        return _DIGEST[1]
    value = hashlib.sha256(b''.join((names[name] if _CONTEXT else names[name].read_bytes()) for name in sorted(names)
                                    if name.startswith('al_') and name.endswith('.py') and '/' not in name)).hexdigest()
    _DIGEST = (key, value)
    return value


def binding_identity():
    if _CONTEXT:
        return dict({k:_CONTEXT[k] for k in ('platform', 'closure_digest', 'origin_digest')},
                    physical_scripts_root=str(Path(__file__).resolve().parent))
    return {'scripts_root':str(Path(__file__).resolve().parent)}


def execution_origin():
    """Original execution provenance data; physical validator origins remain truthful."""
    if _CONTEXT and 'original_execution_origin' in _CONTEXT:
        return dict(_CONTEXT['original_execution_origin'])
    return binding_identity()
