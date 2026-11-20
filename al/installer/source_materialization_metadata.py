"""Bounded descriptor-only Darwin metadata; observed security data stays in memory."""

from __future__ import annotations

import ctypes as C
import errno
import os
import platform
import struct
import sys
from dataclasses import dataclass, field

UNSUPPORTED = frozenset({errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP})
RETURNED = 0x80000000
SECURITY = 0x00400000
CRTIME = 0x00000200
VOL_INFO = 0x80000000
VOL_CAPABILITIES = 0x00020000
MAX_SECURITY = 44 + 128 * 24
MAX_LIST = 1024 * 1024
MAX_VALUE = 16 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024


class UnsupportedObservation(Exception):
    """The descriptor's complete metadata profile cannot be observed."""


class _SizeChanged(ValueError):
    pass


class AttrList(C.Structure):
    _fields_ = [("count", C.c_uint16), ("reserved", C.c_uint16)] + [
        (name, C.c_uint32) for name in ("common", "volume", "directory", "file", "fork")
    ]


class Timespec(C.Structure):
    _fields_ = [("seconds", C.c_int64), ("nanoseconds", C.c_int64)]


class DarwinStat(C.Structure):
    # Installed LP64 sys/stat.h __DARWIN_STRUCT_STAT64, including reserved members.
    _fields_ = [
        ("dev", C.c_int32),
        ("mode", C.c_uint16),
        ("nlink", C.c_uint16),
        ("ino", C.c_uint64),
        ("uid", C.c_uint32),
        ("gid", C.c_uint32),
        ("rdev", C.c_int32),
        ("atime", Timespec),
        ("mtime", Timespec),
        ("ctime", Timespec),
        ("birth", Timespec),
        ("size", C.c_int64),
        ("blocks", C.c_int64),
        ("blksize", C.c_int32),
        ("flags", C.c_uint32),
        ("gen", C.c_uint32),
        ("lspare", C.c_int32),
        ("qspare", C.c_int64 * 2),
    ]


@dataclass(frozen=True, repr=False)
class ACLObservation:
    state: str
    flags: int = 0
    entries: tuple = field(default_factory=tuple)


@dataclass(frozen=True, repr=False)
class MetadataObservation:
    security: tuple
    times: tuple
    stamp: tuple


def _bind(library, name, arguments, result=C.c_int):
    try:
        function = getattr(library, name)
    except AttributeError:
        raise UnsupportedObservation("metadata symbol unavailable") from None
    function.argtypes, function.restype = arguments, result
    return function


def _abi():
    if (
        sys.platform != "darwin"
        or platform.machine() not in {"arm64", "x86_64"}
        or C.sizeof(C.c_void_p) != 8
        or C.sizeof(C.c_long) != 8
    ):
        raise UnsupportedObservation("metadata ABI unavailable")
    offsets = {
        "dev": 0,
        "mode": 4,
        "nlink": 6,
        "ino": 8,
        "uid": 16,
        "gid": 20,
        "rdev": 24,
        "atime": 32,
        "mtime": 48,
        "ctime": 64,
        "birth": 80,
        "size": 96,
        "blocks": 104,
        "blksize": 112,
        "flags": 116,
        "gen": 120,
        "lspare": 124,
        "qspare": 128,
    }
    if (
        C.sizeof(DarwinStat) != 144
        or C.sizeof(AttrList) != 24
        or C.sizeof(Timespec) != 16
        or any(getattr(DarwinStat, name).offset != value for name, value in offsets.items())
    ):
        raise UnsupportedObservation("metadata ABI unavailable")


def _failure(code):
    if code in UNSUPPORTED:
        raise UnsupportedObservation("metadata observation unsupported")
    raise OSError(code, "native metadata operation failed")


def _attributes(fd, library, common, volume=0):
    _abi()
    request = AttrList(5, 0, common | RETURNED, volume, 0, 0, 0)
    buffer = C.create_string_buffer(MAX_SECURITY + 128)
    get = _bind(library, "fgetattrlist", [C.c_int, C.c_void_p, C.c_void_p, C.c_size_t, C.c_uint])
    if get(fd, C.byref(request), buffer, len(buffer), 4 | 8):
        _failure(C.get_errno())
    length = struct.unpack_from("=I", buffer)[0]
    if not 24 <= length <= len(buffer):
        raise ValueError("incomplete native metadata buffer")
    raw = buffer.raw[:length]
    masks = struct.unpack_from("=5I", raw, 4)
    requested = (common | RETURNED, volume, 0, 0, 0)
    if not masks[0] & RETURNED or any(a & ~b for a, b in zip(masks, requested, strict=True)):
        raise ValueError("invalid native metadata mask")
    return raw, masks


