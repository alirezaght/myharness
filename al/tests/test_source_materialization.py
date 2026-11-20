"""Source materialization preserves content boundaries across copy and clone paths."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import struct
import sys
import tempfile
from pathlib import Path

import pytest

from al.installer import source_materialization as M
from al.installer import source_materialization_metadata as N


def _file(tmp_path, name="source.py", payload=b"source payload\n", mode=0o640):
    source = tmp_path / name
    source.write_bytes(payload)
    source.chmod(mode)
    return source


def _inode(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def _observe(fd, _library):
    info = os.fstat(fd)
    if hasattr(os, "listxattr"):
        xattrs = tuple(
            (os.fsencode(name), os.getxattr(fd, name)) for name in sorted(os.listxattr(fd))
        )
    elif sys.platform == "darwin":
        xattrs = N.observe_xattrs(fd, ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True))
    else:
        xattrs = ()
    security = (
        info.st_uid,
        info.st_gid,
        info.st_mode,
        getattr(info, "st_flags", 0),
        xattrs,
        N.ACLObservation("ABSENT"),
    )
    stamp = N._stamp(info)
    return N.MetadataObservation(
        security,
        ((1, 0), info.st_atime_ns, info.st_mtime_ns),
        stamp,
    )


def _install_fake_native(monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    monkeypatch.setattr(N, "observe", _observe)
    monkeypatch.setattr(N, "set_birthtime", lambda *_args: None)


def _fake_clone(source_fd, parent_fd, name, _flags):
    source_info = os.fstat(source_fd)
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent_fd)
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
        while block := os.read(source_fd, 65536):
            os.write(descriptor, block)
        os.fchmod(descriptor, stat.S_IMODE(source_info.st_mode))
    finally:
        os.close(descriptor)
    return 0


def _native_response(length, *, common=0, volume=0):
    raw = bytearray(length)
    struct.pack_into("=I", raw, 0, length)
    struct.pack_into("=5I", raw, 4, common, volume, 0, 0, 0)
    return bytes(raw)


def _fake_attributes(raw):
    def fgetattrlist(_fd, _request, buffer, capacity, _options):
        assert len(raw) <= capacity
        ctypes.memmove(buffer, raw, len(raw))
        return 0

    return fgetattrlist


def _native_library(raw):
    class Library:
        pass

    library = Library()
    library.fgetattrlist = _fake_attributes(raw)
    return library


def _set_test_xattr(fd, name, value):
    if hasattr(os, "setxattr"):
        os.setxattr(fd, name, value)
        return
    if sys.platform != "darwin":
        raise NotImplementedError("descriptor xattr writing unavailable")
    function = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).fsetxattr
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    value_buffer = ctypes.create_string_buffer(value)
    if function(fd, os.fsencode(name), value_buffer, len(value), 0, 0):
        code = ctypes.get_errno()
        raise OSError(code, "test-owned extended attribute write failed")


def _test_xattrs(fd):
    if hasattr(os, "listxattr"):
        return tuple(
            (os.fsencode(name), os.getxattr(fd, name)) for name in sorted(os.listxattr(fd))
        )
    if sys.platform == "darwin":
        return N.observe_xattrs(fd, ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True))
    return ()


def _security_result(security, *, offset=8, common=None):
    raw_security = bytes(security)
    result = bytearray(32 + len(raw_security))
    struct.pack_into("=I", result, 0, len(result))
    struct.pack_into(
        "=5I",
        result,
        4,
        N.RETURNED | N.SECURITY if common is None else common,
        0,
        0,
        0,
        0,
    )
    struct.pack_into("=iI", result, 24, offset, len(raw_security))
    start = 24 + offset
    if 0 <= start and start + len(raw_security) <= len(result):
        result[start : start + len(raw_security)] = raw_security
    return bytes(result)


def test_captured_materialization_uses_real_macos_clone_when_supported_and_is_independent(tmp_path):
    source = _file(tmp_path, payload=b"original materialized bytes\n" * 1024, mode=0o751)
    captured = source.read_bytes()
    mode = stat.S_IMODE(source.stat().st_mode)
    destination = tmp_path / "snapshot/source.py"

    result = M.write_captured(source, destination, captured, mode)

    assert destination.read_bytes() == captured
    assert stat.S_IMODE(destination.stat().st_mode) == mode
    assert destination.stat().st_nlink == 1
    assert _inode(destination) != _inode(source)
    assert result["bytes"] == len(captured)
    assert result["method"] in {"clone", "copy"}
    if result["method"] == "copy":
        assert result["reason"] in {
            "platform_or_symbol_unavailable",
            "clone_unsupported",
            "metadata_compatibility",
            "metadata_observation_unsupported",
        }
        assert sys.platform != "darwin", "Darwin clone acceptance requires a real clone"
    else:
        assert result["reason"] is None

    destination.write_bytes(b"snapshot mutation")
    assert source.read_bytes() == captured
    source.write_bytes(b"source mutation")
    assert destination.read_bytes() == b"snapshot mutation"


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin clone API is macOS-specific")
def test_actual_macos_clone_in_private_temp_root_when_metadata_allows():
    with tempfile.TemporaryDirectory(
        prefix="source-materialization-clone-", dir="/private/tmp"
    ) as root:
        directory = Path(root)
        source = _file(directory, payload=b"controlled APFS clone payload\n" * 128, mode=0o640)
        captured = source.read_bytes()
        destination = directory / "destination/source.py"
        result = M.write_captured(source, destination, captured, 0o640)
        assert result == {"method": "clone", "reason": None, "bytes": len(captured)}
        assert destination.read_bytes() == captured
        assert _inode(destination) != _inode(source)
        destination.write_bytes(b"destination changed")
        assert source.read_bytes() == captured
        source.write_bytes(b"source changed")
        assert destination.read_bytes() == b"destination changed"
        assert not list(directory.glob(".materialization-*"))


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin metadata observer is macOS-specific")
def test_actual_darwin_metadata_observer_returns_complete_known_acl_state(tmp_path):
    source = _file(tmp_path, payload=b"observer-owned fixture", mode=0o640)
    library = M._system()
    assert library is not None
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        observed = N.observe(descriptor, library)
    finally:
        os.close(descriptor)
    assert observed.security[5].state in {"ABSENT", "PRESENT"}
    assert isinstance(observed.security[4], tuple)
    assert observed.times[0][1] < 1_000_000_000


@pytest.mark.parametrize("mutation", ["write", "truncate", "chmod", "unlink"])
def test_independent_materialized_file_does_not_share_fixture_mutations(tmp_path, mutation):
    source = _file(tmp_path, payload=b"shared logical source\n" * 32, mode=0o751)
    captured = source.read_bytes()
    mode = stat.S_IMODE(source.stat().st_mode)
    first, sibling = tmp_path / "first/file.py", tmp_path / "sibling/file.py"
    M.write_captured(source, first, captured, mode)
    M.write_captured(source, sibling, captured, mode)
    assert len({_inode(source), _inode(first), _inode(sibling)}) == 3

    if mutation == "write":
        first.write_bytes(b"changed")
    elif mutation == "truncate":
        first.open("wb").close()
    elif mutation == "chmod":
        first.chmod(0o600)
    else:
        first.unlink()

    assert source.read_bytes() == captured
    assert stat.S_IMODE(source.stat().st_mode) == mode
    assert sibling.read_bytes() == captured
    assert stat.S_IMODE(sibling.stat().st_mode) == mode


@pytest.mark.parametrize("unsupported", [errno.ENOTSUP, errno.EXDEV])
def test_unsupported_and_cross_filesystem_clone_errors_use_exclusive_copy(
    tmp_path, monkeypatch, unsupported
):
    source = _file(tmp_path)
    _install_fake_native(monkeypatch)

    class UnsupportedClone:
        def fclonefileat(self, *_args):
            ctypes.set_errno(unsupported)
            return -1

    monkeypatch.setattr(M, "_system", lambda: UnsupportedClone())
    destination = tmp_path / f"copy-{unsupported}.py"
    result = M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert result == {
        "method": "copy",
        "reason": "clone_unsupported",
        "bytes": len(source.read_bytes()),
    }
    assert destination.read_bytes() == source.read_bytes()
    assert _inode(destination) != _inode(source)


def test_unavailable_clone_symbol_and_metadata_incompatibility_fall_back(tmp_path, monkeypatch):
    source = _file(tmp_path)
    _install_fake_native(monkeypatch)
    monkeypatch.setattr(M, "_system", lambda: None)
    result = M.write_captured(source, tmp_path / "unavailable.py", source.read_bytes(), 0o640)
    assert result["method"] == "copy"
    assert result["reason"] == "platform_or_symbol_unavailable"

    class IncompatibleClone:
        fclonefileat = staticmethod(_fake_clone)

    candidate_inode = None
    original_observe = _observe

    def incompatible_observe(fd, library):
        nonlocal candidate_inode
        observed = original_observe(fd, library)
        info = os.fstat(fd)
        if candidate_inode == (info.st_dev, info.st_ino):
            security = (*observed.security[:3], 1, *observed.security[4:])
            return N.MetadataObservation(security, observed.times, observed.stamp)
        return observed

    def clone_and_remember(source_fd, parent_fd, name, flags):
        nonlocal candidate_inode
        result = _fake_clone(source_fd, parent_fd, name, flags)
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        candidate_inode = (info.st_dev, info.st_ino)
        return result

    monkeypatch.setattr(N, "observe", incompatible_observe)
    monkeypatch.setattr(IncompatibleClone, "fclonefileat", staticmethod(clone_and_remember))
    monkeypatch.setattr(M, "_system", lambda: IncompatibleClone())
    result = M.write_captured(source, tmp_path / "metadata.py", source.read_bytes(), 0o640)
    assert result["method"] == "copy"
    assert result["reason"] == "metadata_compatibility"
    assert (tmp_path / "metadata.py").read_bytes() == source.read_bytes()


@pytest.mark.parametrize("failure", [errno.EPERM, errno.EACCES, errno.ENOSPC, errno.EIO])
def test_permission_exhaustion_and_io_errors_do_not_fall_back(tmp_path, monkeypatch, failure):
    source = _file(tmp_path)
    _install_fake_native(monkeypatch)

    class FailedClone:
        def fclonefileat(self, *_args):
            ctypes.set_errno(failure)
            return -1

    monkeypatch.setattr(M, "_system", lambda: FailedClone())
    destination = tmp_path / f"failed-{failure}.py"
    with pytest.raises(OSError) as raised:
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert raised.value.errno == failure
    assert not destination.exists()


def test_captured_bytes_mode_and_existing_destination_are_authoritative(tmp_path):
    source = _file(tmp_path)
    destination = tmp_path / "destination.py"
    with pytest.raises(ValueError, match="captured publication bytes"):
        M.write_captured(source, destination, b"different", 0o640)
    with pytest.raises(ValueError, match="captured publication mode"):
        M.write_captured(source, destination, source.read_bytes(), 0o600)
    assert not destination.exists()

    destination.write_text("preserve existing destination")
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert destination.read_bytes() == before


def test_copy2_fixture_path_preserves_ordinary_mode_and_mtime(tmp_path):
    source = _file(tmp_path, payload=b"fixture metadata", mode=0o751)
    timestamp = 1_700_000_000_123_456_789
    os.utime(source, ns=(timestamp, timestamp))
    destination = tmp_path / "fixture-copy.py"
    assert M.copy2(source, destination) == str(destination)
    source_info, destination_info = source.stat(), destination.stat()
    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination_info.st_mode) == stat.S_IMODE(source_info.st_mode) == 0o751
    assert destination_info.st_mtime_ns == source_info.st_mtime_ns == timestamp


def test_copy2_clone_profile_preserves_old_times_and_requested_mode(tmp_path, monkeypatch):
    source = _file(tmp_path, payload=b"copy2 profile", mode=0o751)
    timestamp = 1_600_000_123_456_789_000
    os.utime(source, ns=(timestamp, timestamp))

    class CloneAvailable:
        fclonefileat = staticmethod(_fake_clone)

    _install_fake_native(monkeypatch)
    monkeypatch.setattr(M, "_system", lambda: CloneAvailable())
    destination = tmp_path / "copy2-clone.py"
    previous = os.umask(0o077)
    try:
        assert M.copy2(source, destination) == str(destination)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o751
    assert destination.stat().st_mtime_ns == timestamp
    assert not list(tmp_path.glob(".materialization-*"))


def test_symlink_source_ancestor_and_destination_aliases_refuse_without_following(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    actual = _file(real)
    source_link = tmp_path / "source-link.py"
    source_link.symlink_to(actual)
    with pytest.raises(OSError):
        M.copy2(source_link, tmp_path / "source-copy.py")
    assert not (tmp_path / "source-copy.py").exists()

    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(OSError):
        M.copy2(actual, linked_parent / "destination.py")
    assert not (tmp_path / "real/destination.py").exists()

    alias = tmp_path / "hardlink.py"
    os.link(actual, alias)
    with pytest.raises(FileExistsError):
        M.copy2(actual, alias)
    assert _inode(alias) == _inode(actual)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_fifo_source_is_rejected_without_blocking(tmp_path):
    source = tmp_path / "fifo"
    os.mkfifo(source)
    with pytest.raises(ValueError, match="regular source"):
        M.copy2(source, tmp_path / "fifo-copy")
    assert not (tmp_path / "fifo-copy").exists()


def test_source_change_during_capture_refuses_and_leaves_no_destination(tmp_path, monkeypatch):
    source = _file(tmp_path, payload=b"before")
    original = M._hash
    first = True

    def change_after_read(fd):
        nonlocal first
        result = original(fd)
        if first:
            first = False
            source.write_bytes(b"changed during capture")
        return result

    monkeypatch.setattr(M, "_hash", change_after_read)
    destination = tmp_path / "result.py"
    with pytest.raises(ValueError, match="source changed during materialization"):
        M.write_captured(source, destination, b"before", 0o640)
    assert not destination.exists()


def test_destination_replacement_is_not_deleted_during_failure_cleanup(tmp_path, monkeypatch):
    source = _file(tmp_path)
    _install_fake_native(monkeypatch)
    destination = tmp_path / "result.py"
    parked = tmp_path / "created-but-moved.py"
    original = M._require_owned_entry
    replaced = False

    def replace_at_custody_check(parent, entry):
        nonlocal replaced
        if entry.name == destination.name and not replaced:
            replaced = True
            os.rename(destination, parked)
            destination.write_bytes(b"external replacement")
        return original(parent, entry)

    monkeypatch.setattr(M, "_require_owned_entry", replace_at_custody_check)
    with pytest.raises(ValueError, match="custody changed"):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert destination.read_bytes() == b"external replacement"
    assert parked.exists()


def test_reference_replacement_is_not_deleted_during_failure_cleanup(tmp_path, monkeypatch):
    source = _file(tmp_path)
    _install_fake_native(monkeypatch)
    destination = tmp_path / "result.py"
    parked = tmp_path / "parked-reference"
    original = M._require_owned_entry
    replaced = False

    def replace_reference(parent, entry):
        nonlocal replaced
        if entry.name.startswith(".materialization-") and not replaced:
            replaced = True
            os.rename(entry.name, parked.name, src_dir_fd=parent, dst_dir_fd=parent)
            fd = os.open(entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            os.write(fd, b"external reference replacement")
            os.close(fd)
        return original(parent, entry)

    monkeypatch.setattr(M, "_require_owned_entry", replace_reference)
    with pytest.raises(ValueError, match="custody changed"):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert parked.exists()
    replacements = list(tmp_path.glob(".materialization-*"))
    assert len(replacements) == 1
    assert replacements[0].read_bytes() == b"external reference replacement"
    assert not destination.exists()


def test_destination_parent_replacement_is_detected_and_reference_is_cleaned(tmp_path, monkeypatch):
    source = _file(tmp_path)
    _install_fake_native(monkeypatch)
    parent = tmp_path / "out"
    relocated = tmp_path / "out-original"
    destination = parent / "result.py"
    original = M._verify_parent
    replaced = False

    def replace_parent(path, descriptor):
        nonlocal replaced
        original(path, descriptor)
        if Path(path).parent == parent and not replaced:
            replaced = True
            os.rename(parent, relocated)
            parent.mkdir(mode=0o700)

    monkeypatch.setattr(M, "_verify_parent", replace_parent)
    with pytest.raises(ValueError, match="parent path replaced"):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert parent.is_dir() and not list(parent.iterdir())
    assert relocated.is_dir() and not list(relocated.iterdir())


def test_hardlinked_source_still_gets_an_independent_single_link_destination(tmp_path):
    source = _file(tmp_path)
    os.link(source, tmp_path / "source-alias.py")
    destination = tmp_path / "materialized.py"
    result = M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert result["bytes"] == source.stat().st_size
    assert destination.stat().st_nlink == 1
    assert _inode(destination) not in {_inode(source), _inode(tmp_path / "source-alias.py")}


@pytest.mark.parametrize("length", [24, 41])
def test_birthtime_parser_rejects_malformed_extent_before_missing_validity(length, monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    raw = _native_response(length, common=N.RETURNED)
    with pytest.raises(ValueError, match="extent"):
        N.read_birthtime(7, _native_library(raw))


def test_birthtime_parser_treats_correct_extent_without_validity_as_unsupported(monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    raw = _native_response(40, common=N.RETURNED)
    with pytest.raises(N.UnsupportedObservation):
        N.read_birthtime(7, _native_library(raw))


@pytest.mark.parametrize("length", [24, 57])
def test_volume_parser_rejects_malformed_extent_before_missing_capability(length, monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    raw = _native_response(length, common=N.RETURNED)
    with pytest.raises(ValueError, match="extent"):
        N._extended_security(7, _native_library(raw))


def test_acl_states_and_entry_order_remain_distinct():
    prefix = bytearray(44)
    struct.pack_into("=I", prefix, 0, 0x012CC16D)
    struct.pack_into("=II", prefix, 36, 0xFFFFFFFF, 0)
    absent = N._security(bytes(prefix), absence_qualified=True)
    unknown = N._security(bytes(prefix), absence_qualified=False)
    assert absent.state == "ABSENT"
    assert unknown.state == "UNSUPPORTED"

    def present(entries):
        raw = bytearray(44 + len(entries) * 24)
        struct.pack_into("=I", raw, 0, 0x012CC16D)
        struct.pack_into("=II", raw, 36, len(entries), 3)
        for index, entry in enumerate(entries):
            struct.pack_into("=16sII", raw, 44 + index * 24, *entry)
        return N._security(bytes(raw))

    empty = present([])
    first, second = (b"first".ljust(16, b"\0"), 1, 2), (b"second".ljust(16, b"\0"), 3, 4)
    forward, reverse = present([first, second]), present([second, first])
    assert empty.state == "PRESENT" and empty.entries == ()
    assert forward.state == reverse.state == "PRESENT"
    assert forward != reverse


def test_public_acl_parser_rejects_bad_magic_and_invalid_reference(monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    invalid_magic = bytearray(44)
    struct.pack_into("=I", invalid_magic, 36, 0xFFFFFFFF)
    with pytest.raises(ValueError, match="security structure"):
        N._public_acl(7, _native_library(_security_result(invalid_magic)))

    valid_absence = bytearray(44)
    struct.pack_into("=I", valid_absence, 0, 0x012CC16D)
    struct.pack_into("=I", valid_absence, 36, 0xFFFFFFFF)
    with pytest.raises(ValueError, match="security reference"):
        N._public_acl(7, _native_library(_security_result(valid_absence, offset=4)))


def test_public_acl_parser_rejects_impossible_count_and_extent(monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    for count in (1, 129):
        security = bytearray(44)
        struct.pack_into("=I", security, 0, 0x012CC16D)
        struct.pack_into("=II", security, 36, count, 0)
        with pytest.raises(ValueError, match="security extent"):
            N._public_acl(7, _native_library(_security_result(security)))


def test_correctly_sized_volume_response_without_capability_is_unsupported(monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    raw = _native_response(56, common=N.RETURNED)
    with pytest.raises(N.UnsupportedObservation):
        N._extended_security(7, _native_library(raw))


def test_native_operational_error_is_not_reclassified_as_unsupported(monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)

    def failed(*_args):
        ctypes.set_errno(errno.EIO)
        return -1

    class Library:
        pass

    library = Library()
    library.fgetattrlist = failed
    with pytest.raises(OSError) as error:
        N.read_birthtime(7, library)
    assert error.value.errno == errno.EIO


def test_operation_level_malformed_observation_is_not_ordinary_fallback(tmp_path, monkeypatch):
    source = _file(tmp_path)

    class CloneAvailable:
        fclonefileat = staticmethod(_fake_clone)

    _install_fake_native(monkeypatch)

    def malformed(_fd, _library):
        raise ValueError("malformed native observation")

    monkeypatch.setattr(N, "observe", malformed)
    monkeypatch.setattr(M, "_system", lambda: CloneAvailable())
    destination = tmp_path / "malformed.py"
    with pytest.raises(ValueError, match="malformed native observation"):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert not destination.exists()


def test_captured_clone_profile_ignores_umask_and_keeps_requested_mode(tmp_path, monkeypatch):
    source = _file(tmp_path, payload=b"mode profile", mode=0o751)
    old_timestamp = 1_600_000_123_456_789_000
    os.utime(source, ns=(old_timestamp, old_timestamp))

    class CloneAvailable:
        fclonefileat = staticmethod(_fake_clone)

    _install_fake_native(monkeypatch)
    monkeypatch.setattr(M, "_system", lambda: CloneAvailable())
    previous = os.umask(0o077)
    try:
        destination = tmp_path / "mode-copy.py"
        result = M.write_captured(source, destination, source.read_bytes(), 0o751)
    finally:
        os.umask(previous)
    assert result == {"method": "clone", "reason": None, "bytes": len(b"mode profile")}
    assert stat.S_IMODE(destination.stat().st_mode) == 0o751
    assert destination.stat().st_mtime_ns != old_timestamp


@pytest.mark.skipif(
    not hasattr(os, "setxattr") and sys.platform != "darwin",
    reason="descriptor xattr writing unavailable",
)
def test_arbitrary_candidate_xattr_mismatch_falls_back_without_stripping_source(
    tmp_path, monkeypatch
):
    source = _file(tmp_path, payload=b"xattr profile", mode=0o640)
    prefix = "user." if sys.platform != "darwin" else "com.example."
    name = prefix + "harness-test"
    extra = prefix + "harness-clone-only"
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        _set_test_xattr(source_fd, name, b"source-owned opaque value")
    except OSError as error:
        pytest.skip(f"test filesystem does not support user-owned xattrs: {error.errno}")
    finally:
        os.close(source_fd)

    class ExtraAttributeClone:
        @staticmethod
        def fclonefileat(source_fd, parent_fd, basename, flags):
            result = _fake_clone(source_fd, parent_fd, basename, flags)
            fd = os.open(basename, os.O_RDONLY, dir_fd=parent_fd)
            try:
                _set_test_xattr(fd, extra, b"opaque candidate value")
            finally:
                os.close(fd)
            return result

    _install_fake_native(monkeypatch)
    monkeypatch.setattr(M, "_system", lambda: ExtraAttributeClone())
    destination = tmp_path / "xattr-copy.py"
    result = M.write_captured(source, destination, source.read_bytes(), 0o640)

    assert result["method"] == "copy"
    assert result["reason"] == "metadata_compatibility"
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    destination_fd = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        source_attrs = dict(_test_xattrs(source_fd))
        destination_attrs = dict(_test_xattrs(destination_fd))
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert source_attrs[os.fsencode(name)] == b"source-owned opaque value"
    assert os.fsencode(extra) not in destination_attrs


def test_reference_name_collision_retries_without_touching_existing_entry(tmp_path, monkeypatch):
    source = _file(tmp_path)
    collision = tmp_path / ".materialization-first"
    collision.write_bytes(b"owned by the fixture")
    names = iter(["first", "second"])

    class CloneAvailable:
        fclonefileat = staticmethod(_fake_clone)

    _install_fake_native(monkeypatch)
    monkeypatch.setattr(M, "_system", lambda: CloneAvailable())
    monkeypatch.setattr(M.secrets, "token_hex", lambda _n: next(names))
    destination = tmp_path / "collision-copy.py"
    result = M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert result["method"] == "clone"
    assert collision.read_bytes() == b"owned by the fixture"
    assert not (tmp_path / ".materialization-second").exists()


def test_fallback_exclusive_create_does_not_overwrite_a_raced_destination(tmp_path, monkeypatch):
    source = _file(tmp_path)
    _install_fake_native(monkeypatch)

    class RacedUnsupportedClone:
        @staticmethod
        def fclonefileat(_source_fd, parent_fd, basename, _flags):
            fd = os.open(basename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent_fd)
            os.write(fd, b"concurrent replacement")
            os.close(fd)
            ctypes.set_errno(errno.ENOTSUP)
            return -1

    monkeypatch.setattr(M, "_system", lambda: RacedUnsupportedClone())
    destination = tmp_path / "raced.py"
    with pytest.raises(FileExistsError):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert destination.read_bytes() == b"concurrent replacement"
    assert not list(tmp_path.glob(".materialization-*"))


def test_candidate_hardlink_loses_custody_and_is_never_reported_as_success(tmp_path, monkeypatch):
    source = _file(tmp_path)
    sibling = tmp_path / "candidate-alias.py"

    class LinkedClone:
        @staticmethod
        def fclonefileat(source_fd, parent_fd, basename, flags):
            result = _fake_clone(source_fd, parent_fd, basename, flags)
            os.link(basename, sibling, src_dir_fd=parent_fd)
            return result

    _install_fake_native(monkeypatch)
    monkeypatch.setattr(M, "_system", lambda: LinkedClone())
    destination = tmp_path / "linked-candidate.py"
    with pytest.raises(ValueError, match="custody changed"):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert destination.stat().st_ino == sibling.stat().st_ino
    assert destination.stat().st_nlink == 2
    assert not list(tmp_path.glob(".materialization-*"))


def test_file_fsync_failure_cancels_success_and_cleans_owned_reference(tmp_path, monkeypatch):
    source = _file(tmp_path)

    class CloneAvailable:
        fclonefileat = staticmethod(_fake_clone)

    _install_fake_native(monkeypatch)
    monkeypatch.setattr(M, "_system", lambda: CloneAvailable())
    original = os.fsync
    fail_once = True

    def fail_first_fsync(fd):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise OSError(errno.EIO, "injected fsync failure")
        return original(fd)

    monkeypatch.setattr(M.os, "fsync", fail_first_fsync)
    destination = tmp_path / "fsync-failure.py"
    with pytest.raises(OSError) as error:
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert error.value.errno == errno.EIO
    assert not destination.exists()
    assert not list(tmp_path.glob(".materialization-*"))


@pytest.mark.parametrize(
    "common,total,offset,length",
    [
        (N.RETURNED, 36, 0, 0),
        (N.RETURNED, 36, 8, 0),
        (N.RETURNED | N.SECURITY, 36, 8, 0),
        (N.RETURNED, 32, -4, 0),
        (N.RETURNED | N.SECURITY, 32, -4, 0),
        (N.RETURNED, 76, 8, 44),
    ],
    ids=[
        "omitted-zero-ref-trailing",
        "omitted-trailing",
        "valid-zero-trailing",
        "omitted-negative-ref",
        "valid-zero-negative-ref",
        "omitted-nonempty-ref",
    ],
)
def test_public_acl_zero_payload_rejects_malformed_layout(
    common, total, offset, length, monkeypatch
):
    monkeypatch.setattr(N, "_abi", lambda: None)
    raw = bytearray(_native_response(total, common=common))
    struct.pack_into("=iI", raw, 24, offset, length)
    with pytest.raises(ValueError, match="security reference"):
        N._public_acl(7, _native_library(bytes(raw)))


@pytest.mark.parametrize("common", [N.RETURNED, N.RETURNED | N.SECURITY])
def test_public_acl_zero_payload_preserves_complete_bridge_control(common, monkeypatch):
    monkeypatch.setattr(N, "_abi", lambda: None)
    raw = bytearray(_native_response(32, common=common))
    # Actual installed Darwin omitted-security result has offset8,length0.
    struct.pack_into("=iI", raw, 24, 8, 0)
    assert N._public_acl(7, _native_library(bytes(raw))) is None


@pytest.mark.parametrize("common", [N.RETURNED, N.RETURNED | N.SECURITY])
def test_public_acl_zero_payload_malformed_cannot_materialize(tmp_path, monkeypatch, common):
    source = _file(tmp_path)
    raw = bytearray(_native_response(36, common=common))
    struct.pack_into("=iI", raw, 24, 8, 0)
    library = _native_library(bytes(raw))
    library.fclonefileat = _fake_clone
    _install_fake_native(monkeypatch)

    def parser_backed_observation(fd, native_library):
        N._public_acl(fd, native_library)
        return _observe(fd, native_library)

    monkeypatch.setattr(N, "observe", parser_backed_observation)
    monkeypatch.setattr(M, "_system", lambda: library)
    destination = tmp_path / "malformed-zero-reference.py"
    with pytest.raises(ValueError, match="security reference"):
        M.write_captured(source, destination, source.read_bytes(), 0o640)
    assert not destination.exists()
    assert not list(tmp_path.glob(".materialization-*"))
