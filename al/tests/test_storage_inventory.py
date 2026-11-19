"""Inventory is metadata-only, bounded, and never adopts old directories."""

import os

from al.installer.storage_filesystem import _open_directory
from al.installer.storage_inventory import inspect, measure_tree, pressure
from al.installer.storage_lifecycle import StorageManager


def test_inventory_deduplicates_hardlinks_without_reading_bodies(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    first = root / "first"
    first.write_bytes(b"x" * 16384)
    os.link(first, root / "second")
    descriptor = _open_directory(root)
    try:
        result = measure_tree(descriptor)
    finally:
        os.close(descriptor)
    assert result["complete"] is True
    assert result["entries"] == 2
    assert result["logical_bytes"] == root.stat().st_size + first.stat().st_size


def test_entry_limit_reports_partial_observation(tmp_path):
    for index in range(5):
        (tmp_path / str(index)).write_text("fixture")
    descriptor = _open_directory(tmp_path)
    try:
        result = measure_tree(descriptor, limits={"entries": 1})
    finally:
        os.close(descriptor)
    assert result["complete"] is False
    assert result["entries"] == 1


def test_inventory_never_traverses_symlink_targets(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret-sentinel").write_text("never inspected")
    (root / "alias").symlink_to(outside, target_is_directory=True)
    descriptor = _open_directory(root)
    try:
        result = measure_tree(descriptor)
    finally:
        os.close(descriptor)
    assert result["complete"] is True
    assert result["entries"] == 1


def test_absent_namespace_inspection_does_not_create_registry(tmp_path):
    manager = StorageManager(tmp_path / "manager", create=False)
    result = inspect(manager)
    assert result["allocations"] == []
    assert not manager.root.exists()


def test_legacy_data_remains_visible_and_undeletable(tmp_path):
    legacy = tmp_path / "old-evidence"
    legacy.mkdir()
    sentinel = legacy / "retained"
    sentinel.write_text("existing history")
    manager = StorageManager(tmp_path / "manager", create=False)
    result = inspect(manager, include_legacy=True)
    row = next(row for row in result["legacy"] if row["name"] == legacy.name)
    assert row["state"] == "UNREGISTERED" and row["deletable"] is False
    assert row["measurement"]["logical_bytes"] > 0
    assert sentinel.read_text() == "existing history"
    assert not manager.root.exists()


def test_pressure_retains_unsettled_allocations(tmp_path):
    manager = StorageManager(tmp_path / "manager", policy={"minimum_free_bytes": 0})
    allocation = manager.allocate(
        owner={"adapter_kind": "evidence", "identity": "run-E1", "reservation": "r1"},
        allocation_class="operation-scratch",
        declaration_digest="b" * 64,
    )
    result = pressure(manager, requested_class="operation-scratch")
    assert result["reserve_met"] is True
    assert result["retained_by_reason"]["owner_unsettled"] == 1
    assert manager.path(allocation["allocation_id"]).is_dir()


def test_record_limit_does_not_claim_complete_inventory(tmp_path):
    manager = StorageManager(tmp_path / "manager", policy={"minimum_free_bytes": 0})
    for index in range(3):
        manager.allocate(
            owner={
                "adapter_kind": "evidence",
                "identity": f"run-E{index}",
                "reservation": str(index),
            },
            allocation_class="operation-scratch",
            declaration_digest="c" * 64,
        )
    result = inspect(manager, limits={"records": 1})
    assert result["complete"] is False
    assert len(result["allocations"]) == 1


def test_configurable_soft_pressure_is_observed_without_claiming_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STORAGE_WARNING_BYTES", "1")
    monkeypatch.setenv("HARNESS_STORAGE_MINIMUM_FREE_BYTES", "0")
    manager = StorageManager(tmp_path / "manager")
    allocation = manager.allocate(
        owner={"adapter_kind": "evidence", "identity": "run", "reservation": "entry"},
        allocation_class="operation-scratch",
        declaration_digest="a" * 64,
    )
    (manager.path(allocation["allocation_id"]) / "retained-fixture").write_bytes(b"x" * 16384)
    observed = inspect(manager)
    assert observed["pressure"]["warning"] is True
    assert observed["pressure"]["reserve_met"] is True
    assert observed["pressure"]["warning_bytes"] == 1
    assert manager.path(allocation["allocation_id"]).exists()


def test_invalid_policy_configuration_refuses_without_creating_namespace(tmp_path, monkeypatch):
    import pytest

    from al.installer.storage_filesystem import StorageError

    monkeypatch.setenv("HARNESS_STORAGE_WARNING_BYTES", "not-a-number")
    with pytest.raises(StorageError, match="invalid_storage_policy_configuration"):
        StorageManager(tmp_path / "manager")
    assert not (tmp_path / "manager").exists()
