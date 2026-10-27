"""Private dependency copies preserve bytes, tracked source and original aliases."""

import os
import subprocess

import pytest

from harness.dependency_layout import inspect, reconcile
from harness.execution_contract import digest


def test_ignored_hardlinks_are_reconciled_without_changing_source(sample_repo, tmp_path):
    (sample_repo / ".gitignore").write_text("node_modules/\n")
    directory = sample_repo / "node_modules/pkg"
    directory.mkdir(parents=True)
    first = directory / "binary"
    first.write_bytes(b"fixed binary fixture")
    first.chmod(0o755)
    second = directory / "alias"
    os.link(first, second)
    external = tmp_path / "external"
    os.link(first, external)
    before = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=sample_repo)
    plan = inspect(sample_repo, ["node_modules"])
    assert len(plan["hardlinks"]) == 2 and not plan["ready"]
    receipt = reconcile(plan, digest(plan))
    assert receipt["source_unchanged"] and receipt["guard_weakened"] is False
    assert (
        first.read_bytes()
        == second.read_bytes()
        == external.read_bytes()
        == b"fixed binary fixture"
    )
    assert first.stat().st_nlink == second.stat().st_nlink == external.stat().st_nlink == 1
    assert first.stat().st_mode & 0o777 == 0o755
    assert subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=sample_repo) == before


def test_stale_or_tracked_reconciliation_is_refused(sample_repo, tmp_path):
    (sample_repo / ".gitignore").write_text("node_modules/\n")
    folder = sample_repo / "node_modules"
    folder.mkdir()
    source = folder / "a"
    source.write_text("original")
    os.link(source, folder / "b")
    plan = inspect(sample_repo, ["node_modules"])
    source.write_text("changed")
    with pytest.raises(ValueError, match="stale"):
        reconcile(plan, digest(plan))
    source.unlink()
    source.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="escapes|symlink"):
        inspect(sample_repo, ["node_modules/a"])
