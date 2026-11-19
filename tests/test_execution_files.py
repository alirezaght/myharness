"""Ordinary source proofs share a stable descriptor and honest byte cursors."""

import hashlib
import os

import pytest

from harness.execution_files import read_file


def test_source_proof_is_for_full_bytes_on_every_page(tmp_path):
    raw = ("bounded α🧭 source\n" * 9000).encode()
    (tmp_path / "source.py").write_bytes(raw)
    offset, pages, versions = 0, [], []
    while True:
        page = read_file(tmp_path, "source.py", offset=offset, limit=17001)
        assert page["sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        assert page["size"] == len(raw) and page["hash_bytes"] == len(raw)
        assert page["next_offset"] == offset + page["page_bytes"]
        assert page["text"] == raw[offset : page["next_offset"]].decode(errors="replace")
        pages.append(raw[offset : page["next_offset"]])
        versions.append(page["version"])
        offset = page["next_offset"]
        if not page["truncated"]:
            break
    assert b"".join(pages) == raw and all(v == versions[0] for v in versions)
    assert versions[0]["size"] == len(raw)


@pytest.mark.parametrize("mutation", ["write", "replace", "replace-parent"])
def test_source_proof_refuses_movement_during_hash(tmp_path, monkeypatch, mutation):
    parent = tmp_path / "app"
    parent.mkdir()
    path = parent / "source.py"
    path.write_bytes(b"original source\n" * 20000)
    original = os.read
    changed = False

    def racing_read(fd, amount):
        nonlocal changed
        raw = original(fd, amount)
        if not changed:
            changed = True
            if mutation == "replace-parent":
                parent.rename(tmp_path / "old-app")
                parent.mkdir()
                (parent / "source.py").write_bytes(b"replacement")
            elif mutation == "replace":
                other = tmp_path / "replacement"
                other.write_bytes(b"replacement")
                other.replace(path)
            else:
                path.write_bytes(b"changed")
        return raw

    monkeypatch.setattr(os, "read", racing_read)
    with pytest.raises(ValueError, match="changed"):
        read_file(tmp_path, "app/source.py")


def test_ordinary_proofs_preserve_secret_alias_and_special_file_refusals(tmp_path):
    (tmp_path / ".env.example").write_text("synthetic secret")
    (tmp_path / "source.py").write_text("ordinary")
    (tmp_path / "alias.py").symlink_to(tmp_path / "source.py")
    os.link(tmp_path / "source.py", tmp_path / "hard.py")
    os.mkfifo(tmp_path / "pipe")
    for path in (".env.example", "alias.py", "hard.py", "pipe", "../outside"):
        with pytest.raises((ValueError, OSError)):
            read_file(tmp_path, path)


def test_large_source_keeps_pages_without_claiming_a_full_hash(tmp_path):
    from harness.execution_files import MAX_HASH_BYTES

    path = tmp_path / "large.py"
    with path.open("wb") as stream:
        stream.truncate(MAX_HASH_BYTES + 2)
    page = read_file(tmp_path, "large.py", offset=MAX_HASH_BYTES, limit=2)
    assert page["text"] == "\0\0" and not page["truncated"]
    assert page["sha256"] is None and page["hash_bytes"] == 0
    assert page["hash_status"] == "unavailable:source_exceeds_hash_budget"
    assert page["max_hash_bytes"] == MAX_HASH_BYTES and page["size"] == MAX_HASH_BYTES + 2
    assert page["next_offset"] == MAX_HASH_BYTES + 2
    beyond = read_file(tmp_path, "large.py", offset=MAX_HASH_BYTES + 5)
    assert beyond["page_bytes"] == 0 and not beyond["truncated"]
    assert beyond["version"] == page["version"]
