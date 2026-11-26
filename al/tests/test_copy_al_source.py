"""Small real source copies retain exclusion policy, metadata and independent bytes."""

import shutil
import stat
from pathlib import Path

import pytest

from al.tests import conftest as C

RUNTIME_POLICY = ("__pycache__", ".pytest_cache", ".ruff_cache")
POLICIES = [pytest.param(None, id="default"), pytest.param(RUNTIME_POLICY, id="runtime-pin")]


@pytest.fixture
def tiny_source(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "tiny-al"
    root.mkdir(mode=0o700)
    members = (
        "keep.py", "loose.pyc", "nested/keep.py",
        "__pycache__/hidden", ".pytest_cache/hidden", ".ruff_cache/kept",
        "nested/__pycache__/hidden", "nested/.pytest_cache/hidden",
        "nested/.ruff_cache/kept", "nested/compiled.pyc/kept",
        "files/__pycache__", "files/.pytest_cache", "files/.ruff_cache",
    )
    for name in members:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + "\n").encode())
        path.chmod(0o640)
    (root / "empty").mkdir(mode=0o750)
    monkeypatch.setattr(C, "__file__", str(root / "tests/conftest.py"))
    monkeypatch.setattr(C, "_SOURCE_TEMPLATE", {})
    # Keep even the helper's atexit-owned template under this test's private root.
    mkdtemp = C.tempfile.mkdtemp
    monkeypatch.setattr(C.tempfile, "mkdtemp", lambda **kw: mkdtemp(dir=tmp_path, **kw))
    return root


def clone(destination, policy):
    return (C.copy_al_source(destination) if policy is None else
            C.copy_al_source(destination, ignore_patterns=policy))


def expected_files(policy):
    common = {"keep.py", "nested/keep.py"}
    if policy is None:
        return common | {".ruff_cache/kept", "nested/.ruff_cache/kept", "files/.ruff_cache"}
    return common | {"loose.pyc", "nested/compiled.pyc/kept"}


@pytest.mark.parametrize("policy", POLICIES)
def test_copy_policy_has_exact_file_directory_membership_and_metadata(tiny_source, tmp_path, policy):
    first = clone(tmp_path / "first", policy)
    second = clone(tmp_path / "second", policy)
    files = expected_files(policy)
    members = files | {"empty", "files"}
    for name in files:
        members.update(str(p) for p in Path(name).parents if str(p) != ".")
    for copied in (first, second):
        assert {p.relative_to(copied).as_posix() for p in copied.rglob("*")} == members
        for name in members:
            source, actual = tiny_source / name, copied / name
            assert stat.S_IMODE(actual.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
            if name in files:
                assert actual.read_bytes() == source.read_bytes()
                assert actual.stat().st_mtime_ns == source.stat().st_mtime_ns
                assert actual.stat().st_nlink == 1
    for name in files:
        identities = {(p.stat().st_dev, p.stat().st_ino) for p in
                      (tiny_source / name, first / name, second / name)}
        assert len(identities) == 3


@pytest.mark.parametrize("policy", POLICIES)
def test_caller_mutation_and_deletion_do_not_change_source_or_template(tiny_source, tmp_path, policy):
    expected = (tiny_source / "keep.py").read_bytes()
    first = clone(tmp_path / "first", policy)
    (first / "keep.py").write_bytes(b"caller mutation")
    (first / "nested/keep.py").unlink()
    assert (tiny_source / "keep.py").read_bytes() == expected
    assert (tiny_source / "nested/keep.py").is_file()
    shutil.rmtree(first)
    second = clone(tmp_path / "second", policy)
    assert (second / "keep.py").read_bytes() == expected
    assert (second / "nested/keep.py").read_bytes() == (tiny_source / "nested/keep.py").read_bytes()
    assert {p.relative_to(second).as_posix() for p in second.rglob("*") if p.is_file()} == expected_files(policy)


@pytest.mark.parametrize("policy", POLICIES)
def test_observed_source_changes_and_deletions_replace_cached_bytes(tiny_source, tmp_path, policy):
    first = clone(tmp_path / "first", policy)
    original = (first / "keep.py").read_bytes()
    (tiny_source / "keep.py").write_bytes(b"new source bytes and size")
    (tiny_source / "keep.py").chmod(0o700)
    (tiny_source / "nested/keep.py").unlink()
    second = clone(tmp_path / "second", policy)
    assert (second / "keep.py").read_bytes() == b"new source bytes and size"
    assert stat.S_IMODE((second / "keep.py").stat().st_mode) == 0o700
    assert not (second / "nested/keep.py").exists()
    assert (first / "keep.py").read_bytes() == original
    assert (first / "nested/keep.py").is_file()


def test_policy_identity_prevents_reuse_even_when_selected_source_states_match(tiny_source, tmp_path):
    for path in tiny_source.iterdir():
        if path.name != "keep.py":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    assert C._source_state(tiny_source) == C._source_state(tiny_source, RUNTIME_POLICY)
    first = C.copy_al_source(tmp_path / "first")
    default_template = next(iter(C._SOURCE_TEMPLATE.values()))
    second = C.copy_al_source(tmp_path / "second", ignore_patterns=RUNTIME_POLICY)
    runtime_template = next(iter(C._SOURCE_TEMPLATE.values()))
    assert runtime_template != default_template
    assert (first / "keep.py").read_bytes() == (second / "keep.py").read_bytes()
    assert (first / "keep.py").stat().st_ino != (second / "keep.py").stat().st_ino


@pytest.mark.parametrize("policy", POLICIES)
def test_copy_refuses_source_movement_without_publishing_template(tiny_source, tmp_path, monkeypatch, policy):
    real_copy = C.copy2
    changed = False

    def moving_copy(*args, **kwargs):
        nonlocal changed
        result = real_copy(*args, **kwargs)
        if not changed:
            changed = True
            (tiny_source / "keep.py").write_bytes(b"changed during genuine first construction")
        return result

    monkeypatch.setattr(C, "copy2", moving_copy)
    with pytest.raises(RuntimeError, match="source changed"):
        clone(tmp_path / "refused", policy)
    assert changed and not (tmp_path / "refused").exists()
    assert C._SOURCE_TEMPLATE == {}


@pytest.mark.parametrize("policy", POLICIES)
def test_source_observation_uses_same_policy_for_excluded_files(tiny_source, tmp_path, policy):
    patterns = C._SOURCE_IGNORED if policy is None else policy
    first = clone(tmp_path / "first", policy)
    state = C._source_state(tiny_source, patterns)
    (tiny_source / "files/.ruff_cache").write_bytes(b"changed excluded-name file")
    current = C._source_state(tiny_source, patterns)
    second = clone(tmp_path / "second", policy)
    if policy is None:
        assert current != state
        assert (second / "files/.ruff_cache").read_bytes() == b"changed excluded-name file"
        assert (first / "files/.ruff_cache").read_bytes() != b"changed excluded-name file"
    else:
        assert current == state
        assert not (first / "files/.ruff_cache").exists()
        assert not (second / "files/.ruff_cache").exists()
