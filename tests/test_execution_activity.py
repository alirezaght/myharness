"""Current work observations never leak old epochs, commands, source, or credentials."""

import json
import sqlite3

from harness.execution_activity import activity_view


def observed():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE execution_epochs(execution_id,epoch_id,created_at);"
        "CREATE TABLE operation_audit(execution_id,request,outcome,created_at);"
        "INSERT INTO execution_epochs VALUES ('run','new','2026-10-29T12:00:00+00:00');"
    )
    return conn


def action(conn, principal, tool, at, *, failed=False):
    conn.execute(
        "INSERT INTO operation_audit VALUES (?,?,?,?)",
        (
            "run",
            json.dumps(
                {"principal": principal, "tool": tool, "arguments": {"secret": "NEVER_EXPORT"}}
            ),
            json.dumps({"error": "PRIVATE_ERROR"} if failed else {"output": "PRIVATE_SOURCE"}),
            at,
        ),
    )


def test_activity_is_current_epoch_known_principal_metadata_only_and_observational():
    conn = observed()
    action(conn, "current", "write_file", "2026-10-29T11:59:59+00:00")
    action(conn, "old", "write_file", "2026-10-29T12:01:00+00:00")
    action(conn, "current", "read_file", "2026-10-29T12:02:00+00:00", failed=True)
    action(conn, "current", "unknown_tool", "2026-10-29T12:03:00+00:00")
    before = conn.total_changes
    agents = [{"id": "current", "name": "Engineer", "status": "Running"}]
    view = activity_view(conn, {"id": "run", "epoch_id": "new"}, agents, [], [])
    assert conn.total_changes == before
    assert len(view["recent_actions"]) == 1
    assert view["recent_actions"][0] == {
        "agent_id": "current",
        "agent_name": "Engineer",
        "action": "Reading source",
        "at": "2026-10-29T12:02:00+00:00",
        "outcome": "Tool request failed",
    }
    encoded = json.dumps(view)
    assert not any(s in encoded for s in ("NEVER_EXPORT", "PRIVATE_ERROR", "PRIVATE_SOURCE"))
    assert view["finished_agent_runs"] == 0
    assert view["last_activity_at"] == "2026-10-29T12:02:00+00:00"


def test_checks_use_last_reported_result_without_inventing_completion():
    conn = observed()
    events = [
        {"kind": "test.started", "timestamp": 100, "payload": {"id": "E1", "status": "RUNNING"}},
        {"kind": "test.result", "timestamp": 101, "payload": {"id": "E1", "status": "FAIL"}},
        {"kind": "test.started", "timestamp": 102, "payload": {"id": "E2", "status": "RUNNING"}},
    ]
    view = activity_view(
        conn,
        {"id": "run", "epoch_id": "new"},
        [{"id": "a", "name": "Architect", "status": "Completed"}],
        [{"name": "plan"}],
        events,
    )
    assert [check["status"] for check in view["checks"]] == ["FAIL", "RUNNING"]
    assert view["finished_agent_runs"] == 1 and view["report_count"] == 1
    assert "completed" not in view
    assert view["recent_actions"] == []
