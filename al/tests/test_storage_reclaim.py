"""Later-proven settlement reclaim of retained managed scratch (#39).

Decision tests inject the process observation; one test uses the real scan.
Every manager lives under tmp_path.
"""

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from al.installer import cli
from al.installer import storage_lifecycle as lifecycle
from al.installer.storage_filesystem import StorageError, identity
from al.installer.storage_inventory import retained_summary

GRACE = lifecycle.DEFAULT_POLICY["settlement_grace_seconds"]
RETENTION = lifecycle.DEFAULT_POLICY["retention_seconds"]


def quiet(directory, *, deadline):
    return {"pids": {os.getpid()}, "groups": set(), "sessions": set(), "users": {}}


class Observer:
    """A controllable process observation."""

    def __init__(self):
        self.value = quiet(None, deadline=None)
        self.error = None
        self.calls = 0

    def __call__(self, directory, *, deadline):
        self.calls += 1
        if self.error:
            raise StorageError(self.error)
        return self.value


@pytest.fixture
def setup(tmp_path):
    report = tmp_path / "report.json"
    report.write_text('{"result":"passed"}')
    facts = {
        "outcome": "passed",
        "settled": False,
        "accounting_complete": True,
        "source_current": True,
        "classification_current": True,
    }

    def publication(record, reference):
        return {
            "binding": {
                "owner": record["owner"],
                "allocation_id": record["allocation_id"],
                "declaration_digest": record["declaration_digest"],
            },
            "artifacts": [
                {
                    "path": str(report),
                    "identity": identity(report.stat()),
                    "size": report.stat().st_size,
                    "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                }
            ],
        }

    observer = Observer()
    manager = lifecycle.StorageManager(
        tmp_path / "managed-storage",
        policy={"minimum_free_bytes": 0},
        verify_owner=lambda record, reference: dict(facts),
        verify_publication=publication,
        observe=observer,
    )
    return manager, observer, facts


def retained(manager, *, files=(), pid=4242, finalized=True):
    """An allocation retained the way macOS runs leave it today."""
    allocation = manager.allocate(
        owner={"adapter_kind": "evidence", "identity": "run", "reservation": "r"},
        allocation_class="verification-fixtures",
        declaration_digest="a" * 64,
    )
    manager.mark_running(
        allocation["allocation_id"],
        generation=allocation["generation"],
        owner_receipt={"pid": pid, "token": "t"},
    )
    for name, size in files:
        path = Path(allocation["path"]) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    manager.retain(allocation["allocation_id"], reason=lifecycle.UNPROVEN)
    if finalized:
        result = manager.finalize(
            allocation["allocation_id"],
            publication_reference={"p": 1},
            completion_reference={"p": 1},
        )
        assert result == {
            "state": "RETAINED",
            "reason": lifecycle.UNPROVEN,
            "allocation_id": allocation["allocation_id"],
        }
    return allocation


def age(manager, allocation, seconds):
    path = manager.root / "records" / (allocation["allocation_id"] + ".json")
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def record(manager, allocation):
    with manager._locked() as directories:
        return manager._record(directories, allocation["allocation_id"])


def test_passed_unproven_allocation_is_reclaimed_after_grace_with_accounting(setup):
    manager, observer, _ = setup
    allocation = retained(manager, files=[("tmp/a", 1000), ("tmp/nested/b", 24)])

    early = manager.reclaim()
    assert early["results"] == [] and observer.calls == 0
    assert early["retained_by_reason"] == {"settlement_grace_period": 1}
    assert Path(allocation["path"]).is_dir()

    age(manager, allocation, GRACE + 1)
    result = manager.reclaim()
    [receipt] = result["results"]
    assert receipt["state"] == "REMOVED" and receipt["reason"] == lifecycle.SETTLED_LATER
    assert receipt["logical_bytes"] >= 1024
    assert result["reclaimed_logical_bytes"] == receipt["logical_bytes"]
    assert not Path(allocation["path"]).exists()
    assert record(manager, allocation)["state"] == "REMOVED"
    # Idempotent; the evidence owner may still finalize and gets the receipt.
    assert manager.reclaim()["results"] == []
    again = manager.finalize(
        allocation["allocation_id"], publication_reference={"p": 1}, completion_reference={"p": 1}
    )
    assert again == receipt
    summary = retained_summary(manager)
    assert summary["retained"] == 0 and summary["states"] == {"REMOVED": 1}


def test_in_use_and_live_owner_tree_stay_retained(setup):
    manager, observer, _ = setup
    used = retained(manager, pid=5001)
    group = retained(manager, pid=5002)
    session = retained(manager, pid=5003)
    for allocation in (used, group, session):
        age(manager, allocation, GRACE + 1)
    observer.value = {
        "pids": {os.getpid(), 777},
        "groups": {5002},
        "sessions": {5003},
        "users": {used["allocation_id"]: {777}},
    }
    result = manager.reclaim()
    assert result["results"] == []
    assert result["retained_by_reason"] == {
        "allocation_in_use": 1,
        "owner_process_alive": 2,
    }
    reasons = {row["allocation_id"]: row["reason"] for row in result["retained"]}
    assert reasons[used["allocation_id"]] == "allocation_in_use"
    for allocation in (used, group, session):
        assert Path(allocation["path"]).is_dir()
        assert record(manager, allocation)["retention_reason"] == lifecycle.UNPROVEN


def test_undecidable_observation_deletes_nothing_for_either_rule(setup):
    manager, observer, facts = setup
    settled = retained(manager)
    facts["outcome"] = "failed"
    failed = retained(manager)
    age(manager, settled, GRACE + 1)
    age(manager, failed, RETENTION + 1)
    observer.error = "process_observation_unsupported"
    result = manager.reclaim()
    assert result["results"] == []
    assert result["retained_by_reason"] == {"settlement_undecidable": 2}
    assert result["observation_error"] == "process_observation_unsupported"
    assert Path(settled["path"]).is_dir() and Path(failed["path"]).is_dir()


def test_failed_or_unfinalized_scratch_waits_for_the_retention_window(setup):
    manager, _, facts = setup
    facts["outcome"] = "failed"
    failed = retained(manager)
    facts["outcome"] = "passed"
    unfinalized = retained(manager, finalized=False)
    for allocation in (failed, unfinalized):
        age(manager, allocation, GRACE + 1)
    result = manager.reclaim()
    assert result["results"] == []
    assert result["retained_by_reason"] == {lifecycle.UNPROVEN: 2}

    for allocation in (failed, unfinalized):
        age(manager, allocation, RETENTION + 1)
    receipts = manager.reclaim()["results"]
    assert sorted(r["reason"] for r in receipts) == [lifecycle.EXPIRED] * 2
    assert not Path(failed["path"]).exists() and not Path(unfinalized["path"]).exists()


def test_prune_is_a_dry_run_by_default_and_applies_on_request(setup):
    manager, _, _ = setup
    allocation = retained(manager, files=[("tmp/a", 2048)])
    young = retained(manager)
    age(manager, allocation, GRACE + 1)

    preview = lifecycle.prune(manager)
    assert preview["apply"] is False and preview["results"] == []
    [candidate] = preview["candidates"]
    assert candidate["allocation_id"] == allocation["allocation_id"]
    assert candidate["rule"] == lifecycle.SETTLED_LATER
    assert preview["measured_candidates"] == 1 and preview["candidate_logical_bytes"] >= 2048
    assert preview["retained_by_reason"] == {"settlement_grace_period": 1}
    assert Path(allocation["path"]).is_dir()
    assert "Dry run" in lifecycle.describe_prune(preview)[-1]

    applied = lifecycle.prune(manager, apply=True, seconds=10)
    assert applied["reclaimed"] == 1 and applied["complete"] is True
    assert applied["reclaimed_logical_bytes"] >= 2048
    assert not Path(allocation["path"]).exists() and Path(young["path"]).is_dir()


def test_allocate_reclaims_settled_scratch_opportunistically(setup):
    manager, observer, _ = setup
    allocation = retained(manager)
    age(manager, allocation, GRACE + 1)
    manager.allocate(
        owner={"adapter_kind": "evidence", "identity": "next", "reservation": "r2"},
        allocation_class="verification-fixtures",
        declaration_digest="b" * 64,
    )
    assert manager.last_expiry["results"][0]["reason"] == lifecycle.SETTLED_LATER
    assert not Path(allocation["path"]).exists()


def test_a_consumer_registered_after_the_scan_blocks_reclaim(setup):
    manager, observer, _ = setup
    allocation = retained(manager)
    age(manager, allocation, GRACE + 1)

    def late_consumer(directory, *, deadline):
        manager.register_consumer(
            allocation["allocation_id"], consumer_id="c" * 32, owner_reference={"x": 1}
        )
        return quiet(directory, deadline=deadline)

    manager.observe = late_consumer
    result = manager.reclaim()
    assert result["results"] == []
    assert result["retained_by_reason"] == {"changed_during_reclaim": 1}
    assert Path(allocation["path"]).is_dir()


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="process observation is implemented for macOS and Linux",
)
def test_real_observation_sees_a_process_working_inside_the_allocation(setup):
    manager, _, _ = setup
    manager.observe = lifecycle.observe_processes
    allocation = retained(manager, pid=2**22 + 17)
    age(manager, allocation, GRACE + 1)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=allocation["path"]
    )
    try:
        deadline = time.monotonic() + 3
        while not lifecycle.observe_processes(
            manager.root / "allocations", deadline=time.monotonic() + 3
        )["users"].get(allocation["allocation_id"]):
            assert time.monotonic() < deadline, "child cwd not observed"
            time.sleep(0.05)
        result = manager.reclaim()
        assert result["retained_by_reason"] == {"allocation_in_use": 1}
        assert Path(allocation["path"]).is_dir()
    finally:
        child.kill()
        child.wait(timeout=5)
    assert [r["state"] for r in manager.reclaim()["results"]] == ["REMOVED"]


