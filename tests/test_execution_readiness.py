"""Readiness labels follow scoped persisted observations, never candidate scans."""

import copy
import json

import pytest

from harness.execution_projection import execution_view, ingest_event
from tests.execution_helpers import approved_execution, event, started


def setup_readiness(controller, sample_repo, monkeypatch):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    task = {
        "id": "A",
        "title": "Admitted ordinary task",
        "status": "Waiting",
        "dependencies": [],
        "files": ["app"],
        "criteria": ["AC-01"],
        "revision": 1,
        "admission": {"revision": 1, "graph_revision": 1},
        "admission_observation": {"sequence": 3},
    }
    registered = {
        k: v for k, v in task.items() if k not in {"admission", "admission_observation"}
    } | {"status": "Idle"}
    ingest_event(
        controller.conn, eid, event(controller.conn, eid, 2, "task.registered", registered)
    )
    ingest_event(controller.conn, eid, event(controller.conn, eid, 3, "task.updated", task))
    controller.conn.execute("UPDATE executions SET status='Interrupted' WHERE id=?", (eid,))
    return tid, eid, task


def issue(controller, eid, cause="capability_unavailable"):
    return {
        "id": "candidate-inspection",
        "revision": 1,
        "cause": cause,
        "task_ids": ["A"],
        "paths": ["app"],
        "capability_or_check_ref": "candidate-capture",
        "diagnostic_ref": {"sha256": "0" * 64},
        "state": "Open",
        "next_action": "Repair candidate inspection",
        "observed_binding": event(controller.conn, eid, 4, "issue.updated", {})["binding"],
    }


def test_interrupted_capture_failure_is_not_implementation_ready(
    controller, sample_repo, monkeypatch
):
    _, eid, _ = setup_readiness(controller, sample_repo, monkeypatch)
    ingest_event(
        controller.conn,
        eid,
        event(controller.conn, eid, 4, "issue.updated", issue(controller, eid)),
    )
    view = execution_view(controller.conn, eid)
    assert view["implementation"]["status"] == "technically_unavailable"
    assert view["implementation"]["blocking_issue_refs"] == ["candidate-inspection"]


@pytest.mark.parametrize("unrelated_capability", [False, True])
def test_admitted_scope_remains_usable_with_unrelated_verification_debt(
    controller, sample_repo, monkeypatch, unrelated_capability
):
    _, eid, _ = setup_readiness(controller, sample_repo, monkeypatch)
    debt = issue(controller, eid, "coverage_unknown")
    if unrelated_capability:
        debt.update(cause="provider_unavailable", task_ids=[], paths=["other-service"])
    ingest_event(controller.conn, eid, event(controller.conn, eid, 4, "issue.updated", debt))
    view = execution_view(controller.conn, eid)
    assert view["implementation"]["status"] == "ready"
    assert view["implementation"]["ready_task_ids"] == ["A"]
    assert view["verification"]["status"] == "incomplete"
    assert view["verification"]["obligation_refs"] == ["candidate-inspection"]