def _security(raw, *, absence_qualified=False):
    if len(raw) < 44 or struct.unpack_from("=I", raw)[0] != 0x012CC16D:
        raise ValueError("invalid native security structure")
    count, flags = struct.unpack_from("=II", raw, 36)
    if count == 0xFFFFFFFF:
        if len(raw) != 44:
            raise ValueError("invalid absent security extent")
        return ACLObservation("ABSENT") if absence_qualified else ACLObservation("UNSUPPORTED")
    if count > 128 or len(raw) != 44 + count * 24:
        raise ValueError("invalid native security extent")
    entries = tuple(struct.unpack_from("=16sII", raw, 44 + i * 24) for i in range(count))
    return ACLObservation("PRESENT", flags, entries)


def _public_acl(fd, library):
    raw, masks = _attributes(fd, library, SECURITY)
    if len(raw) < 32:
        raise ValueError("incomplete native security reference")
    offset, length = struct.unpack_from("=iI", raw, 24)
    if length == 0:
        if len(raw) != 32 or offset != 8:
            raise ValueError("invalid native security reference")
        return None
    if not masks[0] & SECURITY:
        raise ValueError("invalid native security reference")
    start = 24 + offset
    if offset < 8 or start % 4 or start + length != len(raw):
        raise ValueError("invalid native security reference")
    return _security(raw[start : start + length])


def _extended_security(fd, library):
    raw, masks = _attributes(fd, library, 0, VOL_INFO | VOL_CAPABILITIES)
    if len(raw) != 56:
        raise ValueError("invalid volume capability extent")
    if not masks[1] & VOL_CAPABILITIES:
        raise UnsupportedObservation("volume security capability unavailable")
    values = struct.unpack_from("=8I", raw, 24)
    if not values[1] & values[5] & 0x400:
        raise UnsupportedObservation("volume security capability unavailable")


def _stamp(info):
    return tuple(
        getattr(info, key, 0)
        for key in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_flags",
        )
    )


def _native_key(info):
    return (
        info.dev,
        info.ino,
        info.mode,
        info.uid,
        info.gid,
        info.nlink,
        info.size,
        info.mtime.seconds * 10**9 + info.mtime.nanoseconds,
        info.ctime.seconds * 10**9 + info.ctime.nanoseconds,
        info.flags,
    )


def _bridge_acl(fd, library):
    _extended_security(fd, library)
    get = _bind(
        library,
        "__fstat64_extended",
        [C.c_int, C.POINTER(DarwinStat), C.c_void_p, C.POINTER(C.c_size_t)],
    )
    before = os.fstat(fd)
    birth_before = read_birthtime(fd, library)
    info, buffer = DarwinStat(), C.create_string_buffer(MAX_SECURITY)
    length = C.c_size_t(len(buffer))
    if get(fd, C.byref(info), buffer, C.byref(length)):
        _failure(C.get_errno())
    birth_after = read_birthtime(fd, library)
    after = os.fstat(fd)
    if birth_before != birth_after or (info.birth.seconds, info.birth.nanoseconds) != birth_after:
        raise ValueError("native creation time changed")
    if _stamp(before) != _stamp(after) or _native_key(info) != _stamp(after):
        raise ValueError("native metadata identity changed")
    if not 44 <= length.value <= len(buffer):
        raise ValueError("incomplete extended security result")
    for value in (info.atime, info.mtime, info.ctime, info.birth):
        if not 0 <= value.nanoseconds < 10**9:
            raise ValueError("invalid native timestamp")
    if info.atime.seconds * 10**9 + info.atime.nanoseconds != after.st_atime_ns:
        raise ValueError("native metadata timestamp changed")
    return _security(buffer.raw[: length.value], absence_qualified=True)


def observe_acl(fd, library):
    try:
        public = _public_acl(fd, library)
        if public is not None and public.state == "PRESENT":
            return public
        bridge = _bridge_acl(fd, library)
        if bridge.state == "PRESENT":
            repeated = _public_acl(fd, library)
            if repeated != public or _bridge_acl(fd, library) != bridge:
                raise ValueError("inconsistent native security observation")
        return bridge
    except UnsupportedObservation:
        return ACLObservation("UNSUPPORTED")