def test_retained_summary_and_doctor_report_retained_bytes(setup, tmp_path, monkeypatch):
    manager, _, _ = setup
    retained(manager, files=[("tmp/a", 4096)])
    summary = retained_summary(manager)
    assert summary["retained"] == 1 and summary["measured_retained"] == 1
    assert summary["retained_by_reason"] == {lifecycle.UNPROVEN: 1}
    assert summary["retained_logical_bytes"] >= 4096
    assert summary["estimated_retained_bytes"] == summary["retained_allocated_bytes_estimate"]
    assert summary["complete"] is True

    monkeypatch.setenv("HARNESS_HOME", str(manager.root.parent))
    report = cli.storage_report()
    assert report["retained"] == 1 and report["retained_logical_bytes"] >= 4096
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "absent"))
    assert cli.storage_report()["namespace"] is False


def test_alctl_storage_prune_defaults_to_a_dry_run(setup, capsys):
    manager, _, _ = setup
    allocation = retained(manager)
    age(manager, allocation, GRACE + 1)
    assert cli.main(["storage", "prune", "--root", str(manager.root), "--seconds", "5"]) == 0
    output = capsys.readouterr().out
    assert "Dry run" in output
    assert Path(allocation["path"]).is_dir()


def test_failed_scratch_expires_after_the_failed_retention_not_the_full_window(setup):
    manager, _, facts = setup
    failed_retention = lifecycle.DEFAULT_POLICY["failed_retention_seconds"]
    assert failed_retention < RETENTION
    facts["outcome"] = "failed"
    failed = retained(manager)
    facts["outcome"] = "passed"
    unfinalized = retained(manager, finalized=False)
    allocated = manager.allocate(
        owner={"adapter_kind": "evidence", "identity": "run", "reservation": "r2"},
        allocation_class="verification-fixtures",
        declaration_digest="b" * 64,
    )
    for allocation in (failed, unfinalized, allocated):
        age(manager, allocation, failed_retention + 1)
    receipts = manager.reclaim()["results"]
    # Finished-but-not-passed scratch goes after a day; an allocation whose owner never
    # finished keeps the full window.
    assert sorted(r["allocation_id"] for r in receipts) == sorted(
        [failed["allocation_id"], unfinalized["allocation_id"]])
    assert not Path(failed["path"]).exists() and not Path(unfinalized["path"]).exists()
    assert Path(allocated["path"]).exists()