def test_missing_admission_is_unverified_even_when_host_is_running(
    controller, sample_repo, monkeypatch
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"


def test_bridge_upgrade_needs_fresh_bound_admission(controller, sample_repo, monkeypatch):
    tid, eid, task = setup_readiness(controller, sample_repo, monkeypatch)
    from harness.execution_store import get_execution

    saved = get_execution(controller.conn, eid)
    controller.workspace(tid).record_event(
        "execution.bridge_upgraded",
        {
            "id": eid,
            "binding": saved["binding"],
            "handoff_hash": saved["handoff_hash"],
            "al_session_id": saved["al_session_id"],
            "al_run_id": saved["al_run_id"],
            "at": 10**12,
            "to": "new-bridge",
        },
    )
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"
    fresh = event(controller.conn, eid, 4, "task.updated", task)
    fresh["timestamp"] = 10**12 + 1
    stale = copy.deepcopy(fresh)
    controller.conn.execute(
        "INSERT INTO execution_events(event_id,execution_id,sequence,kind,record,epoch_id) "
        "VALUES (?,?,?,?,?,?)",
        (stale["event_id"], eid, 4, stale["kind"], json.dumps(stale), "old-epoch"),
    )
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"
    ingest_event(controller.conn, eid, fresh)
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"
    task["admission_observation"] = {"sequence": 5}
    readmitted = event(controller.conn, eid, 5, "task.updated", task)
    readmitted["timestamp"] = 10**12 + 2
    ingest_event(controller.conn, eid, readmitted)
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "ready"


def test_specific_fresh_resolution_supersedes_failure(controller, sample_repo, monkeypatch):
    _, eid, _ = setup_readiness(controller, sample_repo, monkeypatch)
    failure = issue(controller, eid)
    ingest_event(controller.conn, eid, event(controller.conn, eid, 4, "issue.updated", failure))
    resolved = failure | {
        "revision": 2,
        "state": "Resolved",
        "resolution_ref": {"sha256": "1" * 64},
    }
    stale = event(controller.conn, eid, 5, "issue.updated", resolved)
    controller.conn.execute(
        "INSERT INTO execution_events(event_id,execution_id,sequence,kind,record,epoch_id) "
        "VALUES (?,?,?,?,?,?)",
        (stale["event_id"], eid, 5, stale["kind"], json.dumps(stale), "old-epoch"),
    )
    assert (
        execution_view(controller.conn, eid)["implementation"]["status"]
        == "technically_unavailable"
    )
    ingest_event(controller.conn, eid, event(controller.conn, eid, 5, "issue.updated", resolved))
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "ready"


def test_graph_change_invalidates_prior_task_admission(controller, sample_repo, monkeypatch):
    _, eid, task = setup_readiness(controller, sample_repo, monkeypatch)
    other = {k: v for k, v in task.items() if k not in {"admission", "admission_observation"}} | {
        "id": "B",
        "files": ["other"],
        "status": "Idle",
    }
    ingest_event(controller.conn, eid, event(controller.conn, eid, 4, "task.registered", other))
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"
    ingest_event(controller.conn, eid, event(controller.conn, eid, 5, "task.updated", task))
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"
    task["admission"]["graph_revision"] = 2
    task["admission_observation"] = {"sequence": 6}
    ingest_event(controller.conn, eid, event(controller.conn, eid, 6, "task.updated", task))
    assert execution_view(controller.conn, eid)["implementation"]["ready_task_ids"] == ["A"]


def test_progress_preserves_current_admission_without_refreshing_it(
    controller, sample_repo, monkeypatch
):
    _, eid, task = setup_readiness(controller, sample_repo, monkeypatch)
    task["activity"] = "Implementing the admitted change"
    ingest_event(controller.conn, eid, event(controller.conn, eid, 4, "task.updated", task))
    assert execution_view(controller.conn, eid)["implementation"]["ready_task_ids"] == ["A"]


@pytest.mark.parametrize("sequence", [None, 1, 2, 4])
def test_missing_or_wrong_admission_provenance_stays_unverified(
    controller, sample_repo, monkeypatch, sequence
):
    _, eid, task = setup_readiness(controller, sample_repo, monkeypatch)
    if sequence is None:
        task.pop("admission_observation")
    else:
        task["admission_observation"] = {"sequence": sequence}
    update = event(controller.conn, eid, 4, "task.updated", task)
    if sequence == 4:
        # A future pointer cannot borrow a prior event's receipt; this event's
        # revision differs from the asserted admission receipt.
        update["payload"]["revision"] = 2
    ingest_event(controller.conn, eid, update)
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"


def test_output_invalidation_revokes_producer_and_dependent_admission(
    controller, sample_repo, monkeypatch
):
    _, eid, task = setup_readiness(controller, sample_repo, monkeypatch)
    other = {k: v for k, v in task.items() if k not in {"admission", "admission_observation"}} | {
        "id": "B",
        "files": ["other"],
        "dependencies": ["A"],
        "status": "Idle",
    }
    ingest_event(controller.conn, eid, event(controller.conn, eid, 4, "task.registered", other))
    task["admission"]["graph_revision"] = 2
    task["admission_observation"] = {"sequence": 5}
    ingest_event(controller.conn, eid, event(controller.conn, eid, 5, "task.updated", task))
    other.update(status="Waiting", admission=copy.deepcopy(task["admission"]))
    other["admission_observation"] = {"sequence": 6}
    ingest_event(controller.conn, eid, event(controller.conn, eid, 6, "task.updated", other))
    assert execution_view(controller.conn, eid)["implementation"]["ready_task_ids"] == ["A", "B"]
    ingest_event(
        controller.conn,
        eid,
        event(
            controller.conn,
            eid,
            7,
            "output.invalidated",
            {"task_id": "A", "reason": "Producer output changed"},
        ),
    )
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"
    ingest_event(controller.conn, eid, event(controller.conn, eid, 8, "task.updated", other))
    assert execution_view(controller.conn, eid)["implementation"]["status"] == "unverified"
