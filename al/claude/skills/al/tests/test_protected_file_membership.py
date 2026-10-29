"""Exact protected files need no descendant scan; directories retain full discovery."""

import stat

import al_repository_facts as F
import pytest


def put(repo, name, text="VALUE = 1\n"):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def count_scopes(monkeypatch):
    queries = []
    original = F.git_records

    def counted(root, args, limits):
        if args[:3] == ["ls-files", "--others", "-z"] and "--" in args:
            queries.append(args[args.index("--") + 1 :])
        return original(root, args, limits)

    monkeypatch.setattr(F, "git_records", counted)
    return queries


def test_regular_file_scopes_preserve_ignored_bytes_without_per_file_git(repo, monkeypatch):
    put(repo, ".gitignore", "build/\nhidden/\n")
    put(repo, "build/source.py")
    put(repo, "hidden/explicit.py")
    scopes = ["svc/app.py", "build/source.py", "hidden/explicit.py", "build/source.py"]
    queries = count_scopes(monkeypatch)
    scan = F.Snapshot(repo, None, scopes, [])
    assert {"build/source.py", "hidden/explicit.py", "svc/app.py"} <= scan.files.keys()
    assert scan.files["hidden/explicit.py"] == b"VALUE = 1\n"
    assert queries == [["svc"]]
    assert scan.stable()
    assert queries == [["svc"], ["svc"]]


def test_mixed_nested_directories_keep_generated_override_semantics(repo, monkeypatch):
    put(repo, ".gitignore", "build/\n")
    put(repo, "build/one.py")
    put(repo, "build/node_modules/two.py")
    put(repo, "build/node_modules/__pycache__/three.py")
    put(repo, "build/node_modules/__pycache__/deeper/node_modules/excluded.py")
    scopes = [
        "build",
        "build/node_modules",
        "build/node_modules/__pycache__",
        "build/one.py",
        "build",
    ]
    queries = count_scopes(monkeypatch)
    scan = F.Snapshot(repo, None, scopes, [])
    assert {
        "build/one.py",
        "build/node_modules/two.py",
        "build/node_modules/__pycache__/three.py",
    } <= scan.files.keys()
    assert "build/node_modules/__pycache__/deeper/node_modules/excluded.py" not in scan.files
    assert queries == [
        ["build"],
        ["build/node_modules"],
        ["build/node_modules/__pycache__"],
        ["svc"],
    ]
    assert scan.stable()
    put(repo, "build/new.py")
    assert not scan.stable()


@pytest.mark.parametrize("replacement", ["directory", "symlink", "missing"])
def test_regular_file_type_changes_after_first_listing_invalidate(repo, monkeypatch, replacement):
    path = put(repo, "explicit.py")
    scan = F.Snapshot(repo, None, ["explicit.py"], [])
    queries = count_scopes(monkeypatch)
    path.unlink()
    if replacement == "directory":
        put(repo, "explicit.py/new.py")
    elif replacement == "symlink":
        path.symlink_to(repo / "svc/app.py")
    assert not scan.stable()
    assert queries == [["explicit.py"], ["svc"]]
    if replacement == "symlink":
        assert any(g["code"] == "symlink-boundary" for g in scan.gaps)


def test_opaque_regular_scope_keeps_conservative_scan_and_gap(repo, monkeypatch):
    assert stat.S_ISREG((repo / "svc/app.py").lstat().st_mode)
    queries = count_scopes(monkeypatch)
    scan = F.Snapshot(repo, None, ["svc/app.py"], ["svc"])
    assert queries == [["svc"], ["svc/app.py"]]
    assert scan.gaps and "svc/app.py" not in scan.files