def test_disk_pressure_reclaims_anything_expirable_past_the_pressure_window(setup, monkeypatch):
    manager, observer, _ = setup
    pressure = lifecycle.DEFAULT_POLICY["pressure_retention_seconds"]
    young = retained(manager, finalized=False)
    old = retained(manager, finalized=False)
    age(manager, young, pressure - 60)
    age(manager, old, pressure + 60)
    assert manager.reclaim()["results"] == [] and manager.reclaim()["pressure"] is False
    monkeypatch.setitem(manager.policy, "pressure_free_bytes", 2**62)  # any real disk is "full"
    result = manager.reclaim()
    assert result["pressure"] is True
    assert [r["allocation_id"] for r in result["results"]] == [old["allocation_id"]]
    assert result["results"][0]["reason"] == lifecycle.EXPIRED
    assert Path(young["path"]).exists() and not Path(old["path"]).exists()
    # Pressure never overrides a live owner or a process working inside the allocation.
    busy = retained(manager, finalized=False)
    age(manager, busy, pressure + 60)
    observer.value = dict(observer.value, users={busy["allocation_id"]: [4242]})
    result = manager.reclaim()
    assert not result["results"] and Path(busy["path"]).exists()
    assert result["retained_by_reason"].get("allocation_in_use") == 1


def test_pressure_policy_is_configurable_and_validated(monkeypatch):
    monkeypatch.setenv("HARNESS_STORAGE_FAILED_RETENTION_SECONDS", "600")
    monkeypatch.setenv("HARNESS_STORAGE_PRESSURE_FREE_BYTES", "1024")
    monkeypatch.setenv("HARNESS_STORAGE_PRESSURE_RETENTION_SECONDS", "60")
    policy = lifecycle.configured_policy()
    assert (policy["failed_retention_seconds"], policy["pressure_free_bytes"],
            policy["pressure_retention_seconds"]) == (600, 1024, 60)
    monkeypatch.setenv("HARNESS_STORAGE_PRESSURE_FREE_BYTES", "-1")
    with pytest.raises(StorageError, match="invalid_storage_policy_configuration"):
        lifecycle.configured_policy()
