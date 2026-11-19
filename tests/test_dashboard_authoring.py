import json

from harness.dashboard.authoring_actions import authoring_action
from harness.dashboard.authoring_view import read_authoring
from tests.test_dashboard import serve
from tests.test_dashboard_comments import post


def test_read_only_snapshot_and_http_guards(db_path, tmp_path):
    assert not db_path.exists()
    assert read_authoring(db_path, tmp_path)["task"] is None
    assert not db_path.exists()
    with serve(db_path, None) as server:
        assert (
            post(
                server, {}, path="/api/authoring/new", headers={"Origin": "https://foreign.example"}
            )[0]
            == 403
        )
        assert (
            post(server, b"{}", path="/api/authoring/new", headers={"Content-Length": "131073"})[0]
            == 413
        )
        assert (
            post(
                server,
                {
                    "request_id": "startup-1234",
                    "request": "Build scheduler",
                    "host": "codex",
                    "repo": "/tmp/foreign",
                },
                path="/api/authoring/new",
            )[0]
            == 400
        )
    assert not db_path.exists()


def test_action_exact_schema_and_replay(controller, db_path, tmp_path, monkeypatch):
    monkeypatch.setattr("harness.authoring_policy.availability", lambda host: {"available": False})
    body = json.dumps(
        {"request_id": "startup-1234", "request": "Build a scheduler", "host": "codex"}
    ).encode()
    status, first = authoring_action(db_path, tmp_path, body, "new")
    assert status == 202
    assert authoring_action(db_path, tmp_path, body, "new")[1] == first
    snapshot = read_authoring(db_path, tmp_path, first["task"])
    request = {
        "task": first["task"],
        "expected": snapshot["base"],
        "request_id": "save-12345",
        "body": "## Exact\n",
    }
    assert authoring_action(db_path, tmp_path, json.dumps(request).encode(), "save-spec")[0] == 200
    request["request_id"] = "save-67890"
    status, error = authoring_action(db_path, tmp_path, json.dumps(request).encode(), "save-spec")
    assert status == 409 and error["base"]


def test_real_http_edit_replay_conflict_and_history(db_path, tmp_path, monkeypatch):
    import threading

    from harness.authoring import AuthoringService
    from harness.controller import HarnessController
    from harness.dashboard.server import DashboardServer
    from harness.db import connect
    from tests.test_dashboard import fetch

    connection = connect(db_path)
    controller = HarnessController(connection)
    task = controller.create_task("HTTP authoring task")
    service = AuthoringService(controller, tmp_path, task.id)
    initial = service.start(
        expected=service.store.base(), request_id="resume-http-1234", host="codex"
    )
    service.cancel_job(
        expected=service.store.base(), request_id="cancel-http-1234", job=initial["job"]
    )
    connection.close()
    server = DashboardServer(db_path, task.id, project_root=tmp_path)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        snapshot = json.loads(fetch(server, f"/api/authoring?task={task.id}")[2])
        request = {
            "task": task.id,
            "expected": snapshot["base"],
            "request_id": "http-save-1234",
            "body": "## HTTP exact text\n",
        }
        first = post(server, request, path="/api/authoring/save-spec")
        assert first[0] == 200
        assert post(server, request, path="/api/authoring/save-spec")[2] == first[2]
        assert (
            post(
                server, request | {"request_id": "http-save-5678"}, path="/api/authoring/save-spec"
            )[0]
            == 409
        )
        saved = json.loads(fetch(server, f"/api/authoring?task={task.id}")[2])
        assert saved["working"]["body"] == request["body"]
        assert (
            fetch(
                server, f"/api/authoring/revision?task=foreign&revision={saved['working']['id']}"
            )[0]
            == 404
        )
        assert post(server, request | {"extra": True}, path="/api/authoring/save-spec")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_malformed_scalar_ids_and_base_are_refused_before_sql_binding(
    controller, db_path, tmp_path
):
    from tests.test_authoring import initialized

    service = initialized(controller, tmp_path)
    common = {
        "task": service.task_id,
        "expected": service.store.base(),
        "request_id": "malformed-1234",
    }
    cases = [
        ("reconcile-preview", {"task": service.task_id, "revision": []}),
        ("cancel-job", common | {"job": []}),
        ("revise", common | {"target": {}, "target_revision": "fixture", "body": "text"}),
        (
            "answer",
            common
            | {"question": True, "mode": "answer", "answer": "value", "source": None, "ref": None},
        ),
        (
            "answer",
            common
            | {"question": 1, "mode": "defer", "answer": "", "source": "codebase", "ref": []},
        ),
        (
            "save-spec",
            common | {"expected": common["expected"] | {"generation": True}, "body": "## Draft"},
        ),
    ]
    for action, payload in cases:
        assert authoring_action(db_path, tmp_path, json.dumps(payload).encode(), action)[0] == 400


