"""P4-06: the delivery panel is a read-only projection in the dashboard."""

import http.client
import json

from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution
from tests.test_dashboard import api, serve


def _delivery_row(conn, execution_id, provider="gitlab"):
    execution = get_execution(conn, execution_id)
    mr = {
        "iid": 14,
        "state": "opened",
        "web_url": "https://gitlab.example.test/group/project/-/merge_requests/14",
    }
    snapshot = {
        "pipeline": {
            "id": 5801,
            "status": "success",
            "web_url": "https://gitlab.example.test/group/project/-/pipelines/5801",
        },
        "jobs": [{"id": 82001, "name": "unit-tests", "status": "success"}],
        "test_report": {
            "total_count": 12,
            "success_count": 11,
            "failed_count": 1,
            "skipped_count": 0,
        },
        "staging": {"ready": True, "status": "success"},
        "blockers": ["one approval remains"],
        "observed_at": "2026-10-26T09:30:00Z",
    }
    if provider == "github":
        mr["web_url"] = "https://github.example.test/group/project/pull/14"
        snapshot["pipeline"]["web_url"] = "https://github.example.test/group/project/actions"
        snapshot["test_report"] = {
            "format": "github_check_summaries",
            "counts_available": False,
            "checks": [{"name": "tests", "conclusion": "success", "summary": "12 tests passed"}],
        }
    conn.execute(
        "INSERT INTO deliveries(execution_id,binding,config,generation,status,candidate_sha,"
        "mr,snapshot,retries,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            execution_id,
            json.dumps(execution["binding"], sort_keys=True),
            json.dumps({"retry_limit": 3, "provider": provider}, sort_keys=True),
            0,
            "Published",
            "0123456789abcdef",
            json.dumps(mr, sort_keys=True),
            json.dumps(snapshot, sort_keys=True),
            1,
            "2026-10-26T09:30:00Z",
        ),
    )
    conn.commit()


def test_dashboard_api_nests_delivery_projection_without_changing_delivery_state(
    controller, sample_repo, monkeypatch, db_path
):
    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    _delivery_row(controller.conn, execution_id)
    before = controller.conn.execute(
        "SELECT * FROM deliveries WHERE execution_id=?", (execution_id,)
    ).fetchone()
    operations_before = controller.conn.execute(
        "SELECT COUNT(*) FROM delivery_operations WHERE execution_id=?", (execution_id,)
    ).fetchone()[0]

    with serve(db_path, task_id) as server:
        result = api(server, f"?task={task_id}")
        status, headers, body = _fetch(server, "/static/delivery.js")

    delivery = result["task"]["execution"]["delivery"]
    assert delivery["status"] == "Published"
    assert delivery["mr"]["iid"] == 14
    assert delivery["pipeline"]["status"] == "success"
    assert delivery["jobs"][0]["status"] == "success"
    assert delivery["test_report"]["failed_count"] == 1
    assert delivery["staging"]["ready"] is True
    assert delivery["browser_validation"]["status"] == "not_run"
    assert delivery["browser_validation"]["currency"] == "unavailable"
    assert delivery["browser_validation"]["plan"] is None
    assert delivery["completion_summary"]["live_acceptance"] == "outstanding"
    assert delivery["retries"] == {"used": 1, "limit": 3}
    assert delivery["blockers"] == ["one approval remains"]
    assert status == 200 and "text/javascript" in headers["Content-Type"]
    assert b"safeDeliveryUrl" in body and b"textContent" in body
    assert b"deliveryDisclosure" in body and b"withDeliveryFocus" in body
    assert b"Browser validation" in body and b"Investigation records" in body
    after = controller.conn.execute(
        "SELECT * FROM deliveries WHERE execution_id=?", (execution_id,)
    ).fetchone()
    operations_after = controller.conn.execute(
        "SELECT COUNT(*) FROM delivery_operations WHERE execution_id=?", (execution_id,)
    ).fetchone()[0]
    assert tuple(before) == tuple(after)
    assert operations_after == operations_before


def test_dashboard_without_configured_delivery_has_no_empty_delivery_panel(
    controller, sample_repo, monkeypatch, db_path
):
    task_id, _ = approved_execution(controller, sample_repo, monkeypatch)
    with serve(db_path, task_id) as server:
        result = api(server, f"?task={task_id}")
    assert "delivery" not in result["task"]["execution"]


def test_delivery_stream_is_read_only(controller, sample_repo, monkeypatch, db_path):
    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    _delivery_row(controller.conn, execution_id)
    before = tuple(
        controller.conn.execute(
            "SELECT * FROM deliveries WHERE execution_id=?", (execution_id,)
        ).fetchone()
    )
    with serve(db_path, task_id) as server:
        server.stream_lifetime = 0.05
        client = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        client.request("GET", f"/api/stream?task={task_id}")
        response = client.getresponse()
        payload = response.read()
        client.close()
    assert response.status == 200
    frames = [line[6:] for line in payload.splitlines() if line.startswith(b"data: ")]
    assert frames and json.loads(frames[0])["task"]["execution"]["delivery"]["mr"]["iid"] == 14
    after = tuple(
        controller.conn.execute(
            "SELECT * FROM deliveries WHERE execution_id=?", (execution_id,)
        ).fetchone()
    )
    assert before == after


def test_browser_read_model_does_not_claim_acceptance_or_add_write_routes(
    controller, sample_repo, monkeypatch, db_path
):
    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    _delivery_row(controller.conn, execution_id)
    before = controller.conn.execute(
        "SELECT COUNT(*) FROM browser_runs WHERE execution_id=?", (execution_id,)
    ).fetchone()[0]
    with serve(db_path, task_id) as server:
        result = api(server, f"?task={task_id}")
        status, headers, body = _fetch(server, "/static/delivery.js")
    delivery = result["task"]["execution"]["delivery"]
    assert delivery["browser_validation"]["status"] == "not_run"
    assert delivery["browser_validation"]["currency"] == "unavailable"
    assert delivery["completion_summary"]["live_acceptance"] == "outstanding"
    assert "POST" not in body.decode()
    assert status == 200 and "text/javascript" in headers["Content-Type"]
    after = controller.conn.execute(
        "SELECT COUNT(*) FROM browser_runs WHERE execution_id=?", (execution_id,)
    ).fetchone()[0]
    assert before == after == 0


def _fetch(server, path):
    client = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        client.request("GET", path)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()
