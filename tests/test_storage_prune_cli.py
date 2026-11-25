"""`harness storage prune` is a dry run unless --apply (#39)."""

import json
import os
import time
from pathlib import Path

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


def test_storage_prune_previews_then_applies(capsys):
    allocation = _retained_settled(harness_home() / "managed-storage")

    assert main(["storage", "prune", "--seconds", "5"]) == 0
    assert "Dry run: nothing was deleted" in capsys.readouterr().out
    assert Path(allocation["path"]).is_dir()

    assert main(["storage", "prune", "--apply", "--seconds", "5", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reclaimed"] == 1 and result["reclaimed_logical_bytes"] >= 512
    assert not Path(allocation["path"]).exists()
