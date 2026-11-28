"""Abandoned runs defer installer updates until `alctl close-runs` archives them restorably (#168)."""

import fcntl
import json
import os
from pathlib import Path

import pytest

from al.installer.cli import parser
from al.installer.guards import ActiveRuns, activity_roots, require_inactive, scan_activity
from al.installer.runtime_close_runs import close_runs
from al.installer.runtime_durable_layout import discover
from al.tests.test_policy import session
from al.tests.test_stale_runs import _binding, _private_dir, _registry, _tree, _write
from al.installer.runtime_pin_transaction import key

REASON = "left behind by an interrupted issue loop"


def _state(path, repository, **kwargs):
    session(path, **kwargs)
    value = json.loads(path.read_text())
    value["identity"] = {"root": str(repository)}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path.read_bytes()


@pytest.fixture
def flat(tmp_path):
    """A repository with an abandoned flat run (pointer, companions, lock) beside a finished one."""
    state_root, storage = _registry(tmp_path)
    repository = _private_dir(tmp_path / "repository")
    runtime = _private_dir(repository / ".codex" / "al-runtime")
    original = _state(runtime / "abandoned.json", repository, status="RUNNING", phase="IMPLEMENTATION")
    _state(runtime / "finished.json", repository)
    _private_dir(runtime / "abandoned" / "evidence")
    (runtime / "abandoned" / "evidence" / "E1.log").write_text("check output\n")
    (runtime / ".abandoned.lock").touch(mode=0o600)
    (runtime / "current").write_text("abandoned\n")
    return state_root, storage, repository, runtime, original


def _close(state_root, runtime, *sessions, **kwargs):
    return close_runs(state_root, sessions, runtime_roots=[str(runtime)], invoking=runtime.parent.parent.parent, **kwargs)


def test_abandoned_flat_run_defers_updates_until_closed_and_restores(flat):
    state_root, _, repository, runtime, original = flat
    with pytest.raises(ActiveRuns) as refused:
        require_inactive([runtime])
    # The deferral names the way out instead of leaving the operator at a dead end.
    assert "potentially resumable run: RUNNING/IMPLEMENTATION" in str(refused.value)
    assert "alctl close-runs --session <id> --reason <text> --apply" in str(refused.value)

    before = _tree(repository)
    listed = _close(state_root, runtime)
    assert [(item["session_id"], item["status"], item["phase"]) for item in listed["closable"]] == [
        ("abandoned", "RUNNING", "IMPLEMENTATION")]
    assert listed["closed"] == [] and _tree(repository) == before  # a dry run moves nothing

    result = _close(state_root, runtime, "abandoned", reason=REASON, apply=True)
    assert [entry["session_id"] for entry in result["closed"]] == ["abandoned"] and not result["skipped"]
    require_inactive([runtime])  # the update is no longer deferred
    assert (runtime / "finished.json").is_file()
    assert not any(os.path.lexists(runtime / name) for name in ("abandoned.json", "abandoned", ".abandoned.lock", "current"))

    manifest = json.loads((Path(result["archive"]) / "manifest.json").read_text())
    assert manifest["status"] == "COMPLETE" and manifest["reason"] == REASON
    moved = manifest["runs"][0]["moved"]
    # The pointer leaves before the state, so the root never holds a dangling pointer.
    assert [Path(item["source"]).name for item in moved] == ["current", "abandoned.json", "abandoned", ".abandoned.lock"]
    assert all(item["moved"] for item in moved)
    archived = {Path(item["source"]).name: Path(item["archived"]) for item in moved}
    assert archived["abandoned.json"].read_bytes() == original  # no state byte changes
    assert (archived["abandoned"] / "evidence" / "E1.log").read_text() == "check output\n"
    # Flat files archive beside the runtime root, never inside the tree workspace inventory walks.
    local = runtime.parent / "al-runtime-archive" / manifest["archived_at"] / "manifest.json"
    assert json.loads(local.read_text()) == manifest
    assert sorted(p.name for p in runtime.iterdir()) == ["finished.json"]
    assert (runtime.parent / "al-runtime-archive" / ".gitignore").read_text() == "*\n"

    for item in reversed(moved):  # the documented restore
        os.rename(item["archived"], item["source"])
    assert (runtime / "current").read_text() == "abandoned\n"
    with pytest.raises(ActiveRuns):
        require_inactive([runtime])


