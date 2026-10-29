"""Read-only, epoch-scoped activity metadata; never exposes tool arguments or output."""

import sqlite3
from datetime import UTC, datetime

_ACTIONS = {
    "read_file": "Reading source",
    "write_artifact": "Preparing a report",
    "apply_patch": "Applying source changes",
    "run_command": "Running a command",
    "al_command": "Updating AL work or checks",
    "poll_process": "Checking a command's progress",
    "cancel_process": "Stopping an owned command",
    "submit_output": "Recording an output report",
    "diagnostic": "Recording a diagnostic",
}


def timestamp(value):
    try:
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, UTC).isoformat()
        return datetime.fromisoformat(value).astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def activity_view(conn, execution, agents, reports, feed):
    epoch = conn.execute(
        "SELECT created_at FROM execution_epochs WHERE execution_id=? AND epoch_id=?",
        (execution["id"], execution["epoch_id"]),
    ).fetchone()
    current = {agent["id"]: agent for agent in agents}
    recent = []
    if epoch and current:
        # Extract only allowlisted metadata in SQL, not source text, argv, stdout,
        # tokens or arbitrary request/result bodies. Unknown old principals cannot
        # supply current-epoch activity even though their native parent is reused.
        try:
            rows = conn.execute(
                "SELECT created_at, "
                "substr(json_extract(request,'$.principal'),1,128) AS principal, "
                "substr(json_extract(request,'$.tool'),1,80) AS tool, "
                "json_extract(outcome,'$.error') IS NOT NULL AS failed "
                "FROM operation_audit WHERE execution_id=? AND created_at>=? "
                "AND json_valid(request) AND (outcome IS NULL OR json_valid(outcome)) "
                "ORDER BY rowid DESC LIMIT 50",
                (execution["id"], epoch["created_at"]),
            ).fetchall()
        except sqlite3.OperationalError:
            # Optional observations must not break status on SQLite builds
            # without JSON functions; semantic events remain available below.
            rows = []
        for row in rows:
            if row["principal"] not in current or row["tool"] not in _ACTIONS:
                continue
            at = timestamp(row["created_at"])
            if not at:
                continue
            item = {
                "agent_id": row["principal"],
                "agent_name": current[row["principal"]]["name"],
                "action": _ACTIONS[row["tool"]],
                "at": at,
                "outcome": "Tool request failed" if row["failed"] else None,
            }
            recent.append(item)
            current[row["principal"]].setdefault("last_operation", item)
            if len(recent) == 5:
                break
    checks = {}
    last_event_at = None
    for event in feed:
        at = timestamp(event.get("timestamp"))
        if at:
            last_event_at = at
        payload = event.get("payload", {})
        if event["kind"] in {"test.started", "test.result"}:
            checks[payload["id"]] = {
                "id": payload["id"],
                "area": payload.get("area"),
                "status": payload.get("status"),
                "counts": payload.get("counts", {}),
                "at": at,
            }
    return {
        "recent_actions": recent,
        "last_activity_at": max(
            [row["at"] for row in recent] + ([last_event_at] if last_event_at else []),
            default=None,
        ),
        "checks": list(checks.values()),
        "report_count": len(reports),
        "finished_agent_runs": sum(agent.get("status") == "Completed" for agent in agents),
    }
