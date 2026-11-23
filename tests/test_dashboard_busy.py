"""Issue #2: a dashboard read that meets another command's write lock is a retryable wait."""

import json

from harness.dashboard.server import DashboardHandler
from tests.test_dashboard import fetch, serve
from tests.test_state_db_busy import _locked_error
from tests.test_state_db_busy import task_id as task_id  # noqa: F401  (fixture)


def test_dashboard_view_reports_busy_db_as_retryable_wait(db_path, task_id, monkeypatch):
    error = _locked_error(db_path)

    def busy_view(self, task):
        raise error

    monkeypatch.setattr("harness.dashboard.server.DashboardHandler._view", busy_view)
    with serve(db_path, task_id) as server:
        status, _, body = fetch(server, "/api/dashboard")
        handler = object.__new__(DashboardHandler)
        handler.server = server
        stream_frame = handler._frame(task_id).decode()
    data = json.loads(body)
    assert status == 503
    assert data["busy"] is True and data["retry"] is True
    assert "busy" in data["error"]
    assert "event: failure" in stream_frame
    failure = json.loads(stream_frame.split("data: ", 1)[1])
    assert failure["busy"] is True
    assert "busy" in failure["error"]
