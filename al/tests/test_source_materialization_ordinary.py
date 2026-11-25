"""Explicit ordinary fixture copies retain custody/security checks; no clone qualification claim."""

import errno
import os
import stat

import pytest

from al.installer import source_materialization as M
from al.installer import source_materialization_metadata as N
from al.tests.test_source_materialization import _file, _install_fake_native, _observe


def available(monkeypatch):
    _install_fake_native(monkeypatch)

    class Library:
        def fclonefileat(self, *args):
            pytest.fail("explicit ordinary selection attempted a clone")

    monkeypatch.setattr(M, "_system", lambda: Library())


def test_explicit_copy_keeps_metadata_without_clone_or_reference(tmp_path, monkeypatch):
    available(monkeypatch)
    source = _file(tmp_path, mode=0o751)
    timestamp = 1_700_000_000_123_456_789
    os.utime(source, ns=(timestamp, timestamp))
    if hasattr(os, "setxattr") and hasattr(os, "listxattr"):
        os.setxattr(source, "user.fixture", b"ordinary-copy")
    created = []
    original = M._create_ordinary
    metadata = []
    copy_metadata = M._copy_metadata

    def remember_metadata(source_fd, destination_fd, info):
        metadata.append((info.st_atime_ns, info.st_mtime_ns))
        return copy_metadata(source_fd, destination_fd, info)

    def create(parent, name):
        created.append(name)
        return original(parent, name)

    monkeypatch.setattr(M, "_create_ordinary", create)
    monkeypatch.setattr(M, "_copy_metadata", remember_metadata)
    destination = tmp_path / "ordinary.py"
    assert M.copy2(source, destination, allow_clone=False) == str(destination)
    copied = destination.stat()
    assert (copied.st_atime_ns, copied.st_mtime_ns) == metadata[0]
    assert created == [destination.name]
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_ino != source.stat().st_ino
    assert stat.S_IMODE(destination.stat().st_mode) == 0o751
    assert destination.stat().st_mtime_ns == timestamp
    if hasattr(os, "setxattr") and hasattr(os, "listxattr"):
        assert os.getxattr(destination, "user.fixture") == b"ordinary-copy"
    result = M._materialize(source, tmp_path / "reason.py", metadata=True, allow_clone=False)
    assert result["method"] == "copy" and result["reason"] == "clone_not_requested"


@pytest.mark.parametrize("subject", ["source", "parent"])
def test_explicit_copy_still_rejects_security_drift(tmp_path, monkeypatch, subject):
    available(monkeypatch)
    source = _file(tmp_path)
    seen = 0

    def observe(fd, library):
        nonlocal seen
        value = _observe(fd, library)
        selected = stat.S_ISDIR(os.fstat(fd).st_mode) == (subject == "parent")
        if selected:
            seen += 1
            if seen > 1:
                security = (*value.security[:2], value.security[2] ^ 0o010, *value.security[3:])
                return N.MetadataObservation(security, value.times, value.stamp)
        return value

    monkeypatch.setattr(N, "observe", observe)
    with pytest.raises(ValueError, match="security metadata changed"):
        M.copy2(source, tmp_path / "denied.py", allow_clone=False)
    assert seen >= 2 and not (tmp_path / "denied.py").exists()
    assert not list(tmp_path.glob(".materialization-*"))


@pytest.mark.parametrize("case", ["existing", "source-link", "ancestor-link", "alias", "fifo"])
def test_explicit_copy_refuses_unsafe_entries(tmp_path, monkeypatch, case):
    available(monkeypatch)
    source = _file(tmp_path)
    selected, destination = source, tmp_path / "destination.py"
    if case == "existing":
        destination.write_bytes(b"preserve foreign destination")
    elif case == "source-link":
        selected = tmp_path / "source-link"
        selected.symlink_to(source)
    elif case == "ancestor-link":
        parent = tmp_path / "parent-link"
        parent.symlink_to(tmp_path, target_is_directory=True)
        selected = parent / source.name
    elif case == "alias":
        os.link(source, destination)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        selected = tmp_path / "fifo"
        os.mkfifo(selected)
    with pytest.raises((OSError, ValueError)):
        M.copy2(selected, destination, allow_clone=False)
    if case == "existing":
        assert destination.read_bytes() == b"preserve foreign destination"
    elif case == "alias":
        assert destination.stat().st_ino == source.stat().st_ino
    else:
        assert not destination.exists()


@pytest.mark.parametrize(
    "case", ["source-change", "destination-replaced", "parent-replaced", "hardlink"]
)
def test_explicit_copy_races_preserve_only_proven_custody(tmp_path, monkeypatch, case):
    available(monkeypatch)
    source = _file(tmp_path)
    parent = tmp_path / "parent"
    parent.mkdir()
    destination = parent / "result.py"
    held = tmp_path / "held"
    alias = tmp_path / "foreign-alias.py"
    original = M._finish_ordinary

    def finish(*args):
        original(*args)
        if case == "source-change":
            source.write_bytes(b"concurrently changed source")
        elif case == "destination-replaced":
            destination.rename(alias)
            destination.write_bytes(b"foreign replacement")
        elif case == "parent-replaced":
            parent.rename(held)
            parent.mkdir()
            (parent / "foreign").write_bytes(b"preserve replacement parent")
        else:
            os.link(destination, alias)

    monkeypatch.setattr(M, "_finish_ordinary", finish)
    with pytest.raises(ValueError):
        M.copy2(source, destination, allow_clone=False)
    if case == "destination-replaced":
        assert destination.read_bytes() == b"foreign replacement" and alias.exists()
    elif case == "parent-replaced":
        assert (parent / "foreign").read_bytes() == b"preserve replacement parent"
        assert not (held / destination.name).exists()
    elif case == "hardlink":
        assert destination.stat().st_ino == alias.stat().st_ino
        assert destination.stat().st_nlink == 2
    else:
        assert not destination.exists()
    assert not list(parent.glob(".materialization-*"))


def test_explicit_copy_exclusive_create_preserves_raced_destination(tmp_path, monkeypatch):
    available(monkeypatch)
    source = _file(tmp_path)
    destination = tmp_path / "raced.py"
    original = M._create_ordinary

    def race(parent, name):
        destination.write_bytes(b"racing owner")
        return original(parent, name)

    monkeypatch.setattr(M, "_create_ordinary", race)
    with pytest.raises(FileExistsError):
        M.copy2(source, destination, allow_clone=False)
    assert destination.read_bytes() == b"racing owner"


@pytest.mark.parametrize("failure", ["fsync", "malformed", "operational"])
def test_explicit_copy_does_not_turn_failures_into_success(tmp_path, monkeypatch, failure):
    available(monkeypatch)
    source = _file(tmp_path)
    destination = tmp_path / "failure.py"

    def fail(*args):
        if failure == "malformed":
            raise ValueError("malformed metadata")
        raise OSError(errno.EIO, "fixture operation failure")

    monkeypatch.setattr(
        M.os if failure == "fsync" else N, "fsync" if failure == "fsync" else "observe", fail
    )
    with pytest.raises(ValueError if failure == "malformed" else OSError):
        M.copy2(source, destination, allow_clone=False)
    assert not destination.exists() and not list(tmp_path.glob(".materialization-*"))
