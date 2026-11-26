"""`harness storage prune` is a dry run unless --apply (#39)."""

import json
import os
import time
from pathlib import Path

import pytest

from harness import cli_storage
from harness.cli import main
from harness.storage import harness_home, load_core


def _retained_settled(root):
    lifecycle = load_core()["storage_lifecycle"]
    manager = lifecycle.StorageManager(
        root,
        policy={"minimum_free_bytes": 0},
        verify_owner=lambda record, reference: {
            "outcome": "passed",
            "settled": False,
            "accounting_complete": True,
            "source_current": True,
            "classification_current": True,
        },
        verify_publication=lambda record, reference: None,
    )
    allocation = manager.allocate(
        owner={"adapter_kind": "harness", "identity": "check", "reservation": "r"},
        allocation_class="operation-scratch",
        declaration_digest="c" * 64,
    )
    # An owner pid above every platform's pid range is provably gone.
    manager.mark_running(
        allocation["allocation_id"],
        generation=allocation["generation"],
        owner_receipt={"handle": "h", "pid": 2**22 + 17},
    )
    (Path(allocation["path"]) / "fixture").write_bytes(b"x" * 512)
    manager.retain(allocation["allocation_id"], reason=lifecycle.UNPROVEN)
    manager.finalize(
        allocation["allocation_id"], publication_reference={"p": 1}, completion_reference={"p": 1}
    )
    record = root / "records" / (allocation["allocation_id"] + ".json")
    stamp = time.time() - lifecycle.DEFAULT_POLICY["settlement_grace_seconds"] - 1
    os.utime(record, (stamp, stamp))
    return allocation


@pytest.mark.parametrize("observation", ["settled", "unavailable", "in_use"])
def test_storage_prune_previews_then_applies(capsys, monkeypatch, observation):
    allocation = _retained_settled(harness_home() / "managed-storage")
    record = (
        harness_home() / "managed-storage" / "records" / (allocation["allocation_id"] + ".json")
    )
    original_record = record.read_bytes()
    observe_calls = []
    manager_for_operator = cli_storage.manager_for_operator

    def observed(directory, *, deadline):
        assert directory == harness_home() / "managed-storage" / "allocations"
        assert deadline > time.monotonic()
        observe_calls.append(directory)
        if observation == "unavailable":
            raise load_core()["storage_filesystem"].StorageError("process_observation_unavailable")
        return {
            "pids": {os.getpid()},
            "groups": set(),
            "sessions": set(),
            "users": (
                {allocation["allocation_id"]: {os.getpid()}} if observation == "in_use" else {}
            ),
        }

    def controlled_manager(*, root, create):
        assert root is None and create is False
        manager = manager_for_operator(root=root, create=create)
        # CLI behavior uses the real manager and filesystem; only the host-wide
        # process census is controlled. Real census coverage lives in
        # al/tests/test_storage_reclaim.py.
        manager.observe = observed
        return manager

    monkeypatch.setattr(cli_storage, "manager_for_operator", controlled_manager)

    assert main(["storage", "prune", "--seconds", "5"]) == 0
    assert "Dry run: nothing was deleted" in capsys.readouterr().out
    assert Path(allocation["path"]).is_dir()
    assert record.read_bytes() == original_record

    assert main(["storage", "prune", "--apply", "--seconds", "5", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(observe_calls) == 2
    if observation == "settled":
        assert result["reclaimed"] == 1 and result["reclaimed_logical_bytes"] >= 512, result
        assert not Path(allocation["path"]).exists()
    else:
        reason = "settlement_undecidable" if observation == "unavailable" else "allocation_in_use"
        assert result["reclaimed"] == 0 and result["reclaimed_logical_bytes"] == 0, result
        assert result["retained_by_reason"] == {reason: 1}, result
        if observation == "unavailable":
            assert result["observation_error"] == "process_observation_unavailable", result
        assert record.read_bytes() == original_record
        assert (Path(allocation["path"]) / "fixture").read_bytes() == b"x" * 512