def _xattrs_once(fd, library):
    listing = _bind(library, "flistxattr", [C.c_int, C.c_void_p, C.c_size_t, C.c_int], C.c_ssize_t)
    get = _bind(
        library,
        "fgetxattr",
        [C.c_int, C.c_char_p, C.c_void_p, C.c_size_t, C.c_uint32, C.c_int],
        C.c_ssize_t,
    )
    size = listing(fd, None, 0, 0)
    if size < 0:
        _failure(C.get_errno())
    if size > MAX_LIST:
        raise ValueError("metadata observation bound exceeded")
    buffer = C.create_string_buffer(max(1, size))
    actual = listing(fd, buffer, len(buffer), 0)
    if actual < 0:
        _failure(C.get_errno())
    if actual != size:
        raise _SizeChanged("metadata list changed")
    raw = buffer.raw[:actual]
    if raw and not raw.endswith(b"\0"):
        raise ValueError("invalid native metadata list")
    names = raw[:-1].split(b"\0") if raw else []
    if len(names) > 4096 or any(not n for n in names) or len(set(names)) != len(names):
        raise ValueError("invalid native metadata list")
    result, total = [], 0
    for name in sorted(names):
        size = get(fd, name, None, 0, 0, 0)
        if size < 0:
            _failure(C.get_errno())
        total += size
        if size > MAX_VALUE or total > MAX_TOTAL:
            raise ValueError("metadata observation bound exceeded")
        value = C.create_string_buffer(max(1, size))
        actual = get(fd, name, value, len(value), 0, 0)
        if actual < 0:
            _failure(C.get_errno())
        if actual != size:
            raise _SizeChanged("metadata value changed")
        result.append((name, value.raw[:actual]))
    return tuple(result)


def observe_xattrs(fd, library):
    before = _stamp(os.fstat(fd))
    for _ in range(3):
        try:
            first = _xattrs_once(fd, library)
            second = _xattrs_once(fd, library)
        except (_SizeChanged, OSError) as error:
            if isinstance(error, OSError) and error.errno != errno.ERANGE:
                raise
            if _stamp(os.fstat(fd)) != before:
                raise ValueError("metadata observation changed") from None
            continue
        if first != second or _stamp(os.fstat(fd)) != before:
            raise ValueError("metadata observation changed")
        return first
    raise ValueError("metadata observation unstable")


def read_birthtime(fd, library):
    raw, masks = _attributes(fd, library, CRTIME)
    if len(raw) != 40:
        raise ValueError("invalid creation time extent")
    if not masks[0] & CRTIME:
        raise UnsupportedObservation("creation time unavailable")
    seconds, nanoseconds = struct.unpack_from("=qq", raw, 24)
    if not 0 <= nanoseconds < 10**9:
        raise ValueError("invalid creation time")
    return seconds, nanoseconds


def set_birthtime(fd, library, value):
    _abi()
    if not 0 <= value[1] < 10**9:
        raise ValueError("invalid creation time")
    change = _bind(library, "fsetattrlist", [C.c_int, C.c_void_p, C.c_void_p, C.c_size_t, C.c_uint])
    request, time = AttrList(5, 0, CRTIME, 0, 0, 0, 0), Timespec(*value)
    if change(fd, C.byref(request), C.byref(time), C.sizeof(time), 0):
        _failure(C.get_errno())
    if read_birthtime(fd, library) != value:
        raise ValueError("creation time normalization failed")


def observe(fd, library):
    _abi()
    before = os.fstat(fd)
    xattrs, acl = observe_xattrs(fd, library), observe_acl(fd, library)
    if acl.state == "UNSUPPORTED":
        raise UnsupportedObservation("security observation unsupported")
    birth = read_birthtime(fd, library)
    if xattrs != observe_xattrs(fd, library) or acl != observe_acl(fd, library):
        raise ValueError("security observation changed")
    after = os.fstat(fd)
    if _stamp(before) != _stamp(after):
        raise ValueError("metadata observation changed")
    security = (after.st_uid, after.st_gid, after.st_mode, after.st_flags, xattrs, acl)
    return MetadataObservation(
        security, (birth, after.st_atime_ns, after.st_mtime_ns), _stamp(after)
    )
