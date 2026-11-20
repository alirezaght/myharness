"""Exact protected files and ordinary directories remain visible; opaque roots are not traversed."""

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
        if "--" in args:
            boundary = args.index("--")
            options = args[:boundary]
            # Ignore-rule inventory also queries cached files; count protected scopes only.
            if (options and options[0] == "ls-files" and "--cached" not in options
                    and {"--others", "-z"} <= set(options)):
                queries.append(args[boundary + 1 :])
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


def test_opaque_regular_scope_keeps_boundary_gap_without_descendant_scan(repo, monkeypatch):
    assert stat.S_ISREG((repo / "svc/app.py").lstat().st_mode)
    queries = count_scopes(monkeypatch)
    scan = F.Snapshot(repo, None, ["svc/app.py"], ["svc"])
    assert queries == []
    assert scan.gaps and "svc/app.py" not in scan.files


def test_other_opaque_root_does_not_hide_an_ordinary_protected_directory(repo, monkeypatch):
    put(repo, ".gitignore", "visible/\n")
    put(repo, "visible/explicit.py")
    queries = count_scopes(monkeypatch)
    scan = F.Snapshot(repo, None, ["visible"], ["svc"])
    assert scan.files["visible/explicit.py"] == b"VALUE = 1\n"
    assert "svc/app.py" not in scan.files
    assert queries  # Observe scoped queries even when opaque-exclusion flags precede -z.
    assert all(scope != "svc" and not scope.startswith("svc/")
               for query in queries for scope in query)
    assert scan.gaps
