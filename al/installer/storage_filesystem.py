"""Bounded descriptor-relative filesystem operations for managed scratch only."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import time
from pathlib import Path

DEFAULT_LIMITS = {"entries": 10000, "depth": 64, "seconds": 1.0, "records": 1000}
MAX_METADATA = 1024 * 1024


class StorageError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise StorageError("invalid_metadata") from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise StorageError("duplicate_metadata_field")
        result[key] = value
    return result


def decode(raw):
    if len(raw) > MAX_METADATA:
        raise StorageError("metadata_limit")
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _invalid())
    except (ValueError, UnicodeError):
        raise StorageError("invalid_metadata") from None


def _invalid():
    raise StorageError("nonfinite_metadata")


def _identifier(value):
    return (
        isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)
    )


def _hash(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def identity(value):
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "uid": value.st_uid,
        "type": stat.S_IFMT(value.st_mode),
    }


def limits(value=None):
    result = DEFAULT_LIMITS | (value or {})
    if set(result) != set(DEFAULT_LIMITS):
        raise StorageError("invalid_limits")
    if any(
        type(result[k]) is not int or not 1 <= result[k] <= maximum
        for k, maximum in (("entries", 1000000), ("depth", 128), ("records", 10000))
    ):
        raise StorageError("invalid_limits")
    if type(result["seconds"]) not in (int, float) or not 0 < result["seconds"] <= 30:
        raise StorageError("invalid_limits")
    return result


def _open_directory(path, *, create=False):
    path = Path(path)
    if not path.is_absolute() or len(path.parts) > 128 or ".." in path.parts:
        raise StorageError("unsafe_namespace")
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise StorageError("unsupported_filesystem")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private(fd):
    value = os.fstat(fd)
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid() or value.st_mode & 0o077:
        raise StorageError("unsafe_namespace")
    return identity(value)


def _read_at(fd, name):
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        before = os.fstat(source)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_METADATA
        ):
            raise StorageError("unsafe_metadata")
        with os.fdopen(os.dup(source), "rb") as stream:
            raw = stream.read(MAX_METADATA + 1)
        if not unchanged(before, os.fstat(source)):
            raise StorageError("metadata_changed")
        return decode(raw)
    finally:
        os.close(source)


def _write_at(fd, name, value):
    raw = canonical(value)
    if len(raw) > MAX_METADATA:
        raise StorageError("metadata_limit")
    temporary = ".write-" + secrets.token_hex(16)
    target = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    try:
        with os.fdopen(target, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def _delete_contents(fd, device, bound, progress, depth):
    if depth >= bound["depth"]:
        return False
    with os.scandir(fd) as iterator:
        for entry in iterator:
            if progress["entries"] >= bound["entries"] or time.monotonic() >= progress["deadline"]:
                return False
            info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
            if info.st_dev != device:
                raise StorageError("filesystem_boundary")
            progress["entries"] += 1
            inode = (info.st_dev, info.st_ino)
            if inode not in progress["seen"]:
                progress["seen"].add(inode)
                progress["logical_bytes"] += info.st_size
                if hasattr(info, "st_blocks") and progress["allocated_bytes"] is not None:
                    progress["allocated_bytes"] += info.st_blocks * 512
                else:
                    progress["allocated_bytes"] = None
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if identity(os.fstat(child)) != identity(info):
                        raise StorageError("member_replaced")
                    if not _delete_contents(child, device, bound, progress, depth + 1):
                        return False
                finally:
                    os.close(child)
                if identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)) != identity(
                    info
                ):
                    raise StorageError("member_replaced")
                os.rmdir(entry.name, dir_fd=fd)
            elif (
                stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                # Test fixtures leave FIFOs/sockets; unlinking only drops the name.
                or stat.S_ISFIFO(info.st_mode)
                or stat.S_ISSOCK(info.st_mode)
            ):
                if identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)) != identity(
                    info
                ):
                    raise StorageError("member_replaced")
                os.unlink(entry.name, dir_fd=fd)
            else:
                raise StorageError("unsupported_member")
    os.fsync(fd)
    return True


@contextlib.contextmanager
def coordinate(fd, *, create=True):
    import fcntl

    lock = os.open(
        "lock", os.O_RDWR | (os.O_CREAT if create else 0) | os.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    try:
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise StorageError("unsafe_lock")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StorageError("coordination_unavailable") from None
                time.sleep(0.01)
        yield
    finally:
        os.close(lock)


def read_json(path):
    path = Path(path)
    parent = _open_directory(path.parent)
    try:
        return _read_at(parent, path.name)
    finally:
        os.close(parent)


def _publish_at(parent, name, value):
    raw = canonical(value)
    if len(raw) > MAX_METADATA:
        raise StorageError("metadata_limit")
    try:
        target = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
    except FileExistsError:
        if canonical(_read_at(parent, name)) != raw:
            raise StorageError("publication_conflict") from None
    else:
        with os.fdopen(target, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    return hashlib.sha256(raw).hexdigest()


def publish_json(path, value):
    path = Path(path)
    parent = _open_directory(path.parent, create=True)
    try:
        return {
            "schema_version": 1,
            "reference": str(path),
            "sha256": _publish_at(parent, path.name, value),
        }
    finally:
        os.close(parent)


def artifact(path, *, maximum=256 * 1024**2):
    path = Path(path)
    parent = _open_directory(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 <= before.st_size <= maximum
        ):
            raise StorageError("unsafe_publication_artifact")
        hasher = hashlib.sha256()
        with os.fdopen(os.dup(fd), "rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                hasher.update(block)
        if not unchanged(before, os.fstat(fd)):
            raise StorageError("publication_changed")
        return {
            "path": str(path),
            "identity": identity(before),
            "size": before.st_size,
            "sha256": hasher.hexdigest(),
        }
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def capture_output(source, directory, name, *, maximum):
    if (
        not name
        or len(name) > 80
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            for c in name
        )
        or name in {".", ".."}
    ):
        raise StorageError("invalid_output_name")
    source = Path(source)
    parent = _open_directory(source.parent)
    target_dir = _open_directory(directory, create=True)
    temporary = ".capture-" + secrets.token_hex(16)
    fd = None
    try:
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 <= before.st_size <= maximum
        ):
            raise StorageError("unsafe_output_spool")
        target = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=target_dir,
        )
        hasher = hashlib.sha256()
        with os.fdopen(target, "wb") as output, os.fdopen(os.dup(fd), "rb") as stream:
            count = 0
            for block in iter(lambda: stream.read(65536), b""):
                count += len(block)
                if count > maximum:
                    raise StorageError("output_limit")
                output.write(block)
                hasher.update(block)
            if count != before.st_size or not unchanged(before, os.fstat(fd)):
                raise StorageError("output_changed")
            output.flush()
            os.fchmod(output.fileno(), 0o444)
            os.fsync(output.fileno())
        final = hasher.hexdigest() + "-" + name
        try:
            os.link(
                temporary,
                final,
                src_dir_fd=target_dir,
                dst_dir_fd=target_dir,
                follow_symlinks=False,
            )
        except FileExistsError:
            pass
        os.unlink(temporary, dir_fd=target_dir)
        os.fsync(target_dir)
        result = artifact(Path(directory) / final, maximum=maximum)
        if result["sha256"] != hasher.hexdigest():
            raise StorageError("publication_conflict")
        return result
    finally:
        try:
            os.unlink(temporary, dir_fd=target_dir)
        except FileNotFoundError:
            pass
        if fd is not None:
            os.close(fd)
        os.close(parent)
        os.close(target_dir)


def unchanged(before, after):
    fields = (
        "st_dev",
        "st_ino",
        "st_uid",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    return all(getattr(before, key) == getattr(after, key) for key in fields)


def read_reference(reference, *, root):
    if (
        not isinstance(reference, dict)
        or set(reference) != {"schema_version", "reference", "sha256"}
        or type(reference["schema_version"]) is not int
        or reference["schema_version"] != 1
        or not isinstance(reference["reference"], str)
        or not _hash(reference["sha256"])
    ):
        raise StorageError("invalid_publication_reference")
    path, root = Path(reference["reference"]), Path(root)
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(root):
        raise StorageError("foreign_publication_reference")
    value = read_json(path)
    if hashlib.sha256(canonical(value)).hexdigest() != reference["sha256"]:
        raise StorageError("publication_changed")
    return value


def evidence_log_digest(path):
    """Match the AL evidence digest, including its executable-mode provenance."""
    path = Path(path)
    parent = _open_directory(path.parent)
    descriptor = None
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > 256 * 1024**2
        ):
            raise StorageError("unsafe_publication_artifact")
        hasher = hashlib.sha256()
        hasher.update(b"\0x" if before.st_mode & 0o111 else b"\0-")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                hasher.update(block)
        if not unchanged(before, os.fstat(descriptor)):
            raise StorageError("publication_changed")
        return hasher.hexdigest()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def source_bytes(path, *, maximum=256 * 1024):
    """Read one fixed canonical source member without following candidate aliases."""
    path = Path(path)
    parent = _open_directory(path.parent)
    descriptor = None
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise StorageError("invalid_canonical_source")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum or not unchanged(before, os.fstat(descriptor)):
            raise StorageError("canonical_source_changed")
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
