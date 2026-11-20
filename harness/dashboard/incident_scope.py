"""Configured repository and actual task/epoch ownership for local reliability inspection."""

from pathlib import Path

from harness.execution_handoff import read_handoff, same_path
from harness.execution_store import get_execution
from harness.reliability import catalog


def repository_scope(conn, selected, root, *, execution=None):
    catalog.scope_filter(conn, selected)
    eid = execution or selected.get("execution")
    if eid is None:
        row = conn.execute(
            "SELECT id FROM executions WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (selected["task"],),
        ).fetchone()
        if row is None:
            raise LookupError("incident scope not found")
        eid = row[0]
    record = get_execution(conn, eid)
    if record["task_id"] != selected["task"]:
        raise LookupError("incident scope not found")
    envelope = read_handoff(record["handoff_path"], record["handoff_hash"])
    if not same_path(envelope["repository"], str(Path(root).resolve())):
        raise LookupError("incident scope not found")


def selected_scope(value):
    if not isinstance(value, dict) or set(value) not in ({"task"}, {"task", "execution", "epoch"}):
        raise ValueError("invalid incident scope")
    from harness.reliability.investigation_contracts import identity

    for part in value.values():
        identity(part)
    return value
