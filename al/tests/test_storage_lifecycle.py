"""Destructive lifecycle checks use only manager-created disposable fixtures."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from al.installer import storage_lifecycle as lifecycle
from al.installer.storage_filesystem import StorageError, identity
from al.tests.runtime_run_helpers import launched  # noqa: F401


class InterruptedCleanup(BaseException):
    pass


@pytest.fixture
def storage(tmp_path):
    durable = tmp_path / "durable"
    durable.mkdir(mode=0o700)
    report = durable / "report.json"
    report.write_text('{"result":"passed"}')
    facts = {
        "outcome": "passed",
        "settled": True,
        "accounting_complete": True,
        "source_current": True,
        "classification_current": True,
    }

    def owner(_record, reference):
        assert reference == {"completion": "actual-owned-handle"}
        return dict(facts)

    def publication(record, reference):
        assert reference == {"publication": "immutable-result"}
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

    manager = lifecycle.StorageManager(
        tmp_path / "manager",
        policy={"minimum_free_bytes": 0},
        verify_owner=owner,
        verify_publication=publication,
    )
    allocation = manager.allocate(
        owner={"adapter_kind": "evidence", "identity": "run-E1", "reservation": "reserved-current"},
        allocation_class="verification-fixtures",
        declaration_digest="a" * 64,
    )
    manager.mark_running(
        allocation["allocation_id"],
        generation=allocation["generation"],
        owner_receipt={"handle": "actual-owned-handle"},
    )
    return manager, allocation, facts, report


def finalize(manager, allocation, **kwargs):
    return manager.finalize(
        allocation["allocation_id"],
        publication_reference={"publication": "immutable-result"},
        completion_reference={"completion": "actual-owned-handle"},
        **kwargs,
    )


def record(manager, allocation):
    with manager._locked() as directories:
        return manager._record(directories, allocation["allocation_id"])


def test_success_removes_only_owned_scratch_and_keeps_published_evidence(storage, tmp_path):
    manager, allocation, _, report = storage
    scratch = Path(allocation["path"])
    (scratch / "nested").mkdir()
    (scratch / "nested" / "fixture").write_bytes(b"x" * 65536)
    outside = tmp_path / "outside"
    outside.write_text("protected")
    (scratch / "link").symlink_to(outside)
    before = report.read_bytes()
    result = finalize(manager, allocation)
    assert result["state"] == "REMOVED"
    assert not scratch.exists()
    assert outside.read_text() == "protected"
    assert report.read_bytes() == before
    assert record(manager, allocation)["state"] == "REMOVED"
    assert finalize(manager, allocation) == result


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "failed"},
        {"outcome": "skipped"},
        {"outcome": "unknown"},
        {"outcome": "cancelled"},
        {"settled": False},
        {"accounting_complete": False},
        {"source_current": False},
        {"classification_current": False},
    ],
)
def test_unsuccessful_or_uncertain_owner_retains_scratch(storage, change):
    manager, allocation, facts, _ = storage
    facts.update(change)
    result = finalize(manager, allocation)
    assert result["state"] == "RETAINED"
    assert Path(allocation["path"]).is_dir()
    assert record(manager, allocation)["state"] == "RETAINED"


def test_failed_outcome_cannot_be_replaced_by_later_pass(storage):
    manager, allocation, facts, _ = storage
    facts["outcome"] = "failed"
    assert finalize(manager, allocation)["state"] == "RETAINED"
    facts["outcome"] = "passed"
    assert finalize(manager, allocation)["reason"] == "unsuccessful_outcome"
    assert Path(allocation["path"]).is_dir()


def test_missing_trusted_validators_cannot_authorize_deletion(storage):
    manager, allocation, _, _ = storage
    manager.verify_owner = None
    with pytest.raises(StorageError, match="trusted_validator_unavailable"):
        finalize(manager, allocation)
    assert Path(allocation["path"]).is_dir()


def test_replaced_allocation_is_not_deleted(storage):
    manager, allocation, _, _ = storage
    scratch = Path(allocation["path"])
    original = scratch.with_name("retained-original")
    scratch.rename(original)
    scratch.mkdir()
    (scratch / "sentinel").write_text("replacement")
    with pytest.raises(StorageError, match="allocation_replaced"):
        finalize(manager, allocation)
    assert (scratch / "sentinel").read_text() == "replacement"
    assert original.is_dir()


def test_registry_directory_replacement_refuses(storage):
    manager, _, _, _ = storage
    records = manager.root / "records"
    records.rename(manager.root / "old-records")
    records.mkdir(mode=0o700)
    with pytest.raises(StorageError, match="namespace_replaced"):
        manager.preview()


def test_namespace_root_replacement_refuses(storage):
    manager, _, _, _ = storage
    manager.root.rename(manager.root.with_name("retained-manager"))
    manager.root.mkdir(mode=0o700)
    with pytest.raises(StorageError, match="namespace_replaced"):
        manager.preview()


def test_symlinked_namespace_ancestor_is_not_followed(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        lifecycle.StorageManager(alias / "manager")
    assert list(target.iterdir()) == []


def test_partial_cleanup_resumes_recorded_quarantine(storage):
    manager, allocation, _, _ = storage
    for index in range(8):
        (Path(allocation["path"]) / str(index)).write_text("fixture")
    result = finalize(manager, allocation, limits={"entries": 2})
    assert result["state"] == "PARTIAL"
    assert not Path(allocation["path"]).exists()
    for _ in range(5):
        result = manager.resume_cleanup(
            allocation["allocation_id"],
            transaction_id=result["transaction_id"],
            limits={"entries": 2},
        )
        if result["state"] == "REMOVED":
            break
    assert result["state"] == "REMOVED"


def test_resume_does_not_touch_replacement_at_original_path(storage, monkeypatch):
    manager, allocation, _, _ = storage
    scratch = Path(allocation["path"])
    (scratch / "old").write_text("owned")
    original_delete = lifecycle._delete_contents

    def crash(*_args):
        raise InterruptedCleanup

    monkeypatch.setattr(lifecycle, "_delete_contents", crash)
    with pytest.raises(InterruptedCleanup):
        finalize(manager, allocation)
    value = record(manager, allocation)
    assert value["state"] == "QUARANTINED"
    scratch.mkdir()
    (scratch / "replacement").write_text("leave unchanged")
    monkeypatch.setattr(lifecycle, "_delete_contents", original_delete)
    assert (
        manager.resume_cleanup(
            allocation["allocation_id"], transaction_id=value["transaction"]["id"]
        )["state"]
        == "REMOVED"
    )
    assert (scratch / "replacement").read_text() == "leave unchanged"


def test_missing_quarantine_before_removal_intent_is_not_success(storage, monkeypatch):
    manager, allocation, _, _ = storage
    original_delete = lifecycle._delete_contents
    monkeypatch.setattr(
        lifecycle, "_delete_contents", lambda *_args: (_ for _ in ()).throw(InterruptedCleanup())
    )
    with pytest.raises(InterruptedCleanup):
        finalize(manager, allocation)
    value = record(manager, allocation)
    quarantine = manager.root / "quarantine" / value["transaction"]["id"]
    quarantine.rename(manager.root / "retained-quarantine")
    monkeypatch.setattr(lifecycle, "_delete_contents", original_delete)
    with pytest.raises(StorageError, match="quarantine_missing"):
        manager.resume_cleanup(
            allocation["allocation_id"], transaction_id=value["transaction"]["id"]
        )


def test_crash_after_root_removal_reconciles_only_durable_intent(storage, monkeypatch):
    manager, allocation, _, _ = storage
    original = manager._removed
    monkeypatch.setattr(
        manager, "_removed", lambda *_args: (_ for _ in ()).throw(InterruptedCleanup())
    )
    with pytest.raises(InterruptedCleanup):
        finalize(manager, allocation)
    value = record(manager, allocation)
    assert value["transaction"]["phase"] == "REMOVE_ROOT_READY"
    monkeypatch.setattr(manager, "_removed", original)
    result = manager.resume_cleanup(
        allocation["allocation_id"], transaction_id=value["transaction"]["id"]
    )
    assert result["state"] == "REMOVED" and result["recovered_bytes"] is None


def test_publication_mutation_prevents_resumed_cleanup(storage):
    manager, allocation, _, report = storage
    for index in range(3):
        (Path(allocation["path"]) / str(index)).write_text("fixture")
    result = finalize(manager, allocation, limits={"entries": 1})
    report.write_text('{"result":"failed"}')
    with pytest.raises(StorageError, match="publication_changed"):
        manager.resume_cleanup(allocation["allocation_id"], transaction_id=result["transaction_id"])


def test_stale_or_malformed_plan_never_authorizes_cleanup(storage):
    manager, allocation, _, _ = storage
    plan = manager.preview()
    manager.retain(allocation["allocation_id"], reason="publication_pending")
    assert manager.apply(plan)["results"][0]["reason"] == "stale_plan"
    malformed = manager.preview()
    malformed["schema_version"] = True
    with pytest.raises(StorageError, match="invalid_cleanup_plan"):
        manager.apply(malformed)
    assert Path(allocation["path"]).is_dir()


def test_actual_owned_child_cleanup_requires_its_settlement(storage, tmp_path):
    from al.installer.owned_process import OwnedProcess

    manager, allocation, facts, _ = storage
    journal = tmp_path / "child-owner"
    journal.mkdir(mode=0o700)
    owner = OwnedProcess(journal, "storage-core-child")
    with (journal / "output.log").open("wb") as output:
        owner.spawn(
            [sys.executable, "-I", "-c", "print('owned child')"],
            cwd=tmp_path,
            env={"PATH": os.defpath},
            output=output,
        )
        owner.process.wait(timeout=5)
        cleanup = owner.cleanup()
    from al.installer.verification_contracts import producer_settled

    facts["settled"] = producer_settled(cleanup)
    assert cleanup["complete"] is True and facts["settled"] is False
    manager.retain(allocation["allocation_id"], reason="descendant_settlement_unproven")
    result = finalize(manager, allocation)
    assert result["state"] == "RETAINED"
    assert result["reason"] == "descendant_settlement_unproven"


def test_unknown_registry_record_is_not_adopted(storage):
    manager, allocation, _, _ = storage
    path = manager.root / "records" / (allocation["allocation_id"] + ".json")
    value = json.loads(path.read_bytes())
    value["state"] = "APPROVED_BY_CALLER"
    path.write_text(json.dumps(value))
    with pytest.raises(StorageError, match="invalid_allocation_record"):
        manager.preview()


def test_partial_cleanup_receipts_are_immutable_across_retries(storage):
    manager, allocation, _, _ = storage
    for index in range(4):
        (Path(allocation["path"]) / str(index)).write_text("owned")
    first = finalize(manager, allocation, limits={"entries": 1})
    first_path = manager.root / "receipts" / (first["receipt_id"] + ".json")
    before = first_path.read_bytes()
    second = manager.resume_cleanup(
        allocation["allocation_id"], transaction_id=first["transaction_id"], limits={"entries": 1}
    )
    assert second["receipt_id"] != first["receipt_id"]
    assert first_path.read_bytes() == before
    assert json.loads(first_path.read_bytes()) == first


@pytest.mark.parametrize(
    "field,value",
    [
        ("identity", {"device": 0}),
        (
            "owner",
            {"adapter_kind": "harness", "identity": "x", "reservation": "x", "argv": "forbidden"},
        ),
        ("consumers", [{"consumer_id": "a" * 32, "owner_reference": {}, "unknown": True}]),
        ("transaction", {"id": "a" * 32, "phase": "REMOVE_ROOT_READY", "identity": {}}),
        ("state", []),
    ],
)
def test_malformed_registry_metadata_never_authorizes_cleanup(storage, field, value):
    manager, allocation, _, _ = storage
    path = manager.root / "records" / (allocation["allocation_id"] + ".json")
    metadata = json.loads(path.read_bytes())
    metadata[field] = value
    path.write_text(json.dumps(metadata))
    with pytest.raises(StorageError, match="invalid_allocation_record"):
        manager.preview()
    assert Path(allocation["path"]).is_dir()


def test_existing_namespace_missing_coordination_lock_is_not_recreated(storage):
    manager, allocation, _, _ = storage
    (manager.root / "lock").unlink()
    with pytest.raises(FileNotFoundError):
        manager.preview()
    assert not (manager.root / "lock").exists()
    assert Path(allocation["path"]).is_dir()


def test_default_declaration_is_explicit_without_rewriting_operands():
    from al.installer import storage_declarations as grammar

    declaration = grammar.default_declaration()
    assert declaration["scratch"] == [{"name": "operation", "class": "operation-scratch"}]
    assert declaration["operands"] == declaration["outputs"] == []
    argv = ["env", "TMPDIR=/explicit/location", "pytest", "--basetemp=/explicit/pytest"]
    assert grammar.render_argv(argv, declaration, "/private/tmp/owned") == argv
    assert grammar.normalize(None) is None


@pytest.mark.parametrize("executable,expected", [("absent", 127), ("not-executable", 126)])
def test_owned_bootstrap_preserves_exec_infrastructure_status(tmp_path, executable, expected):
    from al.installer.owned_process import OwnedProcess

    directory = tmp_path / "owned"
    directory.mkdir(mode=0o700)
    command = tmp_path / executable
    if expected == 126:
        command.write_text("not executable")
        command.chmod(0o600)
    owner = OwnedProcess(directory, "storage-exec-status")
    with (directory / "output.log").open("wb") as output:
        try:
            process = owner.spawn(
                [str(command)], cwd=tmp_path, env={"PATH": os.defpath}, output=output
            )
            assert process.wait(timeout=5) == expected
        finally:
            completion = owner.cleanup()
    assert owner.launch_identity is not None
    assert completion["complete"] and not completion["errors"] and not completion["unresolved"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a" * 256, True),
        ("a" * 257, True),
        ("a" * 4096, True),
        ("a" * 4097, False),
        ("\u20ac" * 1365, True),
        ("\u20ac" * 1366, False),
        ("", False),
        ("\0", False),
        ("prefix\0suffix", False),
        ("\ud800", False),
        (None, False),
        (b"identity", False),
    ],
    ids=[
        "ascii256",
        "ascii257",
        "ascii4096",
        "ascii4097",
        "utf8-within4096",
        "utf8-over4096",
        "empty",
        "nul",
        "embedded-nul",
        "unencodable",
        "none",
        "bytes",
    ],
)
def test_owner_identity_encoded_budget(value, expected):
    owner = {"adapter_kind": "evidence", "identity": value, "reservation": "reserved"}
    assert lifecycle._valid_owner(owner) is expected


@pytest.mark.parametrize("field", sorted(lifecycle.OWNER_FIELDS - {"identity", "adapter_kind"}))
def test_other_owner_field_limits_remain_256_characters(field):
    owner = {"adapter_kind": "evidence", "identity": "ordinary-id", "reservation": "reserved"}
    owner[field] = "\u20ac" * 256
    assert lifecycle._valid_owner(owner)
    owner[field] += "\u20ac"
    assert not lifecycle._valid_owner(owner)


def test_long_evidence_owner_roundtrips_and_keeps_exact_publication_binding(storage, tmp_path):
    manager, _, _, _ = storage
    evidence = tmp_path / ("evidence-" + "a" * 96) / ("b" * 96) / ("c" * 96)
    evidence.mkdir(mode=0o700, parents=True)
    owner = {"adapter_kind": "evidence", "identity": str(evidence), "reservation": "reserved"}
    assert len(owner["identity"].encode("utf-8")) > 256
    allocation = manager.allocate(
        owner=owner, allocation_class="verification-fixtures", declaration_digest="b" * 64
    )
    assert allocation["owner"] == owner
    assert record(manager, allocation)["owner"] == owner
    publication = manager.verify_publication

    def foreign_owner(value, reference):
        proof = publication(value, reference)
        proof["binding"]["owner"] = {**owner, "identity": owner["identity"] + "-foreign"}
        return proof

    manager.verify_publication = foreign_owner
    with pytest.raises(StorageError, match="publication_unverified"):
        finalize(manager, allocation)
    assert Path(allocation["path"]).is_dir()


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_long_evidence_launch_uses_public_managed_runtime(launched, platform, tmp_path):  # noqa: F811
    from al.tests.runtime_run_helpers import public_run

    source, home, repo, launcher, environment = launched
    nested = tmp_path / ("evidence-" + "a" * 80) / ("b" * 80)
    nested.mkdir(mode=0o700, parents=True)
    moved = nested / "repo"
    repo.rename(moved)
    repo = moved
    environment = dict(environment, HARNESS_HOME=str(home / "private-managed-storage"))
    launched = source, home, repo, launcher, environment
    (repo / "svc").mkdir()
    (repo / "svc/app.py").write_text("owned = True\n")
    started = public_run(
        launched, platform, "start", "--spec", "Verify long evidence identity paths."
    )
    assert started.returncode == 0, (started.stdout[-2000:], started.stderr[-2000:])
    marker = repo / "child-was-launched"
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path;" + f"Path({str(marker)!r}).write_text('owned');"
        "print('1 passed')",
    ]
    completed = public_run(
        launched,
        platform,
        "evidence",
        "run",
        "--area",
        "long-owner",
        "--scope",
        "svc",
        "--",
        *command,
    )
    assert completed.returncode == 0, (completed.stdout[-3000:], completed.stderr[-3000:])
    assert marker.read_text() == "owned"
    state = json.loads((repo / ("." + platform) / "al-runtime/pin-flow.json").read_bytes())
    entry = state["evidence"]["entries"][-1]
    assert entry["status"] == "PASS"
    allocation = entry["storage_allocation"]
    identity_path = allocation["owner"]["identity"]
    assert len(identity_path.encode("utf-8")) > 256
    expected = (
        repo
        / ("." + platform)
        / "al-runtime/pin-flow/logs/storage"
        / (entry["id"] + "-" + entry["reservation"])
    )
    assert identity_path == str(expected)
    registry = (
        home
        / "private-managed-storage/managed-storage/records"
        / (allocation["allocation_id"] + ".json")
    )
    assert json.loads(registry.read_bytes())["owner"] == allocation["owner"]
