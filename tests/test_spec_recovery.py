# ruff: noqa: F811
"""Missing-only seal restoration uses original bytes and cannot create approval."""

import sys
from pathlib import Path

import pytest

from harness.approval import ApprovalGate, NotApproved
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest
from harness.execution_handoff import read_handoff
from harness.execution_models import ExecutionConflict
from harness.execution_spec_recovery import preview_spec_restoration, restore_specification
from harness.execution_store import get_execution
from harness.spec_store import SpecStore
from tests.execution_helpers import approved_execution
from tests.test_template_classification_integration import operations, registered  # noqa: F401


@pytest.fixture
def approved(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    envelope = read_handoff(execution["handoff_path"])
    target = Path(envelope["approval"]["path"])
    return execution, envelope, target, target.read_bytes()


def preview(controller, execution):
    return preview_spec_restoration(
        controller,
        execution["id"],
        expected_epoch_id=execution["epoch_id"],
        expected_authority_generation=execution["authority_generation"],
    )


def restore(controller, execution, observed, request_id="restore-one"):
    return restore_specification(
        controller,
        execution["id"],
        request_id=request_id,
        preview_sha256=observed["preview_sha256"],
        expected_epoch_id=execution["epoch_id"],
        expected_authority_generation=execution["authority_generation"],
    )


def immutable_state(controller, execution):
    result = {}
    for table in (
        "approvals",
        "spec_versions",
        "executions",
        "execution_epochs",
        "execution_attempts",
    ):
        result[table] = [tuple(row) for row in controller.conn.execute("SELECT * FROM " + table)]
    return result


def test_exact_missing_restore_revalidates_authority_and_retry_is_noop(controller, approved):
    execution, envelope, target, raw = approved
    before = immutable_state(controller, execution)
    target.unlink()
    with pytest.raises(NotApproved):
        require_execution_authority(controller, execution["id"], operation="read")
    observed = preview(controller, execution)
    assert observed == preview(controller, execution)
    assert observed["preview"]["state_condition"] == "missing"
    assert not target.exists()
    result = restore(controller, execution, observed)
    assert result["restored"] and target.read_bytes() == raw
    assert result["authority_revalidation"]["status"] == "current"
    assert result["implementation_authority_granted"] is False
    assert require_execution_authority(controller, execution["id"], operation="read") == envelope
    retried = restore(controller, execution, observed)
    assert retried["restored"] is False and retried["state"] == "already-present"
    assert retried["receipt"] == result["receipt"]
    events = [
        e
        for e in controller.workspace(execution["task_id"]).events()
        if e.kind == "execution.specification.restored"
    ]
    assert len(events) == 1
    assert immutable_state(controller, execution) == before
    assert not (Path(envelope["repository"]) / ".claude/al-runtime").exists()


def test_already_present_and_missing_original_task_directory(controller, approved):
    execution, _, target, raw = approved
    observed = preview(controller, execution)
    assert observed["preview"]["state_condition"] == "intact"
    assert restore(controller, execution, observed)["restored"] is False
    for path in target.parent.iterdir():
        path.unlink()
    target.parent.rmdir()
    observed = preview(controller, execution)
    assert observed["preview"]["state_condition"] == "missing"
    assert restore(controller, execution, observed, "restore-two")["restored"]
    assert target.read_bytes() == raw


@pytest.mark.parametrize(
    "condition", ["modified", "symlink", "hardlink", "unreadable", "directory"]
)
def test_existing_edited_or_unsafe_targets_are_never_overwritten(
    controller, approved, tmp_path, condition
):
    execution, _, target, raw = approved
    target.unlink()
    other = tmp_path / "other.md"
    other.write_bytes(raw)
    if condition == "modified":
        target.write_bytes(b"Independent source edits must remain intact.\n")
    elif condition == "symlink":
        target.symlink_to(other)
    elif condition == "hardlink":
        target.hardlink_to(other)
    elif condition == "unreadable":
        target.write_bytes(raw)
        target.chmod(0)
    else:
        target.mkdir()
    observed = preview(controller, execution)
    assert observed["preview"]["state_condition"] != "missing"
    assert observed["preview"]["readiness"] == "unavailable"
    with pytest.raises(ExecutionConflict):
        restore(controller, execution, observed)
    assert other.read_bytes() == raw
    if condition == "modified":
        assert target.read_bytes() == b"Independent source edits must remain intact.\n"
    elif condition == "symlink":
        assert target.is_symlink()
    elif condition == "hardlink":
        assert target.stat().st_nlink == 2
    elif condition == "unreadable":
        assert target.stat().st_mode & 0o777 == 0
        target.chmod(0o600)
    else:
        assert target.is_dir()


def test_parent_alias_and_stale_preview_refuse_without_changes(controller, approved):
    execution, _, target, raw = approved
    target.unlink()
    observed = preview(controller, execution)
    target.write_bytes(b"concurrent edits")
    with pytest.raises(ExecutionConflict, match="stale"):
        restore(controller, execution, observed)
    assert target.read_bytes() == b"concurrent edits"
    original = target.parent
    retained = original.with_name(original.name + "-retained")
    original.rename(retained)
    original.symlink_to(retained, target_is_directory=True)
    observed = preview(controller, execution)
    assert observed["preview"]["state_condition"] == "unsafe"
    with pytest.raises(ExecutionConflict):
        restore(controller, execution, observed)
    assert (retained / target.name).read_bytes() == b"concurrent edits"
    assert raw != b"concurrent edits"


@pytest.mark.parametrize(
    "forgery", ["raw-hash", "body", "task", "path", "root", "database", "handoff-hash"]
)
def test_retained_handoff_forgery_cannot_restore_seal(controller, approved, tmp_path, forgery):
    execution, envelope, target, _ = approved
    target.unlink()
    if forgery == "raw-hash":
        envelope["sealed_hash"] = "sha256:" + "0" * 64
    elif forgery in {"body", "task"}:
        if forgery == "body":
            envelope["sealed_bytes"] += "\nUnapproved supplemental body.\n"
        else:
            envelope["sealed_bytes"] = envelope["sealed_bytes"].replace(
                "- **Task:** " + execution["task_id"], "- **Task:** foreign-task"
            )
        envelope["sealed_hash"] = digest(envelope["sealed_bytes"].encode())
    elif forgery == "path":
        envelope["approval"]["path"] = str(tmp_path / "arbitrary.md")
    elif forgery == "root":
        envelope["specs"] = str(tmp_path / "alternate-root")
    elif forgery == "database":
        foreign = tmp_path / "foreign.db"
        foreign.write_bytes(b"not the approved database")
        envelope["database"] = str(foreign)
    else:
        envelope["task_id"] = "foreign-task"
    modified = canonical(envelope).encode()
    Path(execution["handoff_path"]).write_bytes(modified)
    if forgery != "handoff-hash":
        controller.conn.execute(
            "UPDATE executions SET handoff_hash=? WHERE id=?", (digest(modified), execution["id"])
        )
    with pytest.raises(ValueError):
        preview(controller, execution)
    assert not target.exists()
    assert not (tmp_path / "arbitrary.md").exists()


@pytest.mark.parametrize("change", ["new-approval", "body", "review", "phase", "epoch"])
def test_current_approval_phase_body_review_and_epoch_remain_required(controller, approved, change):
    execution, envelope, target, _ = approved
    observed = preview(controller, execution)
    if change == "new-approval":
        ApprovalGate(
            controller, execution["task_id"], SpecStore(target.parent), Path(envelope["repository"])
        ).approve()
    elif change == "body":
        controller.workspace(execution["task_id"]).checkpoint(
            "investigation",
            {
                "decisions": [
                    {"id": "DEC-0099", "version": 1, "title": "New scope", "status": "approved"}
                ]
            },
        )
    elif change == "review":
        controller.workspace(execution["task_id"]).propose_change(
            "DEC-0001", 1, "T", "fixture", "fixture"
        )
    elif change == "phase":
        controller.workspace(execution["task_id"]).record_event(
            "phase.changed", {"phase": "interview"}
        )
    target.unlink()
    if change == "epoch":
        with pytest.raises(ExecutionConflict, match="stale_epoch"):
            restore_specification(
                controller,
                execution["id"],
                request_id="stale-epoch",
                preview_sha256=observed["preview_sha256"],
                expected_epoch_id="foreign-epoch",
                expected_authority_generation=0,
            )
    else:
        with pytest.raises(ValueError):
            restore(controller, execution, observed)
    assert not target.exists()


def test_missing_decision_provenance_is_reported_and_never_reconstructed(
    controller, approved, tmp_path
):
    execution, _, target, raw = approved
    absent = tmp_path / "missing-decision.md"
    controller.conn.execute(
        "INSERT INTO execution_decisions "
        "(id,execution_id,blocker_id,blocker_revision,request_id,request,state,registry_path,"
        "registry_hash,created_at,updated_at) VALUES ('old-decision',?,'blocker',1,'old-request',"
        "'{}','Resolved',?,?,'now','now')",
        (execution["id"], str(absent), "sha256:" + "a" * 64),
    )
    target.unlink()
    observed = preview(controller, execution)
    assert observed["preview"]["provenance_obligations"]
    result = restore(controller, execution, observed)
    assert result["restored"] and target.read_bytes() == raw
    assert result["authority_revalidation"]["status"] == "unavailable"
    assert not absent.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="genuine protected macOS initialization")
