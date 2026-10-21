"""Private, durable, immutable handoff publication outside the repository."""

import os
from pathlib import Path

from harness.execution_contract import MAX_HANDOFF, canonical, digest, loads


def same_path(first: str | Path, second: str | Path) -> bool:
    """Compare existing filesystem targets, including aliases, without rewriting records."""
    if not isinstance(first, (str, Path)) or not isinstance(second, (str, Path)):
        return False
    if not str(first) or not str(second):
        return False
    try:
        return Path(first).resolve(strict=True) == Path(second).resolve(strict=True)
    except (OSError, RuntimeError):
        return False


def durable_file(path: Path, raw: bytes, *, private: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if private:
        os.chmod(path.parent, 0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError("immutable execution file conflicts: " + path.name) from None
        return
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path: Path, value: dict) -> str:
    raw = canonical(value).encode()
    durable_file(path, raw)
    return digest(raw)


def read_handoff(path: str | Path, expected: str | None = None) -> dict:
    raw = Path(path).read_bytes()
    if expected is not None and digest(raw) != expected:
        raise ValueError("approved handoff bytes changed")
    return loads(raw, max_bytes=MAX_HANDOFF)


def atomic_file(path: Path, raw: bytes) -> None:
    """Publish fully fsynced immutable bytes without exposing an incomplete final path."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Same-directory link is atomic and cannot overwrite a prior publication.
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError(
                    "immutable execution publication conflicts: " + path.name
                ) from None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
