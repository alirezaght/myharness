"""Run bindings whose repository is gone are stale, reported and archived restorably (#37)."""

import fcntl
import json
import os
from pathlib import Path

from al.installer.cli import parser
from al.installer.doctor import doctor
from al.installer.runtime_durable_layout import discover
from al.installer.runtime_pin_transaction import key
from al.installer.runtime_stale_runs import is_stale, prune_stale_runs, stale_runs
from al.installer.validator_origin import canonical, digest

AUTHORITY = "a" * 64


def _private_dir(path):
    if not path.parent.exists():
        _private_dir(path.parent)
    if not path.exists():
        path.mkdir(mode=0o700)
        path.chmod(0o700)
    return path


def _write(path, value):
    _private_dir(path.parent)
    raw = canonical(value) + b"\n"
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


def _binding(storage, repository, session="harness-s1", execution="exec-one"):
    """A v3 durable binding with its initialization, successor and runtime records."""
    subject = {"repository_identity": {"root": str(repository), "git_dir": None, "common_dir": None},
               "platform": "claude", "session_id": session}
    name = key(subject)
    runtime = storage / "harness-runtimes" / AUTHORITY / execution / "legacy-0"
    _private_dir(runtime)
    _write(runtime / (session + ".json"), {"session_id": session})
    initialization = dict(subject, kind="proof-admission-initialization-v3")
    raw = canonical(initialization) + b"\n"
    sha = digest(raw)
    _write(storage / "initializations" / sha, initialization)
    _private_dir(storage / "successors" / ("initial-" + name))
    value = dict(subject, schema_version=3, kind="run-pin-binding-v3", run_id="0123abcd",
                 publication_transaction="f" * 64, pin={}, status="ACTIVE",
                 initialization={"id": sha, "sha256": sha, "kind": "proof-admission-initialization-v3",
                                 "stored_path": str(storage / "initializations" / sha),
                                 "source": "protected-run-bootstrap", "captured_at": 0},
                 runtime_layout="harness-durable-epoch-v1", runtime_directory=str(runtime),
                 harness_binding={"authority_identity": AUTHORITY, "execution_id": execution,
                                  "epoch_id": "legacy-0", "authority_generation": 0})
    binding = storage / "runs" / (name + ".json")
    _write(binding, value)
    (storage / "runs" / (name + ".lock")).touch(mode=0o600)
    return binding, value


def _registry(tmp_path):
    state = _private_dir(tmp_path / "al-distribution" / "registry")
    return state, _private_dir(state / "al-run-pins")


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_only_a_missing_repository_root_is_stale(tmp_path):
    assert is_stale({"repository_identity": {"root": str(tmp_path / "gone")}})
    assert not is_stale({"repository_identity": {"root": str(tmp_path)}})
    assert not is_stale({"repository_identity": {"root": "relative/path"}})
    assert not is_stale({"repository_identity": None}) and not is_stale([])


def test_discover_skips_a_binding_whose_repository_is_gone(tmp_path):
    _, storage = _registry(tmp_path)
    live = _private_dir(tmp_path / "live-repo")
    _binding(storage, tmp_path / "deleted-repo", session="harness-gone", execution="exec-gone")
    _binding(storage, live, session="harness-live", execution="exec-live")
    found = discover(storage)
    assert [item["repository"]["root"] for item in found] == [str(live)]


def test_doctor_reports_stale_runs_without_failing(tmp_path):
    state, storage = _registry(tmp_path)
    binding, _ = _binding(storage, tmp_path / "deleted-repo")
    result = doctor(None, state, invoking=tmp_path)
    assert not any("durable run" in error for error in result["errors"])
    assert [item["binding"] for item in result["stale_runs"]] == [str(binding)]
    assert "prune-stale-runs --apply" in result["warnings"][0]


def test_prune_is_a_dry_run_by_default(tmp_path):
    state, storage = _registry(tmp_path)
    binding, _ = _binding(storage, tmp_path / "deleted-repo")
    before = _tree(storage)
    result = prune_stale_runs(state)
    assert result["apply"] is False and result["archive"] is None
    assert [item["binding"] for item in result["stale"]] == [str(binding)]
    assert len(result["stale"][0]["records"]) == 4
    assert _tree(storage) == before and not (state.parent / "runtime-archive").exists()


def test_prune_apply_archives_only_stale_records_with_a_restorable_manifest(tmp_path):
    state, storage = _registry(tmp_path)
    live = _private_dir(tmp_path / "live-repo")
    kept, _ = _binding(storage, live, session="harness-live", execution="exec-live")
    gone, value = _binding(storage, tmp_path / "deleted-repo", session="harness-gone",
                           execution="exec-gone")
    original = {p: p.read_bytes() for p in storage.rglob("*") if p.is_file()}
    result = prune_stale_runs(state, apply=True, now=0)
    archive = Path(result["archive"])
    assert archive == state.parent / "runtime-archive" / "19700101T000000Z"
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["status"] == "COMPLETE" and manifest["kind"] == "al-stale-run-archive-v1"
    moved = manifest["bindings"][0]["moved"]
    sources = {Path(m["source"]) for m in moved}
    assert sources == {gone, storage / "successors" / ("initial-" + gone.stem),
                       storage / "initializations" / value["initialization"]["sha256"],
                       Path(value["runtime_directory"])}
    assert all(not s.exists() and Path(m["archived"]).exists() for m in moved for s in [Path(m["source"])])
    assert kept.exists() and discover(storage)[0]["repository"]["root"] == str(live)
    assert stale_runs(storage) == []
    # Restoring is moving every archived path back; nothing was lost.
    for m in moved:
        os.rename(m["archived"], m["source"])
    assert {p: p.read_bytes() for p in storage.rglob("*") if p.is_file()} == original


def test_prune_skips_a_binding_whose_lock_is_held(tmp_path):
    state, storage = _registry(tmp_path)
    binding, _ = _binding(storage, tmp_path / "deleted-repo")
    descriptor = os.open(binding.with_suffix(".lock"), os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        result = prune_stale_runs(state, apply=True, now=0)
    finally:
        os.close(descriptor)
    assert result["archived"] == [] and result["skipped"][0]["reason"] == "binding lock is held"
    assert binding.exists()


def test_prune_stale_runs_command_is_a_dry_run_unless_applied(tmp_path):
    args = parser().parse_args(["prune-stale-runs", "--state-root", str(tmp_path)])
    assert args.command == "prune-stale-runs" and args.apply is False
    assert parser().parse_args(["prune-stale-runs", "--apply"]).apply is True