def test_rework1_http_legacy_response_target(controller, db_path, tmp_path):
    from tests.test_authoring import legacy_records

    service, question, *_ = legacy_records(controller, tmp_path)
    snapshot = read_authoring(db_path, tmp_path, service.task_id)
    targets = snapshot.get("revision_targets", [])
    assert targets, "HTTP projection must expose legacy revision targets"
    target = next(row for row in targets if row["target"] == f"question-response:{question.id}")
    payload = {
        "task": service.task_id,
        "request_id": "legacy-http-1234",
        "expected": snapshot["base"],
        "target": target["target"],
        "target_revision": target["revision"],
        "value": {"mode": "unknown", "answer": "", "source": None, "ref": None},
    }
    assert (
        authoring_action(db_path, tmp_path, json.dumps(payload).encode(), "revise-record")[0] == 200
    )


def test_rework1_generic_revision_of_typed_head_is_refused_atomically(
    controller, db_path, tmp_path
):
    import pytest

    from tests.test_authoring import legacy_records

    service, question, _, _, proposal = legacy_records(controller, tmp_path)
    for ident, value in [
        (
            f"question-response:{question.id}",
            {"mode": "answer", "answer": "New typed answer", "source": None, "ref": None},
        ),
        (f"recommendation:{proposal.id}", {"status": "modified", "response": "New typed decision"}),
    ]:
        target = next(
            row for row in service.snapshot()["revision_targets"] if row["target"] == ident
        )
        typed = service.revise_record(
            expected=service.store.base(),
            request_id="typed-head-" + str(len(ident)),
            target=ident,
            target_revision=target["revision"],
            value=value,
        )
        before = service.snapshot()
        with pytest.raises(ValueError, match="revise-record"):
            service.revise(
                expected=before["base"],
                request_id="invalid-generic-1234",
                target=typed["contribution"],
                target_revision=typed["contribution"],
                body="Unstructured revision",
            )
        payload = {
            "task": service.task_id,
            "expected": before["base"],
            "request_id": "invalid-http-1234",
            "target": typed["contribution"],
            "target_revision": typed["contribution"],
            "body": "Unstructured revision",
        }
        assert authoring_action(db_path, tmp_path, json.dumps(payload).encode(), "revise")[0] == 409
        assert service.snapshot() == before


def test_rework1b_schema17_dashboard_upgrade_through_http(tmp_path, monkeypatch):
    import threading

    from harness import db
    from harness.controller import HarnessController
    from harness.dashboard.server import DashboardServer
    from tests.test_dashboard import api

    original = db._migrations
    path = tmp_path / "schema17-http.db"
    with monkeypatch.context() as patch:
        patch.setattr(db, "_migrations", lambda: [m for m in original() if m[0] <= 17])
        connection = db.connect(path)
        task = HarnessController(connection).create_task("Retained old HTTP task")
        connection.close()
    server = DashboardServer.__new__(DashboardServer)
    thread = None
    try:
        server.__init__(path, task.id, project_root=tmp_path)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        result = api(server)
        assert result["task"]["request"] == "Retained old HTTP task"
        assert result["authoring"]["initialized"] is False
        assert server.authoring_worker.thread is None
    finally:
        if thread:
            server.shutdown()
        if hasattr(server, "socket"):
            server.server_close()
        if thread:
            thread.join(timeout=10)
    connection = db.connect(path)
    try:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 18
        assert connection.execute("SELECT count(*) FROM authoring_tasks").fetchone()[0] == 0
    finally:
        connection.close()
    assert not path.with_suffix(".db.authoring.lock").exists()