def test_flat_run_binding_records_are_archived_with_it(flat):
    state_root, storage, repository, runtime, _ = flat
    subject = {"repository_identity": {"root": str(repository), "git_dir": None, "common_dir": None},
               "platform": "codex", "session_id": "abandoned"}
    binding = storage / "runs" / (key(subject) + ".json")
    _write(binding, dict(subject, schema_version=2, status="ACTIVE", run_id="abc12345"))
    (binding.with_suffix(".lock")).touch(mode=0o600)
    other = storage / "runs" / (key(dict(subject, platform="claude")) + ".json")
    _write(other, dict(subject, platform="claude", schema_version=2, status="ACTIVE", run_id="def12345"))

    result = _close(state_root, runtime, "abandoned", reason=REASON, apply=True)
    entry = result["closed"][0]
    assert entry["binding"] == str(binding) and not binding.exists()
    assert str(binding) in [item["source"] for item in entry["moved"]]
    assert other.is_file()  # another platform's run of the same session name is not this run


def test_durable_run_closes_with_its_binding_records(tmp_path):
    state_root, storage = _registry(tmp_path)
    repository = _private_dir(tmp_path / "live-repository")
    binding, value = _binding(storage, repository, session="harness-left")
    runtime = Path(value["runtime_directory"])
    _state(runtime / "harness-left.json", repository, status="WAITING_FOR_USER", phase="REVIEW")
    roots = activity_roots([], state_root=state_root)
    assert scan_activity(roots)["blockers"][0]["category"] == "terminal-workflow"

    listed = close_runs(state_root, invoking=tmp_path)
    assert [(item["session_id"], item["layout"]) for item in listed["closable"]] == [("harness-left", "durable")]
    result = close_runs(state_root, ["harness-left"], reason=REASON, apply=True, invoking=tmp_path)
    assert [entry["session_id"] for entry in result["closed"]] == ["harness-left"]
    assert discover(storage) == [] and not binding.exists() and not runtime.exists()
    assert not scan_activity(activity_roots([], state_root=state_root))["blockers"]
    archived = [Path(item["archived"]) for item in result["closed"][0]["moved"]]
    assert all(path.exists() and Path(result["archive"]) in path.parents for path in archived)


def test_only_named_potentially_resumable_runs_close(flat):
    state_root, _, repository, runtime, _ = flat
    # An unresolved writer may still be live: it is reported, never closed.
    _state(runtime / "writing.json", repository, status="RUNNING", phase="IMPLEMENTATION",
           writers={"w1": {"id": "w1", "state": "RUNNING"}})
    listed = _close(state_root, runtime)
    assert [item["session_id"] for item in listed["closable"]] == ["abandoned"]
    assert [Path(b["path"]).name for b in listed["other_blockers"]] == ["writing.json"]

    before = _tree(repository)
    result = _close(state_root, runtime, "writing", "finished", "unknown", reason=REASON, apply=True)
    assert [item["session_id"] for item in result["skipped"]] == ["writing", "finished", "unknown"]
    assert result["closed"] == [] and result["archive"] is None and _tree(repository) == before
    with pytest.raises(ValueError, match="at least one --session"):
        _close(state_root, runtime, reason=REASON, apply=True)
    with pytest.raises(ValueError, match="--reason"):
        _close(state_root, runtime, "abandoned", reason=" ", apply=True)
    assert _tree(repository) == before


