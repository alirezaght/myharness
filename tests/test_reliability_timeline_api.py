# ruff: noqa: F811
"""Scoped HTTP timeline over real process facts and independently measured privacy canaries."""

import hashlib
import json
from urllib.parse import urlencode

import pytest

from harness.dashboard.reliability_view import read_timeline
from harness.reliability import store
from tests.test_dashboard import fetch, serve
from tests.test_reliability_processes import processes, settled, start_process  # noqa: F401
from tests.test_reliability_store import observed  # noqa: F401


def query_for(processes, **extra):
    operation = processes[0]
    return urlencode(
        {
            "task": operation.envelope["task_id"],
            "execution": operation.execution_id,
            "epoch": operation.epoch_id,
            **extra,
        }
    )


def test_real_http_process_detail_scope_pagination_and_privacy(processes):
    secret = "timeline-private-code-output-5741"
    oid, handle = start_process(processes, f"print({secret!r})")
    settled(processes, handle)
    operation, principal, controller = processes
    for _ in range(13):
        attempt = store.begin(operation, principal, "read_file")
        store.finish(operation, attempt, "DISPATCH_REJECTED")
    changes = controller.conn.total_changes
    with serve(operation.database, None) as server:
        path = "/api/execution/timeline?" + query_for(processes, limit=10)
        seen = set()
        while path:
            status, _, body = fetch(server, path)
            assert status == 200
            assert (
                secret not in body.decode()
                and hashlib.sha256(secret.encode()).hexdigest() not in body.decode()
            )
            data = json.loads(body)
            assert data["trace_id"] and data["measurement_availability"]["tokens"] == "unavailable"
            ids = {item["item_id"] for item in data["items"]}
            assert not ids & seen
            seen |= ids
            path = (
                "/api/execution/timeline?" + query_for(processes, limit=10, **data["next_cursor"])
                if data["next_cursor"]
                else None
            )
        assert len(seen) == 31  # 14 operation attempts (two facts each) and three process events.
        status, _, body = fetch(
            server,
            "/api/execution/timeline?"
            + query_for(processes, kind="process.exited", principal=principal["thread_id"]),
        )
        assert status == 200
        item = json.loads(body)["items"][0]
        assert item["parent_observation_id"] == oid and item["duration_ms"] >= 0
        status, _, body = fetch(
            server, "/api/execution/timeline/span?" + query_for(processes, span=item["span_id"])
        )
        assert status == 200 and json.loads(body)["span"]["returncode"] == 0
        assert (
            secret not in body.decode()
            and hashlib.sha256(secret.encode()).hexdigest() not in body.decode()
        )
        for extra in (
            {"task": "foreign"},
            {"execution": "foreign"},
            {"epoch": "foreign"},
            {"span": "foreign"},
        ):
            status, _, body = fetch(
                server,
                "/api/execution/timeline/span?"
                + query_for(processes, span=item["span_id"], **({} if "span" in extra else extra))
                if "span" not in extra
                else "/api/execution/timeline/span?" + query_for(processes, **extra),
            )
            assert status == 404 and json.loads(body) == {"error": "Timeline scope not found"}
    assert controller.conn.total_changes == changes


@pytest.mark.parametrize(
    "extra",
    [
        "&epoch=duplicate",
        "&unknown=1",
        "&kind=untrusted",
        "&limit=101",
        "&limit=0",
        "&before_id=missing-pair",
        "&before_time=bad",
        "&principal=<script>",
    ],
)
def test_strict_query_fields(processes, extra):
    assert read_timeline(processes[0].database, query_for(processes) + extra)[0] == 400


def test_missing_historical_trace_does_not_write_or_backfill(processes):
    operation, _, controller = processes
    controller.conn.execute("DELETE FROM reliability_traces")
    before = controller.conn.total_changes
    status, data = read_timeline(operation.database, query_for(processes))
    assert status == 200 and data["trace_id"] is None and data["items"] == []
    assert data["correlation_status"] == "historical_correlation_unavailable"
    assert controller.conn.total_changes == before
    assert controller.conn.execute("SELECT COUNT(*) FROM reliability_traces").fetchone()[0] == 0


def test_missing_optional_r2_tables_leave_r1_reads_compatible(processes):
    from harness.dashboard.reliability_view import read_incidents

    operation, principal, controller = processes
    oid = store.begin(operation, principal, "read_file")
    store.finish(operation, oid, "DISPATCH_REJECTED")
    for table in ("reliability_timeline_links", "reliability_process_spans", "reliability_traces"):
        controller.conn.execute("DROP TABLE " + table)
    controller.conn.execute("DELETE FROM schema_migrations WHERE version=20")
    status, old = read_incidents(operation.database, query_for(processes))
    assert status == 200 and len(old["items"]) == 1
    status, new = read_timeline(operation.database, query_for(processes))
    assert status == 200 and new["trace_id"] is None
    assert not controller.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='reliability_traces'"
    ).fetchone()


@pytest.mark.parametrize(
    "kind",
    [
        "run.started",
        "run.ended",
        "task.updated",
        "output.invalidated",
        "test.result",
        "decision.acknowledged",
    ],
)
@pytest.mark.parametrize("extra_value", ["raw", "hash", "prefixed_hash"])
def test_semantic_privacy_real_http_never_exports_extra_payload_reference(
    observed, kind, extra_value
):
    from harness.execution_projection import ingest_event
    from tests.test_reliability_timeline import (
        SEMANTIC_PRIVATE_TEXT,
        semantic_privacy_event,
        semantic_privacy_extra,
    )

    raw_hash = hashlib.sha256(SEMANTIC_PRIVATE_TEXT.encode()).hexdigest()
    value = {"raw": SEMANTIC_PRIVATE_TEXT, "hash": raw_hash, "prefixed_hash": "sha256:" + raw_hash}[
        extra_value
    ]
    record, expected = semantic_privacy_event(observed, kind, semantic_privacy_extra(kind, value))
    operation, _, controller = observed
    assert ingest_event(controller.conn, operation.execution_id, record)
    with serve(operation.database, None) as server:
        status, _, body = fetch(server, "/api/execution/timeline?" + query_for(observed, limit=100))
    assert status == 200
    data = json.loads(body)
    item = next(item for item in data["items"] if item["source_id"] == record["event_id"])
    assert SEMANTIC_PRIVATE_TEXT not in body.decode() and raw_hash not in body.decode()
    assert item["reference_id"] == expected
    assert item["provenance"] == "validated_semantic_report"
