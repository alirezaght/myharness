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
