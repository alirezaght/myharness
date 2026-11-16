"""Exact retained amendment bytes, acknowledgment identity, and derived layout checks."""

import json

import pytest

from harness.execution_contract import canonical, digest
from harness.execution_handoff import read_handoff
from harness.execution_models import ExecutionConflict
from harness.execution_recovery_lineage import _revisions, build_recovery_lineage
from harness.execution_store import bind_al_run, get_execution
from tests.execution_helpers import approved_execution, binding, event


@pytest.mark.parametrize("case", ["valid", "wrong_hash", "no_ack", "rejected"])
def test_recovery_amendment_requires_ordered_source_bound_acknowledgment(
    controller, sample_repo, monkeypatch, case
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    bind_al_run(controller.conn, eid, execution["al_session_id"], "run123")
    execution = get_execution(controller.conn, eid)
    envelope = read_handoff(execution["handoff_path"])
    sha = digest(canonical(envelope["task_package"]).encode()).split(":")[1]
    previous = {
        "session_id": execution["al_session_id"],
        "run_id": "run123",
        "repository_identity": {"root": str(sample_repo)},
    }
    protected = {
        "predecessor_transaction": previous,
        "predecessor_initialization": dict(previous, original_task_package={"sha256": sha}),
        "source_anchors": {"explicit_test_observation": True},
    }
    registry = sample_repo / "docs/decisions/DEC-0001-fixture.md"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("# Explicit synthetic decision contract fixture\n")
    registry_ref = {
        "id": "DEC-0001",
        "path": str(registry),
        "sha256": digest(registry.read_bytes()),
    }
    delivery = {
        "schema_version": 1,
        "id": "decision-one",
        "binding": binding(controller.conn, eid),
        "blocker_id": "blocker-one",
        "blocker_revision": 1,
        "verdict": "reject" if case == "rejected" else "accept",
        "text": "Preserve the source-bound accepted behavior for this fixture scope.",
        "tasks": ["task-one"],
        "criteria": ["AC-01"],
        "amendment": case != "rejected",
        "registry": registry_ref,
        "base_revision": _revisions(sha, [])[-1],
    }
    amendment = {
        "id": delivery["id"],
        "text": delivery["text"],
        "source": "developer Decision Registry DEC-0001",
        "affected_criteria": ["AC-01"],
        "affected_tasks": ["task-one"],
        "base_revision": delivery["base_revision"],
        "delivery_hash": digest(delivery),
        "registry": registry_ref,
        "scoped": True,
    }
    artifact_sha = digest(json.dumps(amendment, sort_keys=True).encode()).split(":")[1]
    revision = _revisions(sha, [{"artifact": {"sha256": artifact_sha}}])[-1]
    controller.conn.execute(
        "INSERT INTO execution_decisions "
        "(id,execution_id,blocker_id,blocker_revision,request_id,request,state,registry_id,"
        "registry_path,registry_bytes,registry_hash,delivery,created_at,updated_at) "
        "VALUES ('decision-one',?,'blocker-one',1,'request-one','{}',"
        "'Resolved',?,?,?,?,?,'now','now')",
        (
            eid,
            registry_ref["id"],
            str(registry),
            registry.read_text(),
            registry_ref["sha256"],
            canonical(delivery),
        ),
    )
    events = [
        event(
            controller.conn,
            eid,
            1,
            "task.registered",
            {
                "id": "task-one",
                "title": "Fixture scope",
                "status": "Idle",
                "dependencies": [],
                "files": ["app"],
                "criteria": ["AC-01"],
                "revision": 1,
            },
        ),
        event(
            controller.conn,
            eid,
            2,
            "amendment.created",
            {
                "id": delivery["id"],
                "tasks": ["task-one"],
                "revision": revision,
                "amendment": artifact_sha,
            },
        ),
    ]
    if case != "no_ack":
        events.append(
            event(
                controller.conn,
                eid,
                3,
                "decision.acknowledged",
                {
                    "delivery_id": delivery["id"],
                    "blocker_id": "blocker-one",
                    "state": "Rejected" if case == "rejected" else "RevalidationRequired",
                    "decision_hash": "sha256:" + "0" * 64
                    if case == "wrong_hash"
                    else digest(delivery),
                },
            )
        )
    for record in events:
        controller.conn.execute(
            "INSERT INTO execution_events(event_id,execution_id,sequence,kind,record) "
            "VALUES (?,?,?,?,?)",
            (record["event_id"], eid, record["sequence"], record["kind"], canonical(record)),
        )
    if case == "wrong_hash":
        with pytest.raises(ExecutionConflict, match="provenance differs"):
            build_recovery_lineage(controller, execution, envelope, protected)
        return
    lineage = build_recovery_lineage(controller, execution, envelope, protected)
    assert len(lineage["amendments"]) == (1 if case == "valid" else 0)
    if case == "valid":
        assert lineage["revision_hashes"][-1] == revision
        assert lineage["historical_scopes"][0]["tasks"]["task-one"]["files"] == ["app"]
    elif case == "no_ack":
        assert lineage["pending_decisions"][0]["id"] == delivery["id"]
    else:
        assert lineage["rejected_decisions"][0]["id"] == delivery["id"]


@pytest.mark.parametrize("generation,epoch", [(0, "legacy-0"), (2, "epoch-1234abcd")])
def test_durable_layout_is_derived_and_rejects_alternate_paths(tmp_path, generation, epoch):
    from al.installer.runtime_durable_layout import directory

    storage = tmp_path / "issuer"
    storage.mkdir(mode=0o700)
    transaction = {
        "run_id": "1234abcd",
        "repository_identity": {"root": str(tmp_path / "checkout")},
        "runtime_layout": "harness-durable-epoch-v1",
        "harness_binding": {
            "authority_identity": "a" * 64,
            "execution_id": "exec-one",
            "epoch_id": epoch,
            "authority_generation": generation,
        },
    }
    derived = storage / "harness-runtimes" / ("a" * 64) / "exec-one" / epoch
    transaction["runtime_directory"] = str(derived)
    assert directory(transaction, storage, create=True) == derived
    transaction["runtime_directory"] = str(tmp_path / "caller-selected")
    with pytest.raises(ValueError, match="not derived"):
        directory(transaction, storage)
    transaction["runtime_directory"] = str(derived)
    derived.chmod(0o755)
    with pytest.raises(ValueError, match="private and owned"):
        directory(transaction, storage)