def test_a_run_whose_lock_is_held_is_left_alone(flat):
    state_root, _, repository, runtime, _ = flat
    before = _tree(repository)
    descriptor = os.open(runtime / ".abandoned.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _close(state_root, runtime, "abandoned", reason=REASON, apply=True)
    finally:
        os.close(descriptor)
    assert result["skipped"] == [{"session_id": "abandoned", "reason": "run lock is held"}]
    assert _tree(repository) == before
    manifest = json.loads((Path(result["archive"]) / "manifest.json").read_text())
    assert manifest["status"] == "COMPLETE" and manifest["runs"] == []


def test_cli_parses_close_runs():
    args = parser().parse_args(["close-runs", "--session", "a", "--session", "b", "--reason", "gone",
                                "--runtime-root", "/r", "--apply"])
    assert (args.command, args.session, args.reason, args.runtime_root, args.apply) == (
        "close-runs", ["a", "b"], "gone", ["/r"], True)
    assert parser().parse_args(["close-runs"]).apply is False


def test_closed_claude_run_leaves_the_workspace_inventory_clean(tmp_path):
    import hashlib
    import subprocess

    from al.installer.activity_inventory import inventory_activity, repository_identity
    from al.installer.activity_observation import locked_activity
    from al.installer.activity_retirement import seal

    state_root, _ = _registry(tmp_path)
    repository = _private_dir(tmp_path / "repository")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    runtime = _private_dir(repository / ".claude" / "al-runtime")
    (runtime / ".gitignore").write_text("*\n")  # as AL creates every runtime root
    _state(runtime / "left.json", repository, status="RUNNING", phase="PLANNING")
    (runtime / ".left.lock").touch(mode=0o600)
    (runtime / ".left.lock.holder").write_text("123\n")
    conversations = _private_dir(runtime / "conversations")
    native = "conversation-1"
    pointer = conversations / (hashlib.sha256(native.encode()).hexdigest() + ".json")
    pointer.write_text(json.dumps({"conversation_id": native, "session_id": "left"}))
    pointer.with_suffix(".lock").touch(mode=0o600)

    result = _close(state_root, runtime, "left", reason=REASON, apply=True)
    moved = [Path(item["source"]).relative_to(runtime).as_posix() for item in result["closed"][0]["moved"]]
    assert moved[:2] == [pointer.relative_to(runtime).as_posix(), pointer.with_suffix(".lock").relative_to(runtime).as_posix()]
    assert ".left.lock.holder" in moved and not os.path.lexists(pointer)

    rows = [{"repository": repository_identity(repository), "platform": "claude", "runtime_root": str(runtime)}]
    inventory = inventory_activity(rows, invoking_run=None)
    assert inventory["stable"], [root.get("errors") for root in inventory["roots"]]
    inventory = dict(inventory, kind="workspace-activity-inventory")
    inventory.pop("digest", None)
    with locked_activity(seal(inventory)):
        pass  # no archived lock or dangling pointer is left for the workspace to trip on
    # The archived run stays out of Git, as the runtime root does.
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repository,
                            check=True, capture_output=True, text=True)
    assert status.stdout == ""


def test_a_pointer_naming_another_session_stays(flat):
    state_root, _, _, runtime, _ = flat
    (runtime / "current").write_text("finished\n")
    _close(state_root, runtime, "abandoned", reason=REASON, apply=True)
    assert (runtime / "current").read_text() == "finished\n"
    require_inactive([runtime])


def test_a_run_that_changes_after_listing_is_left_alone(flat, monkeypatch):
    from al.installer import runtime_close_runs

    state_root, _, repository, runtime, _ = flat
    original = runtime_close_runs._locked

    def resumed(lock):  # the run saves new state between the listing and the locked re-check
        if lock.name == ".abandoned.lock":
            _state(runtime / "abandoned.json", repository, status="RUNNING", phase="REVIEW")
        return original(lock)

    monkeypatch.setattr(runtime_close_runs, "_locked", resumed)
    before = _tree(repository)
    result = _close(state_root, runtime, "abandoned", reason=REASON, apply=True)
    assert result["skipped"] == [{"session_id": "abandoned", "reason": "run changed while closing"}]
    assert result["closed"] == [] and _tree(repository) == before


def test_a_durable_run_whose_session_lock_is_held_is_left_alone(tmp_path):
    state_root, storage = _registry(tmp_path)
    repository = _private_dir(tmp_path / "live-repository")
    binding, value = _binding(storage, repository, session="harness-busy")
    runtime = Path(value["runtime_directory"])
    _state(runtime / "harness-busy.json", repository, status="RUNNING", phase="IMPLEMENTATION")
    lock = runtime / ".harness-busy.lock"
    lock.touch(mode=0o600)
    before = _tree(storage)
    descriptor = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)  # an AL command of the run is in flight
        result = close_runs(state_root, ["harness-busy"], reason=REASON, apply=True, invoking=tmp_path)
    finally:
        os.close(descriptor)
    assert result["skipped"] == [{"session_id": "harness-busy", "reason": "run lock is held"}]
    assert _tree(storage) == before and binding.is_file()
