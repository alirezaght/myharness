"""Editorial reuse must retain original execution and destination provenance."""

import json
from pathlib import Path

import pytest

from al.tests.editorial_receipt_helpers import latest, prepare_readers, run_reader, state, succeed
from al.tests.runtime_run_helpers import launched, public_run
from al.tests.test_task_editorial_revision import editorial, request_for


# Platform-agnostic flow: the shared scripts are byte-identical across platforms (parity test).
@pytest.mark.parametrize("platform", ["codex"])
def test_editorial_overlay_reuses_real_original_execution_without_new_child(launched, platform):
    initial = prepare_readers(launched, platform)
    succeed(run_reader(launched, platform))
    original_state = state(launched, platform)
    original = latest(original_state, "reader")
    assert original["status"] == "PASS" and original["pid"]
    receipt_ref = original["execution_receipt"]
    raw = Path(receipt_ref["stored_path"]).read_bytes()
    receipt = json.loads(raw)
    assert receipt["kind"] == "execution-receipt-v2" and receipt["schema_version"] == 2
    assert receipt["projection"]["kind"] == "execution-projection-v2"
    assert (
        receipt["projection"]["verification_authority"]["artifact"]
        == initial["proof_admission"]["authority"]
    )
    assert "task_hash" not in receipt["projection"]
    changed = succeed(editorial(launched, platform, request_for(original_state)))
    document = json.loads(changed.stdout)["document"]["hash"]
    succeed(run_reader(launched, platform, "duplicate"))
    reused = state(launched, platform)
    target = latest(reused, "duplicate")
    assert target["status"] == "PASS" and target["pid"] is None
    assert "execution_receipt" not in target and "reuse" not in target
    association = json.loads(Path(target["execution_association"]["stored_path"]).read_bytes())
    assert association["kind"] == "execution-association-v2"
    assert association["receipt_ref"] == receipt_ref
    assert association["document_hash"] == document
    assert (
        association["editorial_lineage"]["from_document_hash"]
        == receipt["projection"]["document_hash"]
    )
    assert association["editorial_lineage"]["to_document_hash"] == document
    assert len(association["editorial_lineage"]["overlay_refs"]) == 1
    assert target["reservation"] != original["reservation"]
    assert len([entry for entry in reused["evidence"]["entries"] if entry.get("pid")]) == 1
    assert Path(receipt_ref["stored_path"]).read_bytes() == raw
    second = request_for(reused, "another-overlay", document)
    succeed(editorial(launched, platform, second))
    succeed(run_reader(launched, platform, "duplicate"))
    again = state(launched, platform)
    later = latest(again, "duplicate")
    assert later["status"] == "PASS" and later["pid"] is None
    assert len([entry for entry in again["evidence"]["entries"] if entry.get("pid")]) == 1
    association2 = json.loads(Path(later["execution_association"]["stored_path"]).read_bytes())
    assert association2["receipt_ref"] == receipt_ref
    assert len(association2["editorial_lineage"]["overlay_refs"]) == 2
    assert original["id"] != target["id"] != later["id"]
    assert Path(receipt_ref["stored_path"]).read_bytes() == raw
    assert public_run(launched, platform, "show").returncode == 0
    Path(original["log"]).write_text("Altered original execution output\n")
    succeed(run_reader(launched, platform, "duplicate"))
    after_tamper = state(launched, platform)
    fresh = latest(after_tamper, "duplicate")
    assert fresh["status"] == "PASS" and fresh["pid"]
    assert "execution_association" not in fresh
    assert len([entry for entry in after_tamper["evidence"]["entries"] if entry.get("pid")]) == 2
    assert Path(receipt_ref["stored_path"]).read_bytes() == raw
