# ruff: noqa: F811
"""The reliability marker changes with the execution's reliability facts and only with them."""

import time
from contextlib import closing

from harness.db import connect
from harness.reliability import store
from harness.reliability.revision import reliability_revision
from tests.test_reliability_store import observed  # noqa: F401


def _marker(observed) -> str:
    operation = observed[0]
    with closing(connect(operation.database)) as conn:
        return reliability_revision(conn, operation.execution_id)


def test_marker_follows_recorded_started_and_finished_observations(observed):
    operation, principal, _ = observed
    empty = _marker(observed)
    assert _marker(observed) == empty  # stable while nothing changes
    observation = store.begin(operation, principal, "read_file")
    started = _marker(observed)
    assert started != empty
    # Finishing updates the started row in place and adds the incident.
    store.finish(operation, observation, "DISPATCH_REJECTED")
    finished = _marker(observed)
    assert finished not in {empty, started}
    assert _marker(observed) == finished


def test_marker_follows_an_in_place_update_that_adds_no_row(observed):
    operation, principal, _ = observed
    observation = store.begin(operation, principal, "read_file")
    started = _marker(observed)
    with closing(connect(operation.database)) as conn:
        rows = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("reliability_observations", "reliability_incidents")
        }
    # A successful return only updates the started observation row.
    store.finish(operation, observation, "RETURNED")
    with closing(connect(operation.database)) as conn:
        assert {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in rows
        } == rows
    assert _marker(observed) != started


def test_marker_ignores_writes_outside_the_reliability_tables(observed):
    operation, _, controller = observed
    before = _marker(observed)
    # A write outside the reliability, incident and policy tables (a task event).
    controller.workspace(
        controller.conn.execute(
            "SELECT task_id FROM executions WHERE id=?", (operation.execution_id,)
        ).fetchone()[0]
    ).record_event("note", {"text": "unrelated"})
    controller.conn.commit()
    assert _marker(observed) == before


def test_marker_reads_only_and_stays_cheap_for_a_large_history(observed):
    operation, principal, _ = observed
    for _ in range(300):
        observation = store.begin(operation, principal, "read_file")
        store.finish(operation, observation, "DISPATCH_REJECTED")
    with closing(connect(operation.database)) as conn:
        changes = conn.total_changes
        started = time.perf_counter()
        for _ in range(10):
            reliability_revision(conn, operation.execution_id)
        elapsed = (time.perf_counter() - started) / 10
        assert conn.total_changes == changes
    # Computed once per stream poll per open dashboard; record, do not gate, the timing.
    print(f"reliability_revision: {elapsed * 1000:.2f} ms with 300 observations")


def _synthetic():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE policy_attempts(id TEXT, execution_id TEXT, revision INTEGER, state TEXT);
        CREATE TABLE incident_investigation_revisions(id TEXT, revision INTEGER);
        CREATE TABLE unrelated_rows(id TEXT, execution_id TEXT);
        INSERT INTO policy_attempts VALUES
            ('a1', 'exec-1', 1, 'started'), ('a2', 'exec-1', 5, 'settled'),
            ('b1', 'exec-2', 1, 'started');
        INSERT INTO incident_investigation_revisions VALUES ('r1', 1);
        """
    )
    return conn


def test_marker_is_scoped_by_execution_and_sums_revisions():
    conn = _synthetic()
    before = reliability_revision(conn, "exec-1")
    # Another execution's scoped row does not change this execution's marker.
    conn.execute("UPDATE policy_attempts SET revision=2, state='settled' WHERE id='b1'")
    assert reliability_revision(conn, "exec-1") == before
    # A revision bump on a scoped row that is not the maximum, with an equal-length state.
    conn.execute("UPDATE policy_attempts SET revision=2 WHERE id='a1'")
    bumped = reliability_revision(conn, "exec-1")
    assert bumped != before
    # Unscoped family tables count rows; tables outside the families are ignored.
    conn.execute("INSERT INTO incident_investigation_revisions VALUES ('r2', 2)")
    added = reliability_revision(conn, "exec-1")
    assert added != bumped
    conn.execute("INSERT INTO unrelated_rows VALUES ('u1', 'exec-1')")
    assert reliability_revision(conn, "exec-1") == added
