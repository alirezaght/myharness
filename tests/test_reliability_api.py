# ruff: noqa: F811
"""Real HTTP routes, literal filtering, scoping and read-only persistence."""

import json
from urllib.parse import urlencode

import pytest

from harness.dashboard.reliability_view import read_incidents
from harness.reliability import store
from tests.test_dashboard import fetch, serve
from tests.test_reliability_store import observed, rows  # noqa: F401


def query_for(observed, **extra):
    operation, _, controller = observed
    tid = controller.conn.execute(
        "SELECT task_id FROM executions WHERE id=?", (operation.execution_id,)
    ).fetchone()[0]
    return urlencode(
        {"task": tid, "execution": operation.execution_id, "epoch": operation.epoch_id, **extra}
    )


def seed(observed, count=3):
    operation, principal, _ = observed
    for _ in range(count):
        oid = store.begin(operation, principal, "read_file")
        store.finish(operation, oid, "DISPATCH_REJECTED")
    store.begin(operation, principal, "read_file")


def test_http_list_detail_literal_search_pagination_and_no_writes(observed):
    operation, _, controller = observed
    seed(observed)
    before = controller.conn.total_changes
    with serve(operation.database, None) as server:
        status, _, body = fetch(server, "/api/execution/incidents?" + query_for(observed, limit=2))
        data = json.loads(body)
        assert status == 200 and len(data["items"]) == 2 and len(data["pending"]) == 1
        assert not data["coverage"]["async_completion_classification"]
        assert data["pending"][0]["symptom"] == "Outcome not recorded"
        cursor = data["next_cursor"]
        status, _, body = fetch(
            server, "/api/execution/incidents?" + query_for(observed, limit=2, **cursor)
        )
        older = json.loads(body)
        assert status == 200 and len(older["items"]) == 1 and older["next_cursor"] is None
        assert {i["incident_id"] for i in data["items"]}.isdisjoint(
            i["incident_id"] for i in older["items"]
        )
        incident = data["items"][0]["incident_id"]
        status, _, body = fetch(
            server, "/api/execution/incidents/detail?" + query_for(observed, incident=incident)
        )
        assert status == 200 and json.loads(body)["item"]["root_cause"] == "not_assessed"
        for term in ["%", "_%", "' OR 1=1 --", "secret-canary"]:
            status, _, body = fetch(
                server, "/api/execution/incidents?" + query_for(observed, q=term)
            )
            assert status == 200 and json.loads(body)["items"] == []
        status, _, body = fetch(
            server,
            "/api/execution/incidents?" + query_for(observed, q="Operation dispatch was rejected"),
        )
        assert status == 200 and len(json.loads(body)["items"]) == 3
    assert controller.conn.total_changes == before
    assert len(rows(observed, "reliability_incidents")) == 3


@pytest.mark.parametrize(
    "extra",
    [
        "&task=duplicate",
        "&surprise=x",
        "&limit=0",
        "&limit=101",
        "&limit=1.0",
        "&before_id=123",
        "&before_time=bad",
        "&category=policy_violation",
        "&q=" + "a" * 129,
    ],
)
def test_bad_queries_refuse(observed, extra):
    assert read_incidents(observed[0].database, query_for(observed) + extra)[0] == 400


def test_scope_refuses_full_tuple_and_missing_storage_is_fixed(observed, tmp_path):
    seed(observed, 1)
    operation, _, _ = observed
    incident = rows(observed, "reliability_incidents")[0]["incident_id"]
    for extra in [{"task": "foreign"}, {"execution": "foreign"}, {"epoch": "foreign"}]:
        status, body = read_incidents(
            operation.database, query_for(observed, incident=incident, **extra), detail=True
        )
        assert (status, body) == (404, {"error": "Reliability scope not found"})
    missing = tmp_path / "missing.db"
    assert read_incidents(missing, query_for(observed)) == (
        503,
        {"error": "Reliability storage unavailable"},
    )
    assert not missing.exists()


def record_private_dispatches(observed):
    import hashlib
    from pathlib import Path

    operation, principal, _ = observed
    arguments, result, exception = (
        "private-argument-r1-9321",
        "private-result-r1-7362",
        "private-exception-r1-8214",
    )
    with pytest.raises(ValueError, match="unknown operation"):
        operation.dispatch(principal, "unknown", {"private_argument": arguments})
    (Path(operation.envelope["repository"]) / "private-result.txt").write_text(result)
    returned = operation.dispatch(principal, "read_file", {"path": "private-result.txt"})
    assert result in returned["text"]
    with pytest.raises(ValueError, match=exception):
        operation.dispatch(principal, "read_file", {"path": exception})
    terms = [arguments, result, exception]
    return terms + [hashlib.sha256(term.encode()).hexdigest() for term in terms]


def test_real_dispatch_private_arguments_results_exceptions_absent_from_scoped_http(observed):
    terms = record_private_dispatches(observed)
    with serve(observed[0].database, None) as server:
        status, _, body = fetch(
            server, "/api/execution/incidents?" + query_for(observed, limit=100)
        )
        assert status == 200
        data = json.loads(body)
        assert len(data["items"]) == 2
        for item in data["items"]:
            status, _, detail = fetch(
                server,
                "/api/execution/incidents/detail?"
                + query_for(observed, incident=item["incident_id"]),
            )
            assert status == 200
            assert all(term not in detail.decode() for term in terms)
        assert all(term not in body.decode() for term in terms)
