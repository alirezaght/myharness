"""Publication is an authorized nonterminal fact, never source or review approval."""

import copy
import json
from pathlib import Path

import pytest

from al.tests.editorial_receipt_helpers import state, state_path, succeed
from al.tests.publication_helpers import command, prepare, get_packet
from al.tests.runtime_run_helpers import launched, public_run


# Platform-agnostic flow: the shared scripts are byte-identical across platforms (parity test).
@pytest.mark.parametrize("platform", ["codex"])
def test_reported_publication_preserves_pending_review_and_failed_code(launched, platform):
    record = prepare(launched, platform)
    baseline = state(launched, platform)
    for mutation in (
        "unknown-field",
        "foreign-run",
        "missing-check",
        "wrong-remote",
        "self-verified",
        "pretend-observer",
    ):
        forged = copy.deepcopy(record)
        if mutation == "unknown-field":
            forged["merge_allowed"] = True
        elif mutation == "foreign-run":
            forged["run_id"] = "00000000"
        elif mutation == "missing-check":
            forged["required_check_bindings"] = []
        elif mutation == "wrong-remote":
            forged["remote"]["identity"] = "foreign/review"
        elif mutation == "self-verified":
            forged["observation"]["provenance"] = "independently_verified"
        else:
            forged["observation"]["observer_binding"] = {
                "verified": True,
                "url": record["remote"]["url"],
            }
        before = state_path(launched, platform).read_bytes()
        refused = command(launched, platform, "record", forged)
        assert refused.returncode != 0, mutation
        assert state_path(launched, platform).read_bytes() == before
    pending = json.loads(succeed(command(launched, platform, "record", record)).stdout)
    assert pending["readiness"]["status"] == "pending"
    assert pending["readiness"]["code"]["ready"]
    assert pending["readiness"]["authorization"]["ready"]
    assert pending["readiness"]["ready_to_publish"]
    for result in ("published", "failed"):
        record["result"] = result
        response = json.loads(succeed(command(launched, platform, "record", record)).stdout)
        readiness = response["readiness"]
        assert readiness["status"] == (
            "published_review_pending" if result == "published" else "failed"
        )
        assert readiness["provenance"] == "reported"
        assert not readiness["review"]["ready"] and not readiness["release"]["ready"]
    from harness.execution_publication_contract import validate_delivery

    projection = response["readiness"]
    delivery = {"projection_version": 2, "status": projection["status"], "publication": projection}
    identity = {"al_session_id": projection["session_id"], "al_run_id": projection["run_id"]}
    validate_delivery(delivery, identity)
    for key, value in (
        ("release", {"ready": True, "status": "approved", "reason": "publication grants release"}),
        ("ready_to_publish", False),
        ("run_id", "foreign"),
    ):
        forged = copy.deepcopy(delivery)
        forged["publication"][key] = value
        with pytest.raises(ValueError):
            validate_delivery(forged, identity)
    current = state(launched, platform)
    for key in baseline:
        if key != "updated_at":
            assert current[key] == baseline[key], key
    assert current["status"] == "RUNNING" and current["phase"] == "PLANNING"
    assert len(current["publication_records"]) == 3
    assert not current.get("approval")
    before = state_path(launched, platform).read_bytes()
    assert command(launched, platform, "record", record).returncode != 0
    assert state_path(launched, platform).read_bytes() == before
    # A previously reported publication never turns a damaged executable receipt green.
    entry = next(row for row in current["evidence"]["entries"] if row["area"] == "publication-code")
    log = Path(entry["log"])
    original = log.read_bytes()
    log.write_bytes(original + b"altered observation\n")
    readiness = json.loads(succeed(public_run(launched, platform, "publication", "status")).stdout)
    assert not readiness["code"]["ready"] and not readiness["ready_to_publish"]
    assert readiness["status"] == "failed" and readiness["provenance"] == "reported"
    before = state_path(launched, platform).read_bytes()
    record["result"] = "published"
    refused = command(launched, platform, "record", record)
    assert refused.returncode != 0 and "code readiness" in refused.stdout + refused.stderr
    assert state_path(launched, platform).read_bytes() == before
    log.write_bytes(original)
    # Policy bytes and current candidate identity remain independent required bindings.
    policy = launched[2] / ".al/publication-policy.json"
    raw = policy.read_bytes()
    policy.write_bytes(raw + b" ")
    assert command(launched, platform, "record", record).returncode != 0
    policy.write_bytes(raw)
    assert get_packet(launched, platform)["readiness"]["code"]["ready"]
    # The synthetic URL was never contacted and no remote was configured or pushed.
    assert not (launched[2] / ".git/refs/remotes").exists()
    assert not any(path.name.startswith("remote-") for path in launched[2].parent.iterdir())