def test_missing_planning_preview_refreshes_source_observation_without_collision(registered):
    from harness.execution_planning_recovery import preview_planning

    _, _, controller, initialization, _, execution, envelope = registered[0]
    Path(initialization["source"]).unlink()
    kwargs = {
        "expected_epoch_id": execution["epoch_id"],
        "expected_authority_generation": execution["authority_generation"],
    }
    first = preview_planning(controller, execution["id"], **kwargs)
    assert (
        preview_planning(controller, execution["id"], **kwargs)["preview_sha256"]
        == first["preview_sha256"]
    )
    source = Path(envelope["repository"]) / "new-observation.txt"
    source.write_text("Independent current source observation.\n")
    refreshed = preview_planning(controller, execution["id"], **kwargs)
    assert refreshed["preview_sha256"] != first["preview_sha256"]
    assert (
        preview_planning(controller, execution["id"], **kwargs)["preview_sha256"]
        == refreshed["preview_sha256"]
    )


@pytest.mark.parametrize("edge", ["publication", "audit"])
def test_interrupted_restoration_retries_without_overwrite_or_duplicate_audit(
    controller, approved, monkeypatch, edge
):
    import harness.execution_spec_recovery as recovery

    execution, _, target, raw = approved
    target.unlink()
    observed = preview(controller, execution)
    name = "_publish" if edge == "publication" else "_audit"
    original = getattr(recovery, name)

    def interrupted(*args, **kwargs):
        if edge == "publication":
            original(*args, **kwargs)
        raise OSError("synthetic acknowledgment lost after " + edge)

    monkeypatch.setattr(recovery, name, interrupted)
    with pytest.raises(OSError, match="acknowledgment lost"):
        restore(controller, execution, observed)
    assert target.read_bytes() == raw
    monkeypatch.setattr(recovery, name, original)
    result = restore(controller, execution, observed)
    assert not result["restored"] and result["authority_revalidation"]["status"] == "current"
    assert (
        len(
            [
                event
                for event in controller.workspace(execution["task_id"]).events()
                if event.kind == "execution.specification.restored"
            ]
        )
        == 1
    )


def test_concurrent_existing_file_cannot_be_overwritten(controller, approved, monkeypatch):
    import harness.execution_spec_recovery as recovery

    execution, _, target, _ = approved
    target.unlink()
    observed = preview(controller, execution)
    original = recovery._publish

    def competing(target, root, raw):
        target.write_bytes(b"concurrent independent source work")
        return original(target, root, raw)

    monkeypatch.setattr(recovery, "_publish", competing)
    with pytest.raises(ExecutionConflict, match="changed during"):
        restore(controller, execution, observed)
    assert target.read_bytes() == b"concurrent independent source work"
