"""Canonical first-record creation provenance fences historical native children."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from harness.execution_principals import PrincipalRegistry
from harness.native_runtime import native_auth_environment


def test_successor_root_resume_and_fresh_child_metadata_boundary(tmp_path, monkeypatch):
    native = tmp_path / "original-native-home"
    root, old, fresh, missing = [str(uuid.uuid4()) for _ in range(4)]
    records = native / "sessions/2026/10/29"
    records.mkdir(parents=True)
    cutoff = datetime(2026, 10, 29, 10, tzinfo=UTC).timestamp()

    def emit(tid, source, created):
        payload = {"id": tid, "session_id": tid, "cli_version": "0.164.0", "source": source}
        if created is not None:
            payload["timestamp"] = datetime.fromtimestamp(created, UTC).isoformat()
        # A newly emitted old record still has an old payload creation timestamp.
        (records / ("rollout-" + tid + ".jsonl")).write_text(
            json.dumps(
                {"timestamp": "2026-10-29T13:00:00Z", "type": "session_meta", "payload": payload}
            )
            + "\n"
            + "NEVER_READ_NATIVE_TRANSCRIPT\n"
        )

    emit(root, "exec", cutoff - 100)
    child_source = {
        "subagent": {"thread_spawn": {"parent_thread_id": root, "agent_role": "al-engineer"}}
    }
    emit(old, child_source, cutoff - 1)
    emit(fresh, child_source, cutoff + 1)
    emit(missing, child_source, None)
    fence = {
        "epoch_id": "epoch-fresh",
        "authority_generation": 1,
        "activated_at": cutoff,
        "predecessor_child_ids": [],
    }
    registry = PrincipalRegistry(
        tmp_path / "epoch-principals",
        native,
        {"approved": "fixture"},
        "epoch-policy",
        epoch_fence=fence,
    )
    registry.observe_root_start(root)
    assert registry.enroll(root)["role"] == "orchestrator"
    with pytest.raises(ValueError, match="stale_epoch"):
        registry.enroll(old)
    with pytest.raises(ValueError, match="creation provenance"):
        registry.enroll(missing)
    assert registry.enroll(fresh)["role"] == "al-engineer"
    # Predecessor IDs remain excluded even if a rewritten metadata timestamp claims freshness.
    emit(old, child_source, cutoff + 2)
    fence["predecessor_child_ids"] = [old]
    with pytest.raises(ValueError, match="stale_epoch"):
        registry.enroll(old)
    monkeypatch.setenv("CODEX_HOME", str(native))
    assert native_auth_environment()["CODEX_HOME"] == str(native)


from tests.test_execution_operations import operations  # noqa: E402,F401


def test_explicit_settled_capability_replacement_revokes_old_cached_principal(operations):  # noqa: F811
    from tests.test_execution_continuity import child, command, prepare, report, state

    operation, root, _, _, _, _, _ = operations
    prepare(operation, root)
    old = child(operation, "al-engineer")
    old_id = old["thread_id"]
    report(
        operation,
        root,
        "agent-register",
        {"id": old_id, "name": "Old actual fixture child", "role": "al-engineer", "task": "A"},
    )
    report(operation, root, "agent-update", {"id": old_id, "status": "Running"})
    command(operation, root, "integration", "task-start", "--task", "A")
    command(
        operation,
        root,
        "writer",
        "start",
        "--id",
        "old-writer",
        "--role",
        "al-engineer",
        "--files",
        "work",
        "--task",
        "A",
        "--agent",
        old_id,
    )
    fresh = child(operation, "al-engineer")
    path = next(
        operation.registry.native_root.glob("sessions/*/*/*/*" + fresh["thread_id"] + ".jsonl")
    )
    first = json.loads(path.read_text())
    first["payload"]["timestamp"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(first) + "\n")
    request = {
        "id": fresh["thread_id"],
        "name": "Fresh replacement fixture",
        "role": "al-engineer",
        "task": "A",
        "replaces": old_id,
        ("release_reason"): (
            "Release the old tool capability after its scoped writer and checks settle."
        ),
    }
    with pytest.raises(ValueError, match="settle predecessor writer"):
        report(operation, root, "agent-register", request)
    assert old_id in operation.assignments
    command(operation, root, "writer", "finish", "--id", "old-writer", "--outcome", "success")
    report(operation, root, "agent-register", request)
    assert old_id not in operation.assignments and fresh["thread_id"] in operation.assignments
    assert state(operation)["integration"]["agents"][old_id]["status"] == "Running"
    receipt = json.loads(
        (operation.registry.directory / "capability-releases" / (old_id + ".json")).read_bytes()
    )
    assert receipt["host_termination_observed"] is False
    assert list((operation.directory / "assignment-history").glob("*.json"))
    with pytest.raises(ValueError, match="stale_principal"):
        operation.dispatch(old, "read_file", {"path": "README.md"})
    with pytest.raises(ValueError, match="stale_principal"):
        operation.registry.enroll(old_id)


def test_native_epoch_membership_is_immutable_and_does_not_read_transcripts(tmp_path):
    from harness.execution_native_epoch import bind_native_boundary

    native = tmp_path / "native"
    sessions = native / "sessions/2026/10/29"
    sessions.mkdir(parents=True)
    old, fresh = str(uuid.uuid4()), str(uuid.uuid4())
    (sessions / ("rollout-" + old + ".jsonl")).write_text(
        "Not parsed: synthetic private transcript sentinel"
    )
    selector = {
        "epoch_id": "epoch-new",
        "authority_generation": 1,
        "activated_at": 100,
        "predecessor_child_ids": [],
    }
    first = bind_native_boundary(tmp_path / "epoch-registry", native, selector)
    assert first["preexisting_child_ids"] == [old]
    (sessions / ("rollout-" + fresh + ".jsonl")).write_text("Not parsed: synthetic new invocation")
    second = bind_native_boundary(tmp_path / "epoch-registry", native, selector)
    assert second == first and fresh not in second["preexisting_child_ids"]
