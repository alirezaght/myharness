from pathlib import Path

import pytest

from harness.execution_authority import require_execution_authority
from harness.execution_decisions import materialize_decision, record_execution_decision
from harness.execution_projection import ingest_event
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution, event, started


def test_registry_intent_retries_keep_frozen_id_and_base_authority(
    controller, sample_repo, monkeypatch
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    task = {
        "id": "A",
        "title": "A",
        "status": "Idle",
        "dependencies": [],
        "files": ["svc"],
        "criteria": ["AC-01"],
        "revision": 1,
    }
    ingest_event(controller.conn, eid, event(controller.conn, eid, 2, "task.registered", task))
    blocker = {
        "id": "design",
        "revision": 1,
        "tasks": ["A"],
        "affected": ["A"],
        "question": "Which design?",
        "context": "Explicit developer architecture required",
        "alternatives": ["a", "b"],
        "recommendation": "a",
    }
    ingest_event(controller.conn, eid, event(controller.conn, eid, 3, "blocker.raised", blocker))
    # Delivery cannot yet find a bound source file. The Registry materialization survives.
    kwargs = dict(
        blocker_revision=1,
        request_id="request-one",
        verdict="reject",
        decision_text="Reject recommendation; keep waiting for my architecture decision.",
        tasks=["A"],
        criteria=["AC-01"],
    )
    with pytest.raises(OSError):
        record_execution_decision(controller, eid, "design", **kwargs)
    intent = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    path = Path(intent["registry_path"])
    before = path.read_bytes()
    assert materialize_decision(controller, intent["id"])["registry_id"] == intent["registry_id"]
    with pytest.raises(OSError):
        record_execution_decision(controller, eid, "design", **kwargs)
    assert path.read_bytes() == before
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_decisions").fetchone()[0] == 1
    assert (
        len(list((sample_repo / "docs/decisions").glob("DEC-*.md"))) == 3
    )  # two repository fixtures plus new decision
    assert require_execution_authority(controller, eid)["execution_id"] == eid
    assert not any(
        e.kind == "decision.recorded"
        for e in controller.workspace(get_execution(controller.conn, eid)["task_id"]).events()
    )
    with pytest.raises(ValueError, match="changed content"):
        record_execution_decision(
            controller,
            eid,
            "design",
            **(kwargs | {"decision_text": "Changed decision for same request ID is forbidden"}),
        )


def test_frozen_intent_recovers_crash_before_registry_file(controller, sample_repo, monkeypatch):
    import harness.execution_decisions as service

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    ingest_event(
        controller.conn,
        eid,
        event(
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
        ),
    )
    ingest_event(
        controller.conn,
        eid,
        event(
            controller.conn,
            eid,
            3,
            "blocker.raised",
            {
                "id": "design",
                "revision": 1,
                "tasks": ["A"],
                "question": "Choose architecture",
                "context": "Need explicit decision",
                "alternatives": ["one", "two"],
                "recommendation": "one",
            },
        ),
    )
    original = service.durable_file

    def crash(*args, **kwargs):
        raise OSError("crash before Registry publication")

    monkeypatch.setattr(service, "durable_file", crash)
    with pytest.raises(OSError, match="before Registry"):
        record_execution_decision(
            controller,
            eid,
            "design",
            blocker_revision=1,
            request_id="frozen-request",
            verdict="alternative",
            decision_text="Choose the alternate architecture after this explicit developer review.",
            tasks=["A"],
            criteria=["AC-01"],
        )
    intent = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    assert intent["registry_id"] and not Path(intent["registry_path"]).exists()
    monkeypatch.setattr(service, "durable_file", original)
    recovered = materialize_decision(controller, intent["id"])
    assert recovered["registry_id"] == intent["registry_id"] and recovered["state"] == "Ready"
    assert Path(recovered["registry_path"]).read_text() == intent["registry_bytes"]


def test_inbox_publication_is_complete_at_first_visibility_and_never_overwrites(
    tmp_path, monkeypatch
):
    import os

    from harness.execution_handoff import atomic_file

    target = tmp_path / "inbox" / "decision.json"
    raw = b'{"complete":"' + b"x" * 65536 + b'"}'
    original = os.link
    publications = []

    def observe(source, destination):
        if not target.exists():
            assert Path(source).read_bytes() == raw
            publications.append(True)
        return original(source, destination)

    monkeypatch.setattr(os, "link", observe)
    atomic_file(target, raw)
    assert publications == [True] and target.read_bytes() == raw
    atomic_file(target, raw)
    with pytest.raises(ValueError, match="conflicts"):
        atomic_file(target, b'{"changed":true}')
    assert target.read_bytes() == raw
