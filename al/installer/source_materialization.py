"""Exclusive source materialization; clones never grant cleanup or execution authority."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
from pathlib import Path

from . import source_materialization_metadata as native

UNSUPPORTED = frozenset({errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV})
CLONE_NOFOLLOW = 0x0001
CLONE_NOOWNERCOPY = 0x0002


def _state(info):
    return tuple(
        getattr(info, key)
        for key in (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def _parent(path, *, create=False):
    path = Path(path)
    if ".." in path.parts or not path.name or path.name in {".", ".."}:
        raise ValueError("canonical source materialization path required")
    path = path.absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parent.parts[1:]:
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            following = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            previous, descriptor = descriptor, following
            os.close(previous)
        return descriptor, path.name, path
    except BaseException as error:
        try:
            os.close(descriptor)
        except BaseException as close_error:
            error.add_note("materialization descriptor close failed: " + type(close_error).__name__)
        raise


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("regular source materialization file required")
    return info


def _hash(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    result = hashlib.sha256()
    while block := os.read(fd, 1024 * 1024):
        result.update(block)
    return result.hexdigest()


def _system():
    if sys.platform != "darwin":
        return None
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    try:
        clone = library.fclonefileat
    except AttributeError:
        return None
    clone.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    clone.restype = ctypes.c_int
    return library


def _copy_metadata(source_fd, destination_fd, info):
    # Match copy2's platform profile using pinned descriptors, never pathname aliases.
    if hasattr(os, "listxattr"):
        try:
            names = os.listxattr(source_fd)
        except OSError as error:
            if error.errno not in {errno.ENOTSUP, errno.ENODATA, errno.EINVAL}:
                raise
            names = []
        for name in names:
            try:
                os.setxattr(destination_fd, name, os.getxattr(source_fd, name))
            except OSError as error:
                if error.errno not in {errno.ENOTSUP, errno.ENODATA, errno.EINVAL}:
                    raise
    os.fchmod(destination_fd, stat.S_IMODE(info.st_mode))
    os.utime(destination_fd, ns=(info.st_atime_ns, info.st_mtime_ns))
    flags = getattr(info, "st_flags", 0)
    if flags:
        if hasattr(os, "fchflags"):
            os.fchflags(destination_fd, flags)
        elif sys.platform == "darwin":
            library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            change = library.fchflags
            change.argtypes = [ctypes.c_int, ctypes.c_uint]
            change.restype = ctypes.c_int
            if change(destination_fd, flags):
                code = ctypes.get_errno()
                if code not in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                    raise OSError(code, "source flag materialization failed")
        else:
            raise ValueError("descriptor-relative source flags unavailable")


def _same_entry(parent, name, info):
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    return (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)


def _verify_parent(path, descriptor):
    current, _, _ = _parent(path)
    try:
        old, new = os.fstat(descriptor), os.fstat(current)
        if (old.st_dev, old.st_ino) != (new.st_dev, new.st_ino):
            raise ValueError("materialization parent path replaced")
    except BaseException as error:
        try:
            os.close(current)
        except BaseException as close_error:
            error.add_note("materialization descriptor close failed: " + type(close_error).__name__)
        raise
    else:
        os.close(current)


class _Owned:
    def __init__(self, name, fd, info):
        self.name, self.fd, self.info = name, fd, info
        self.removed = False
        self.removal_attempted = False

    def close(self):
        if self.fd is not None:
            descriptor, self.fd = self.fd, None
            os.close(descriptor)


def _require_owned_entry(parent, entry):
    if entry.fd is None or entry.removed:
        raise ValueError("materialization custody unavailable")
    current = _regular(entry.fd)
    if (
        not _same_entry(parent, entry.name, entry.info)
        or (current.st_dev, current.st_ino) != (entry.info.st_dev, entry.info.st_ino)
        or current.st_uid != os.geteuid()
        or current.st_nlink != 1
    ):
        raise ValueError("materialization inode custody changed")
    return current


def _remove_owned_entry(parent, entry):
    if entry.removal_attempted:
        raise ValueError("materialization cleanup already attempted")
    entry.removal_attempted = True
    _require_owned_entry(parent, entry)
    os.unlink(entry.name, dir_fd=parent)
    entry.removed = True
    try:
        os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError("materialization entry replaced after removal")


def _create_ordinary(parent, name):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        return _Owned(name, fd, os.fstat(fd))
    except BaseException as error:
        try:
            os.close(fd)
        except BaseException as close_error:
            error.add_note("materialization descriptor close failed: " + type(close_error).__name__)
        raise


def _finish_ordinary(source_fd, destination_fd, expected, wanted_mode, metadata, digest):
    os.lseek(source_fd, 0, os.SEEK_SET)
    blocks = (
        (expected,) if expected is not None else iter(lambda: os.read(source_fd, 1024 * 1024), b"")
    )
    for block in blocks:
        remaining = memoryview(block)
        while remaining:
            count = os.write(destination_fd, remaining)
            if count <= 0:
                raise OSError(errno.EIO, "materialization write made no progress")
            remaining = remaining[count:]
    os.fchmod(destination_fd, wanted_mode)
    if (
        _hash(destination_fd) != digest
        or stat.S_IMODE(os.fstat(destination_fd).st_mode) != wanted_mode
    ):
        raise ValueError("materialized content or mode differs")
    if metadata:
        _copy_metadata(source_fd, destination_fd, os.fstat(source_fd))
    if stat.S_IMODE(os.fstat(destination_fd).st_mode) != wanted_mode:
        raise ValueError("materialized metadata mode differs")


def _same_source_metadata(first, second):
    return first.security == second.security and first.times[::2] == second.times[::2]


def _materialize(source, destination, *, expected=None, mode=None, metadata=False, allow_clone=True):
    if type(allow_clone) is not bool:
        raise ValueError("materialization clone choice must be boolean")
    source_parent, source_name, source_path = _parent(source)
    destination_parent = source_fd = None
    candidate = reference = None
    source_profile = parent_profile = reference_profile = accepted_profile = None
    library = None

    def context():
        if _state(os.fstat(source_fd)) != _state(before) or not _same_entry(
            source_parent, source_name, before
        ):
            raise ValueError("source moved before materialization publication")
        _verify_parent(source_path, source_parent)
        _verify_parent(destination_path, destination_parent)
        if source_profile is not None:
            try:
                current_source = native.observe(source_fd, library)
                current_parent = native.observe(destination_parent, library)
            except native.UnsupportedObservation:
                raise ValueError("materialization metadata qualification lost") from None
            if not _same_source_metadata(source_profile, current_source):
                raise ValueError("source security metadata changed")
            if current_parent.security != parent_profile.security:
                raise ValueError("destination parent security metadata changed")

    def independent(entry):
        info = _require_owned_entry(destination_parent, entry)
        if (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino):
            raise ValueError("independent materialization inode required")
        return info

    def reference_stable():
        if reference is not None and not reference.removed:
            independent(reference)
            if (
                reference_profile is not None
                and native.observe(reference.fd, library) != reference_profile
            ):
                raise ValueError("ordinary reference metadata changed")

    try:
        source_fd = os.open(
            source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent
        )
        before = _regular(source_fd)
        digest = _hash(source_fd)
        if _state(os.fstat(source_fd)) != _state(before) or not _same_entry(
            source_parent, source_name, before
        ):
            raise ValueError("source changed during materialization capture")
        if expected is not None and (
            not isinstance(expected, bytes)
            or hashlib.sha256(expected).hexdigest() != digest
            or len(expected) != before.st_size
        ):
            raise ValueError("source differs from captured publication bytes")
        wanted_mode = stat.S_IMODE(before.st_mode) if mode is None else mode
        if (
            type(wanted_mode) is not int
            or not 0 <= wanted_mode <= 0o7777
            or stat.S_IMODE(before.st_mode) != wanted_mode
        ):
            raise ValueError("source differs from captured publication mode")
        destination_parent, destination_name, destination_path = _parent(destination, create=True)
        parent_info = os.fstat(destination_parent)
        if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022:
            raise ValueError("fresh caller-owned materialization directory required")
        try:
            os.stat(destination_name, dir_fd=destination_parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, "materialization destination exists")
        library = _system()
        method, reason = "copy", "platform_or_symbol_unavailable"
        eligible = library is not None
        if eligible and getattr(before, "st_flags", 0):
            eligible, reason = False, "metadata_compatibility"
        if eligible:
            try:
                observed_source = native.observe(source_fd, library)
                observed_parent = native.observe(destination_parent, library)
            except native.UnsupportedObservation:
                eligible, reason = False, "metadata_observation_unsupported"
            else:
                source_profile, parent_profile = observed_source, observed_parent
        if not allow_clone:
            reason = "clone_not_requested"
        if eligible and allow_clone:
            context()
            for _ in range(8):
                try:
                    reference = _create_ordinary(
                        destination_parent, ".materialization-" + secrets.token_hex(16)
                    )
                    break
                except FileExistsError:
                    continue
            else:
                raise FileExistsError(errno.EEXIST, "ordinary reference name collisions")
            independent(reference)
            _finish_ordinary(source_fd, reference.fd, expected, wanted_mode, metadata, digest)
            try:
                reference_profile = native.observe(reference.fd, library)
            except native.UnsupportedObservation:
                eligible, reason = False, "metadata_observation_unsupported"
            context()
            reference_stable()
            if eligible:
                if library.fclonefileat(
                    source_fd,
                    destination_parent,
                    os.fsencode(destination_name),
                    CLONE_NOFOLLOW | CLONE_NOOWNERCOPY,
                ):
                    code = ctypes.get_errno()
                    if code not in UNSUPPORTED:
                        raise OSError(code, "source clone failed")
                    reason = "clone_unsupported"
                else:
                    # Record the entry before opening; an unbound result is never deleted.
                    info = os.stat(
                        destination_name, dir_fd=destination_parent, follow_symlinks=False
                    )
                    candidate = _Owned(destination_name, None, info)
                    candidate.fd = os.open(
                        destination_name,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=destination_parent,
                    )
                    independent(candidate)
                    os.fchmod(candidate.fd, wanted_mode)
                    if _hash(candidate.fd) != digest:
                        raise ValueError("cloned content differs")
                    if _hash(source_fd) != digest:
                        raise ValueError("source content changed")
                    context()
                    reference_stable()
                    try:
                        observed = native.observe(candidate.fd, library)
                        if observed.security != reference_profile.security:
                            reason = "metadata_compatibility"
                        else:
                            if observed.times[0] != reference_profile.times[0]:
                                native.set_birthtime(
                                    candidate.fd, library, reference_profile.times[0]
                                )
                            os.utime(candidate.fd, ns=reference_profile.times[1:])
                            accepted_profile = native.observe(candidate.fd, library)
                            if (
                                accepted_profile.security != reference_profile.security
                                or accepted_profile.times != reference_profile.times
                            ):
                                raise ValueError("clone metadata normalization differs")
                            method, reason = "clone", None
                    except native.UnsupportedObservation:
                        reason = "metadata_observation_unsupported"
        if method != "clone":
            if candidate is not None:
                _remove_owned_entry(destination_parent, candidate)
                candidate.close()
                candidate = None
            context()
            reference_stable()
            candidate = _create_ordinary(destination_parent, destination_name)
            independent(candidate)
            _finish_ordinary(source_fd, candidate.fd, expected, wanted_mode, metadata, digest)
            if _hash(source_fd) != digest:
                raise ValueError("source content changed")
        final_state = _state(os.fstat(candidate.fd))
        context()
        reference_stable()
        final = independent(candidate)
        if final.st_size != before.st_size or stat.S_IMODE(final.st_mode) != wanted_mode:
            raise ValueError("materialized final profile differs")
        if method == "clone" and native.observe(candidate.fd, library) != accepted_profile:
            raise ValueError("accepted clone metadata changed")
        os.fsync(candidate.fd)
        if reference is not None:
            _remove_owned_entry(destination_parent, reference)
            reference.close()
        os.fsync(destination_parent)
        context()
        if _state(independent(candidate)) != final_state:
            raise ValueError("materialized inode changed after verification")
        if method == "clone" and native.observe(candidate.fd, library) != accepted_profile:
            raise ValueError("accepted clone metadata changed")
        # Closing is part of success. A failed close cancels publication success.
        descriptor, source_fd = source_fd, None
        os.close(descriptor)
        descriptor, source_parent = source_parent, None
        os.close(descriptor)
        candidate.close()
        descriptor, destination_parent = destination_parent, None
        os.close(descriptor)
        return {"method": method, "reason": reason, "bytes": before.st_size}
    except BaseException as error:
        for entry in (candidate, reference):
            if entry is not None and not entry.removed:
                try:
                    _remove_owned_entry(destination_parent, entry)
                except BaseException as cleanup_error:
                    error.add_note(
                        "owned materialization cleanup failed: " + type(cleanup_error).__name__
                    )
        for entry in (candidate, reference):
            if entry is not None:
                try:
                    entry.close()
                except BaseException as close_error:
                    error.add_note(
                        "materialization descriptor close failed: " + type(close_error).__name__
                    )
        for descriptor in (source_fd, source_parent, destination_parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as close_error:
                    error.add_note(
                        "materialization descriptor close failed: " + type(close_error).__name__
                    )
        raise


def copy2(source, destination, *, follow_symlinks=True, allow_clone=True):
    """copytree callback for reviewed regular sources; aliases always refuse."""
    destination = Path(destination)
    if destination.is_dir() and not destination.is_symlink():
        destination = destination / Path(source).name
    _materialize(source, destination, metadata=True, allow_clone=allow_clone)
    return str(destination)


def write_captured(source, destination, expected, mode):
    """Publish verified captured bytes at an exclusive fresh destination."""
    return _materialize(source, destination, expected=expected, mode=mode)
