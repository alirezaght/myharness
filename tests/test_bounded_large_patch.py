"""Small patches work on existing large source without losing bounds or race guards."""

import os

import pytest

from harness.execution_files import MAX_EDIT_TARGET, apply_patch, read_file

PATCH = (
    "*** Begin Patch\n*** Update File: api.yaml\n@@\n"
    "-old: reference\n+new: reference\n*** End Patch\n"
)


def test_small_patch_preserves_large_source_and_ordinary_read_bounds(tmp_path):
    original = "# existing API\n" * 10000 + "old: reference\n"
    target = tmp_path / "api.yaml"
    target.write_text(original)
    assert read_file(tmp_path, "api.yaml", limit=65536)["truncated"]
    admitted = []
    apply_patch(tmp_path, PATCH, lambda paths: admitted.append(paths))
    assert admitted == [["api.yaml"], ["api.yaml"]]
    assert target.read_text() == original.replace("old: reference", "new: reference")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "oversize", "ambiguous", "binary"])
def test_unsafe_or_unbounded_edits_preserve_source(tmp_path, kind):
    target = tmp_path / "api.yaml"
    if kind == "symlink":
        source = tmp_path / "real.yaml"
        source.write_text("old: reference\n")
        target.symlink_to(source)
    elif kind == "hardlink":
        source = tmp_path / "real.yaml"
        source.write_text("old: reference\n")
        os.link(source, target)
    elif kind == "oversize":
        target.write_bytes(b"x" * (MAX_EDIT_TARGET + 1))
    elif kind == "ambiguous":
        target.write_text("old: reference\n" * 2)
    else:
        target.write_bytes(b"\xffold: reference\n")
    before = target.read_bytes()
    with pytest.raises((ValueError, OSError, UnicodeError)):
        apply_patch(tmp_path, PATCH, lambda paths: None)
    assert target.read_bytes() == before


def test_second_authorization_race_cannot_overwrite_concurrent_source(tmp_path):
    target = tmp_path / "api.yaml"
    target.write_text("# source\n" * 10000 + "old: reference\n")
    calls = []

    def authorize(paths):
        calls.append(paths)
        if len(calls) == 2:
            target.write_text("concurrent source\n")

    with pytest.raises(ValueError, match="changed before commit"):
        apply_patch(tmp_path, PATCH, authorize)
    assert target.read_text() == "concurrent source\n"
