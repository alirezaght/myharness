"""Workspace source projection and the guarded HTTP proposal seam."""

import json

from harness.dashboard.view import dashboard_view
from tests.test_dashboard import serve, start_task
from tests.test_dashboard_comments import post
from tests.test_dashboard_diagrams import _sample

PATH = "/api/workspace/proposals"


def test_empty_sources_are_honest_and_base_ignores_activity(db_path):
    task = start_task(db_path, "Understand this requirement", "interview")
    workspace = dashboard_view(db_path, task, None)["task"]["workspace"]
    assert workspace["source"]["requirements"][0]["text"] == "Understand this requirement interview"
    assert workspace["source"]["plan"] == []
    assert workspace["source"]["spec"] is None
    assert workspace["source"]["requirements"][0]["links"] == []
    assert dashboard_view(db_path, task, None)["task"]["workspace"]["base"] == workspace["base"]


def test_http_create_replay_validation_and_security(db_path):
    task = _sample(db_path)
    base = dashboard_view(db_path, task, None)["task"]["workspace"]["base"]
    payload = {
        "task": task,
        "base": base,
        "request_id": "request-1234",
        "operations": [{"type": "requirement", "target": "request", "text": "Clarify"}],
    }
    with serve(db_path, task) as server:
        assert post(server, payload, path=PATH)[0] == 201
        assert post(server, payload, path=PATH)[0] == 200
        assert post(server, payload | {"base": "stale"}, path=PATH)[0] == 409
        assert (
            post(server, payload, path=PATH, headers={"Origin": "https://foreign.example"})[0]
            == 403
        )
        assert post(server, b'{"task":"x","task":"y"}', path=PATH)[0] == 400
        assert post(server, payload | {"extra": True}, path=PATH)[0] == 400
        assert (
            post(
                server,
                payload
                | {
                    "request_id": "request-5678",
                    "operations": [
                        {
                            "type": "node_position",
                            "target": "component:browser",
                            "x": float("inf"),
                            "y": 0,
                        }
                    ],
                },
                path=PATH,
            )[0]
            == 400
        )
        assert post(server, b"{}", path=PATH, headers={"Content-Length": "131073"})[0] == 413
        assert post(server, payload | {"task": "foreign"}, path=PATH)[0] == 404


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.db"
    with serve(path, "missing") as server:
        status, _, body = post(
            server,
            {"task": "missing", "base": "a", "operations": [], "request_id": "request-1234"},
            path=PATH,
        )
    assert status == 404 and "error" in json.loads(body)
    assert not path.exists()


def test_workspace_javascript_syntax_and_truthful_task_groups():
    import shutil
    import subprocess

    import pytest

    from harness.dashboard.server import STATIC_DIR

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable; browser verification must establish script loading")
    for filename in ("workspace.js", "workspace_plan.js", "workspace_diagram.js", "app.js"):
        subprocess.run(
            [node, "--check", str(STATIC_DIR / filename)], check=True, capture_output=True
        )
    script = (STATIC_DIR / "workspace.js").read_text()
    cases = [
        {"status": "Running", "guidance": {"status": "Work in progress"}},
        {"status": "RecoveryRequired", "guidance": {"status": "Work stopped · action needed"}},
        {"status": "Completed", "guidance": {"status": "Checks incomplete"}},
        {"status": "Completed", "guidance": {"status": "Verified complete"}},
        {"status": "in progress", "guidance": {"status": "Specification in progress"}},
    ]
    script = "globalThis.window = { addEventListener() {} };\n" + script
    script += "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(workspaceTaskGroup)));"
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [
        "Running",
        "Needs you",
        "Needs you",
        "Finished",
        "Ready / pending",
    ]
