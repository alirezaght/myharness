"""Executable bounds and filesystem races, using the real streamed helper."""
import hashlib
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from al.installer import execution_process as processes
from al.installer import validator_origin as origin
from al.installer import workspace_executable as workspace


@pytest.fixture
def executable(tmp_path):
    path = tmp_path.resolve() / 'executable'
    path.write_bytes(b'original executable bytes')
    path.chmod(0o700)
    return path


def test_large_executable_and_ordinary_resource_bounds(tmp_path, monkeypatch):
    path = tmp_path.resolve() / 'large-executable'
    block = b'x' * 1024 * 1024
    with path.open('wb') as stream:
        for _ in range(17):
            stream.write(block)
    path.chmod(0o750)
    expected = {'sha256': hashlib.sha256(block * 17).hexdigest(), 'mode': 0o750}
    assert origin.EXECUTABLE_LIMIT == workspace.LIMIT == 128 * 1024 * 1024
    assert workspace.executable_state is origin.executable_state
    assert origin.executable_state(path) == expected
    with pytest.raises(ValueError, match='16 MiB limit'):
        origin.capture(path)
    monkeypatch.setattr(origin.sys, 'executable', str(path))
    assert origin.interpreter_identity() == dict(
        expected, executable=str(path), canonical_executable=str(path),
        implementation=sys.implementation.name, version=list(sys.version_info[:3]),
    )
    # Simulated Linux kernel path lookup; the hashing and canonical checks are real.
    seen = []
    monkeypatch.setattr(processes, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(processes, 'os', SimpleNamespace(
        readlink=lambda name: seen.append(name) or str(path),
    ))
    assert processes.kernel_image(123) == dict(
        expected, path=str(path), platform='linux', mechanism='linux-proc-exe-v1',
    )
    assert seen == ['/proc/123/exe']


@pytest.mark.parametrize('kind', ['empty', 'directory', 'symlink', 'oversized'])
def test_invalid_executable_refused_before_open(tmp_path, monkeypatch, kind):
    path = tmp_path.resolve() / 'invalid'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'symlink':
        target = path.with_name('target')
        target.write_bytes(b'executable')
        path.symlink_to(target)
    else:
        with path.open('wb') as stream:
            stream.truncate(origin.EXECUTABLE_LIMIT + 1 if kind == 'oversized' else 0)
    def forbidden(*args, **kwargs):
        pytest.fail('invalid executable reached open')
    monkeypatch.setattr(origin.os, 'open', forbidden)
    with pytest.raises(ValueError):
        origin.executable_state(path)


def test_reduced_bound_exact_edge(executable, monkeypatch):
    # Scoped arithmetic check; the large fixture separately proves the real bound.
    monkeypatch.setattr(origin, 'EXECUTABLE_LIMIT', executable.stat().st_size)
    assert origin.executable_state(executable)['sha256'] == hashlib.sha256(executable.read_bytes()).hexdigest()
    with executable.open('ab') as stream:
        stream.write(b'x')
    with pytest.raises(ValueError, match='invalid bounded'):
        origin.executable_state(executable)


@pytest.mark.parametrize('change', ['content', 'mode', 'inode', 'growth', 'overflow'])
def test_read_boundary_mutation_rejected_and_descriptor_closed(executable, monkeypatch, change):
    original = executable.stat()
    real_fdopen, real_close = os.fdopen, os.close
    descriptors, closed = [], []
    if change == 'overflow':
        monkeypatch.setattr(origin, 'EXECUTABLE_LIMIT', original.st_size)
    def mutate():
        if change == 'content':
            # Same length and restored mtime: ctime must still detect the write.
            executable.write_bytes(b'X' * original.st_size)
            os.utime(executable, ns=(original.st_atime_ns, original.st_mtime_ns))
            assert executable.stat().st_ctime_ns != original.st_ctime_ns
        elif change == 'mode':
            executable.chmod(0o600)
        elif change == 'inode':
            replacement = executable.with_name('replacement')
            replacement.write_bytes(executable.read_bytes())
            replacement.chmod(0o700)
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            replacement.replace(executable)
        else:
            with executable.open('ab') as stream:
                stream.write(b'growth')
    class MutatingStream:
        def __init__(self, descriptor, *args, **kwargs):
            descriptors.append(descriptor)
            self.stream = real_fdopen(descriptor, *args, **kwargs)
            self.changed = False
        def __enter__(self):
            self.stream.__enter__()
            return self
        def read(self, size):
            if not self.changed:
                self.changed = True
                mutate()
            return self.stream.read(size)
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
    def close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)
    monkeypatch.setattr(origin.os, 'fdopen', MutatingStream)
    monkeypatch.setattr(origin.os, 'close', close)
    if change == 'content':
        # Separate setup from the mutation on filesystems that coalesce ctime ticks.
        time.sleep(0.02)
    with pytest.raises(ValueError, match='moved during capture|byte limit'):
        origin.executable_state(executable)
    assert closed == descriptors and len(closed) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize('change', ['inode', 'symlink'])
def test_stat_to_open_substitution_refused(executable, monkeypatch, change):
    real_open, real_close = os.open, os.close
    opened, closed = [], []
    target = executable.with_name('replacement')
    target.write_bytes(executable.read_bytes())
    target.chmod(0o700)
    def substitute(path, flags):
        assert flags & os.O_NOFOLLOW
        if change == 'symlink':
            executable.unlink()
            executable.symlink_to(target)
        else:
            target.replace(executable)
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        return descriptor
    def close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)
    monkeypatch.setattr(origin.os, 'open', substitute)
    monkeypatch.setattr(origin.os, 'close', close)
    with pytest.raises((ValueError, OSError)):
        origin.executable_state(executable)
    assert closed == opened
    assert len(opened) == (0 if change == 'symlink' else 1)
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_captured_standalone_validator_uses_real_interpreter():
    # No package/loader context: this is the pre-loader utility execution shape.
    namespace = {'__name__': 'captured_validator'}
    raw = Path(origin.__file__).read_bytes()
    exec(compile(raw, '<captured-validator>', 'exec'), namespace)
    expected = origin.interpreter_identity()
    assert namespace['interpreter_identity']() == expected
    actual = Path(expected['canonical_executable'])
    hasher = hashlib.sha256()
    with actual.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    assert expected['sha256'] == hasher.hexdigest()
    print(f'real interpreter executable: {actual}; bytes={actual.stat().st_size}')
