"""Verified immutable Git blob bytes; authoritative misses stay batched."""
import hashlib
from pathlib import Path
import re
import subprocess

from al_analysis_cache import CACHE
from al_profile import measured


_BINDINGS = {}


def _binding_key(root):
    """Repository object binding inputs for a plain repository: its metadata and config identity."""
    from al_gitlinks import plain_metadata
    root = Path(root).resolve();metadata = plain_metadata(root)
    try:
        if metadata is None:
            return None
        state = [metadata.lstat(), (metadata / 'config').lstat()]
    except OSError:
        return None
    return str(root), tuple((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in state)


def object_binding(root):
    key = _binding_key(root)
    if key is not None and key in _BINDINGS:
        return list(_BINDINGS[key])
    value = _object_binding(root)
    if key is not None and _binding_key(root) == key:
        if len(_BINDINGS) >= 8:
            _BINDINGS.clear()
        _BINDINGS[key] = tuple(value)
    return value


def _object_binding(root):
    result = subprocess.run(['git', '--no-optional-locks', 'rev-parse', '--git-common-dir', '--show-object-format'], cwd=root, capture_output=True, check=True)
    common, algorithm = result.stdout.decode().strip().splitlines()
    if algorithm not in {'sha1', 'sha256'}:
        raise ValueError('unsupported Git object format')
    return [str(Path(root).resolve()), str((Path(root) / common).resolve()), algorithm]


def valid_blob(data, oid, algorithm):
    if not isinstance(data, bytes) or not re.fullmatch('[0-9a-f]{' + str(40 if algorithm == 'sha1' else 64) + '}', oid):
        return False
    return hashlib.new(algorithm, b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == oid


def lookup(binding, oid):
    hit, value = CACHE.get('git-blob', [binding, oid, 'blob'])
    return value if hit and valid_blob(value, oid, binding[2]) else None


def store(binding, oid, data):
    if not valid_blob(data, oid, binding[2]):
        raise ValueError('Git blob object identity mismatch')
    if len(data) <= 2 * 1024 * 1024:
        CACHE.put('git-blob', [binding, oid, 'blob'], data)


@measured('base-object-read')
def blobs(root, oids, *, max_bytes=64 * 1024 * 1024, file_bytes=2 * 1024 * 1024):
    binding = object_binding(root)
    result, missing, total = {}, [], 0
    for oid in dict.fromkeys(oids):
        value = lookup(binding, oid)
        if value is None:
            missing.append(oid)
        else:
            total += len(value)
            if len(value) > file_bytes or total > max_bytes:
                raise ValueError('Git blob byte limit')
            result[oid] = value
    if missing:
        with subprocess.Popen(['git', '--no-optional-locks', 'cat-file', '--batch'], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            try:
                for oid in missing:
                    proc.stdin.write(oid.encode() + b'\n'); proc.stdin.flush()
                    frame = proc.stdout.readline(256)
                    header = frame.split()
                    if not frame.endswith(b'\n') or len(header) != 3 or header[:2] != [oid.encode(), b'blob'] or not header[2].isdigit():
                        raise ValueError('invalid Git blob batch frame')
                    size = int(header[2]); total += size
                    if size > file_bytes or total > max_bytes:
                        raise ValueError('Git blob byte limit')
                    value = proc.stdout.read(size)
                    if len(value) != size or proc.stdout.read(1) != b'\n':
                        raise ValueError('truncated Git blob batch frame')
                    store(binding, oid, value)
                    result[oid] = value
                proc.stdin.close()
                if proc.stdout.read(1) or proc.wait() != 0:
                    raise ValueError('Git blob batch failed')
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
    return result
