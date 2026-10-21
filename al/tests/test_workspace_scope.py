"""Root completeness is independently reconstructed, never inferred from a subset."""

import copy
import json
from pathlib import Path
import subprocess

import pytest

from al.installer import workspace_scope as S
from al.installer.registry import canonical, digest, record_path, write_json
from al.installer.targets import resolve_target
from al.tests.activity_helpers import git_repo


@pytest.fixture
def scope_case(tmp_path, monkeypatch):
    monkeypatch.delenv("AL_RUNTIME_DIR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    source = git_repo(tmp_path / "source")
    return home, source, tmp_path / "state"


def seal(value):
    value["digest"] = digest(canonical({k: v for k, v in value.items() if k != "digest"}))
    return value


def test_nested_repositories_and_linked_worktree_are_distinct_not_common_dir_exempt(scope_case):
    home, source, state = scope_case
    personal = home / "personal"
    personal.mkdir()
    outer = git_repo(personal / "outer")
    nested = git_repo(personal / "outer/nested")
    linked = personal / "linked"
    subprocess.run(
        ["git", "-C", outer["root"], "worktree", "add", "-q", "-b", "linked", str(linked)],
        check=True,
        capture_output=True,
    )
    value = S.derive_scope(source["root"], home, state)
    roots = {row["runtime_root"] for row in value["roots"]}
    assert roots == {
        str(Path(root) / ("." + platform) / "al-runtime")
        for root in (source["root"], outer["root"], nested["root"], str(linked))
        for platform in ("codex", "claude")
    }
    found = value["enumeration"]["repositories"]
    identities = {row["root"]: row for row in found}
    assert identities[outer["root"]]["common_dir"] == identities[str(linked)]["common_dir"]
    assert identities[outer["root"]]["git_dir"] != identities[str(linked)]["git_dir"]
    assert S.revalidate_scope(json.loads(json.dumps(value)), state) == value


@pytest.mark.parametrize(
    "removed", ["invoking", "second-platform", "registered", "whole-scan-root"]
)
def test_self_consistent_omitted_scope_is_refused(scope_case, tmp_path, removed):
    home, source, state = scope_case
    registered = git_repo(tmp_path / "registered")
    target = resolve_target(repo=registered["root"])
    write_json(
        record_path(state, target["target_id"]),
        {
            "schema_version": 1,
            "target_id": target["target_id"],
            "target": target,
            "policy": "follow",
            "source": None,
            "current": None,
            "previous": None,
            "pending": None,
            "runtime_roots": [],
        },
    )
    value = S.derive_scope(source["root"], home, state)
    forged = copy.deepcopy(value)
    if removed == "whole-scan-root":
        forged["enumeration"]["observations"].pop()
    elif removed == "invoking":
        forged["roots"] = [row for row in forged["roots"] if row["repository"] != source]
    elif removed == "registered":
        forged["roots"] = [row for row in forged["roots"] if row["repository"] != registered]
        forged["registered_contributors"] = []
    else:
        forged["roots"] = [row for row in forged["roots"] if row["platform"] != "claude"]
    with pytest.raises(ValueError, match="changed or incomplete"):
        S.revalidate_scope(seal(forged), state)


def test_explicit_and_ambient_are_additive_and_include_other_platform(
    scope_case, tmp_path, monkeypatch
):
    home, source, state = scope_case
    extra = git_repo(tmp_path / "extra")
    custom = tmp_path / "custom-runtime"
    descriptor = {"repository": extra, "platform": "claude", "runtime_root": str(custom)}
    monkeypatch.setenv("AL_RUNTIME_DIR", str(custom))
    value = S.derive_scope(source["root"], home, state, explicit=[descriptor])
    assert str(custom) in {row["runtime_root"] for row in value["roots"]}
    assert {
        str(Path(extra["root"]) / ("." + platform) / "al-runtime")
        for platform in ("codex", "claude")
    } <= {row["runtime_root"] for row in value["roots"]}
    monkeypatch.delenv("AL_RUNTIME_DIR")
    with pytest.raises(ValueError, match="changed or incomplete"):
        S.revalidate_scope(value, state)
    with pytest.raises(ValueError, match="explicit repository"):
        S.derive_scope(source["root"], home, state, explicit=[str(custom)])


def test_new_repository_symlink_or_unavailable_boundary_cannot_be_empty(scope_case, monkeypatch):
    home, source, state = scope_case
    value = S.derive_scope(source["root"], home, state)
    (home / "Documents").mkdir()
    git_repo(home / "Documents/new")
    with pytest.raises(ValueError, match="changed or incomplete"):
        S.revalidate_scope(value, state)
    (home / "Documents/link").symlink_to(home / "Documents/new", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        S.derive_scope(source["root"], home, state)
    (home / "Documents/link").unlink()
    real = S.os.scandir

    def denied(path):
        if Path(path) == home / "Documents":
            raise PermissionError("inspection denied")
        return real(path)

    monkeypatch.setattr(S.os, "scandir", denied)
    with pytest.raises(PermissionError):
        S.derive_scope(source["root"], home, state)


def test_scope_limit_or_unknown_global_runtime_blocks(scope_case, monkeypatch):
    home, source, state = scope_case
    (home / "personal").mkdir()
    monkeypatch.setitem(S.LIMITS, "directories", 0)
    with pytest.raises(ValueError, match="incomplete"):
        S.derive_scope(source["root"], home, state)
    monkeypatch.setitem(S.LIMITS, "directories", 200000)
    (home / ".codex/al-runtime").mkdir(parents=True)
    with pytest.raises(ValueError, match="unsupported non-repository"):
        S.derive_scope(source["root"], home, state)
