"""Unindexed selection, late legacy writes, bounded projection and concurrent CAS."""

# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from harness.db import connect
from harness.reliability import catalog, legacy_projection
from tests.incident_helpers import seed, selected
from tests.test_reliability_store import observed  # noqa: F401


def test_more_than_100_exact_ids_and_projection_does_not_move_read_pages(observed):
    conn = observed[2].conn
    seed(observed, 104)
    scoped = selected(observed)
    first = catalog.list_incidents(conn, scoped, limit=30)
    remaining_before = catalog.list_incidents(conn, scoped, limit=100, cursor=first["next_cursor"])
    result = legacy_projection.project_legacy_batch(
        conn, scoped, expected_revision=0, request_id="project-1"
    )
    assert result["projected_count"] == 100 and result["more_as_of_commit"]
    remaining_after = catalog.list_incidents(conn, scoped, limit=100, cursor=first["next_cursor"])
    assert [r["incident_id"] for r in remaining_before["items"]] == [
        r["incident_id"] for r in remaining_after["items"]
    ]
    second = legacy_projection.project_legacy_batch(
        conn, scoped, expected_revision=1, request_id="project-2"
    )
    assert second["projected_count"] == 5 and not second["more_as_of_commit"]
    seen, cursor = [], None
    while True:
        page = catalog.list_incidents(conn, scoped, limit=20, cursor=cursor)
        seen.extend(i["incident_id"] for i in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(set(seen)) == len(seen) == 105
    assert set(seen) == {
        r[0] for r in conn.execute("SELECT incident_id FROM reliability_incidents")
    }


def test_empty_batch_replay_then_late_backdated_old_pin_rows(observed):
    conn = observed[2].conn
    seed(observed)
    scoped = selected(observed)
    legacy_projection.project_legacy_batch(conn, scoped, expected_revision=0, request_id="first")
    empty = legacy_projection.project_legacy_batch(
        conn, scoped, expected_revision=1, request_id="empty"
    )
    assert empty["projected_count"] == 0 and not empty["more_as_of_commit"]
    late = []
    for time in ("1990-01-01", "2026-01-01T00:00:00+00:00", "2099-01-01"):
        late += seed(observed, created_at=time)
    visible = catalog.list_incidents(conn, scoped)
    assert set(late) <= {
        r["incident_id"] for r in visible["items"] if r["indexing"] == "legacy_unindexed"
    }
    assert (
        legacy_projection.project_legacy_batch(
            conn, scoped, expected_revision=1, request_id="empty"
        )
        == empty
    )
    assert len(legacy_projection.pending(conn, scoped)) == 3
    with pytest.raises(catalog.IncidentConflict):
        legacy_projection.project_legacy_batch(
            conn, scoped, expected_revision=2, request_id="empty"
        )
    result = legacy_projection.project_legacy_batch(
        conn, scoped, expected_revision=2, request_id="late"
    )
    assert result["projected_count"] == 3 and result["checkpoint_revision"] == 3
    assert conn.execute("SELECT count(*) FROM incident_operation_sources").fetchone()[0] == 5


def test_concurrent_projection_checkpoint_and_same_request_replay(observed):
    seed(observed, 2)
    scoped = selected(observed)
    path = observed[0].database

    def project(request):
        with closing(connect(path)) as conn:
            try:
                return legacy_projection.project_legacy_batch(
                    conn, scoped, expected_revision=0, request_id=request, limit=1
                )
            except catalog.IncidentConflict:
                return "conflict"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(project, ["one", "two"]))
    assert results.count("conflict") == 1
    winner = "one" if results[0] != "conflict" else "two"
    assert project(winner) == next(r for r in results if r != "conflict")
    conn = observed[2].conn
    assert conn.execute("SELECT count(*) FROM incident_operation_sources").fetchone()[0] == 1


def test_projection_fault_rolls_back_checkpoint_and_all_facts(observed):
    conn = observed[2].conn
    seed(observed, 3)
    conn.execute(
        "CREATE TRIGGER injected BEFORE INSERT ON incident_operation_sources "
        "WHEN (SELECT count(*) FROM incident_operation_sources)=1 "
        "BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(Exception, match="injected"):
        legacy_projection.project_legacy_batch(
            conn, selected(observed), expected_revision=0, request_id="fault"
        )
    for table in (
        "reliability_incident_catalog",
        "incident_operation_sources",
        "incident_projection_requests",
        "incident_projection_checkpoints",
    ):
        assert conn.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


def test_old_pin_insert_serializes_after_projection_and_remains_discoverable(observed, monkeypatch):
    import threading

    conn = observed[2].conn
    seed(observed)
    scoped = selected(observed)
    entered, release, inserting = threading.Event(), threading.Event(), threading.Event()
    original = legacy_projection.project_operation

    def paused(connection, incident):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(connection, incident)

    monkeypatch.setattr(legacy_projection, "project_operation", paused)

    def batch():
        with closing(connect(observed[0].database)) as other:
            return legacy_projection.project_legacy_batch(
                other, scoped, expected_revision=0, request_id="concurrent-batch"
            )

    # This is an old-writer persistence fixture, not a qualified runtime callback.
    def late_insert():
        import sqlite3

        from harness.db import transaction

        other = sqlite3.connect(observed[0].database, autocommit=True)
        other.row_factory = sqlite3.Row
        other.execute("PRAGMA foreign_keys=ON")
        other.execute("PRAGMA busy_timeout=5000")
        try:
            inserting.set()
            with transaction(other):
                legacy_observed = (
                    observed[0],
                    observed[1],
                    type("Controller", (), {"conn": other})(),
                )
                return seed(legacy_observed, created_at="1980-01-01")[0]
        finally:
            other.close()

    with ThreadPoolExecutor(2) as pool:
        projected = pool.submit(batch)
        assert entered.wait(10)
        inserted = pool.submit(late_insert)
        assert inserting.wait(10)
        release.set()
        receipt = projected.result(timeout=15)
        late = inserted.result(timeout=15)
    assert receipt["projected_count"] == 2 and not receipt["more_as_of_commit"]
    assert catalog.detail(conn, scoped, late)["indexing"] == "legacy_unindexed"
    next_batch = legacy_projection.project_legacy_batch(
        conn, scoped, expected_revision=1, request_id="after-concurrent"
    )
    assert next_batch["projected_count"] == 1
