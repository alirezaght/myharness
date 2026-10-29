"""Synthetic versions only; assertions never print classified file payloads."""

import hashlib
import os
import subprocess

import pytest

from harness.template_classification import ERROR, validate_record


def git(root, *args):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root, capture_output=True, check=True
    )
    return result.stdout.decode().strip()


@pytest.fixture
def classified_repo(tmp_path):
    root = tmp_path / "classified-repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Synthetic Fixture")
    path = root / "app" / ".env.example"
    path.parent.mkdir()
    raw = b"PUBLIC_PLACEHOLDER=synthetic-only\n"
    path.write_bytes(raw)
    path.chmod(0o644)
    (root / ".gitignore").write_text(".env\n")
    (root / ".env").write_text("STAGING_SECRET=NEVER_PRINT_SYNTHETIC_SECRET\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "synthetic classified input")
    head = git(root, "rev-parse", "HEAD")
    oid = git(root, "rev-parse", "HEAD:app/.env.example")
    versions = {
        "baseline": f"100644 blob {oid}",
        "HEAD": f"100644 blob {oid}",
        "index": f"100644 {oid} 0",
        "worktree": {"blob_oid": oid, "mode": "0o644", "symlink": False, "nlink": 1},
    }
    record = {
        "kind": "exact-template-classification-v1",
        "repository": str(root),
        "baseline": head,
        "HEAD": head,
        "files": [
            {
                "path": "app/.env.example",
                "versions": versions,
                "mode": 0o644,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "blob_oid": oid,
            }
        ],
    }
    return root, record


def test_exact_template_and_ignored_env_candidate(classified_repo):
    root, record = classified_repo
    assert validate_record(record) == [str(root / "app/.env.example")]


@pytest.mark.parametrize(
    "change", ["bytes", "mode", "symlink", "hardlink", "deleted", "added", "tracked-secret"]
)
def test_stale_and_unclassified_candidates_fail_closed(classified_repo, change):
    root, record = classified_repo
    path = root / "app/.env.example"
    if change == "bytes":
        path.write_text("different synthetic version")
    elif change == "mode":
        path.chmod(0o600)
    elif change == "symlink":
        path.unlink()
        path.symlink_to(root / ".env")
    elif change == "hardlink":
        os.link(path, root / "hardlink-alias")
    elif change == "deleted":
        path.unlink()
    elif change == "added":
        (root / "app/.env.new.example").write_text("new unclassified placeholder")
    else:
        git(root, "add", "--force", ".env")
    with pytest.raises(ValueError, match="template classification policy refused") as result:
        validate_record(record)
    assert str(result.value) == ERROR


def test_materialized_copy_matches_exact_bytes_and_git_identity(classified_repo, tmp_path):
    root, record = classified_repo
    copy = tmp_path / "materialized"
    subprocess.run(["git", "clone", "-q", str(root), str(copy)], capture_output=True, check=True)
    assert len(validate_record(record, root=copy)) == 1
    (copy / "app/.env.example").write_text("changed materialized template")
    with pytest.raises(ValueError, match="template classification policy refused"):
        validate_record(record, root=copy)
