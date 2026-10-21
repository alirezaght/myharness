import copy

import pytest

from harness.execution_contract import loads, progress
from harness.execution_projection import execution_view, ingest_event
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution, event, started


def test_atomic_replay_conflicts_and_gaps(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    registered = event(
        controller.conn,
        eid,
        2,
        "task.registered",
        {
            "id": "A",
            "title": "A",
            "status": "Idle",
            "dependencies": [],
            "files": ["svc"],
            "criteria": ["AC-01"],
            "revision": 1,
        },
    )
    assert ingest_event(controller.conn, eid, registered)
    assert not ingest_event(controller.conn, eid, registered)
    changed = copy.deepcopy(registered)
    changed["payload"]["title"] = "conflict"
    with pytest.raises(ValueError, match="duplicate"):
        ingest_event(controller.conn, eid, changed)
    assert get_execution(controller.conn, eid)["cursor"] == 2
    gap = event(controller.conn, eid, 4, "phase.changed", {"phase": "IMPLEMENTATION"})
    with pytest.raises(ValueError, match="gap"):
        ingest_event(controller.conn, eid, gap)
    unknown = event(
        controller.conn, eid, 3, "agent.updated", {"id": "missing", "status": "Running"}
    )
    with pytest.raises(ValueError, match="registration"):
        ingest_event(controller.conn, eid, unknown)
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0] == 2
    assert get_execution(controller.conn, eid)["cursor"] == 2
    assert execution_view(controller.conn, eid)["tasks"][0]["title"] == "A"


def test_foreign_and_reported_success_refused(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    foreign = event(controller.conn, eid, 2, "phase.changed", {"phase": "DONE"})
    foreign["binding"]["approval_id"] += 1
    with pytest.raises(ValueError, match="foreign"):
        ingest_event(controller.conn, eid, foreign)
    fake = event(
        controller.conn, eid, 2, "implementation.result", {"status": "APPROVED", "approval": {}}
    )
    with pytest.raises(ValueError, match="guarded"):
        ingest_event(controller.conn, eid, fake)
    assert get_execution(controller.conn, eid)["status"] != "Completed"


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"partial":'])
def test_strict_json(raw):
    with pytest.raises(ValueError):
        loads(raw)


@pytest.mark.parametrize(
    "value",
    [
        {"completed": True, "total": 4, "unit": "tests"},
        {"completed": 5, "total": 4, "unit": "tests"},
        {"value": 50, "source": "", "label": "estimate"},
    ],
)
def test_honest_progress(value):
    with pytest.raises(ValueError):
        progress(value)


def test_terminal_ingestion_allows_identical_replay_but_no_new_work(
    controller, sample_repo, monkeypatch
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    original = event(controller.conn, eid, 2, "phase.changed", {"phase": "PLANNING"})
    ingest_event(controller.conn, eid, original)
    controller.conn.execute("UPDATE executions SET status='Completed' WHERE id=?", (eid,))
    assert not ingest_event(controller.conn, eid, original)
    with pytest.raises(ValueError, match="terminal"):
        ingest_event(
            controller.conn,
            eid,
            event(controller.conn, eid, 3, "phase.changed", {"phase": "IMPLEMENTATION"}),
        )
    assert get_execution(controller.conn, eid)["cursor"] == 2
