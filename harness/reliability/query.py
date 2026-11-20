"""Scoped projections from closed facts only; no audit/event body imports."""

from harness.execution_epochs import get_epoch
from harness.execution_store import get_execution
from harness.reliability.contracts import COVERAGE, SYMPTOMS


def scope(conn, task, execution, epoch):
    record = get_execution(conn, execution)
    if record["task_id"] != task:
        raise LookupError("reliability scope not found")
    # Legacy is valid only when it is actually recorded or still the selected legacy epoch.
    if epoch != "legacy-0" or record["epoch_id"] != epoch:
        get_epoch(conn, execution, epoch)
    return {"task": task, "execution": execution, "epoch": epoch}


def project(row):
    item = dict(row)
    item["symptom"] = SYMPTOMS[item["code"]]
    item["root_cause"] = "not_assessed"
    item["evidence"] = {
        key: item[key]
        for key in (
            "observation_id",
            "source_id",
            "execution_id",
            "epoch_id",
        )
    }
    return item


SELECT = (
    "SELECT i.*,o.source_id,o.execution_id,o.task_id,o.epoch_id,"
    "o.authority_generation,o.principal_id,o.role,o.tool,o.policy_digest,"
    "o.spec_version,o.approval_id,o.started_at,o.finished_at,o.state,o.code,o.provenance "
    "FROM reliability_incidents i JOIN reliability_observations o "
    "ON o.observation_id=i.observation_id "
)


def list_incidents(conn, selected, *, category=None, q="", limit=25, cursor=None):
    where = " WHERE o.task_id=? AND o.execution_id=? AND o.epoch_id=?"
    args = [selected["task"], selected["execution"], selected["epoch"]]
    if category:
        where += " AND i.category=?"
        args.append(category)
    if q:
        # instr performs literal, parameterized substring search, including % and _.
        labels = (
            "CASE o.code "
            + " ".join("WHEN '" + key + "' THEN '" + value + "'" for key, value in SYMPTOMS.items())
            + " END"
        )
        where += (
            " AND instr(lower(i.incident_id||' '||o.observation_id||' '||o.source_id||' '||"
            "o.execution_id||' '||o.tool||' '||o.code||' '||i.category||' '||" + labels + "),?)>0"
        )
        args.append(q.lower())
    if cursor:
        where += " AND (i.created_at,i.incident_id)<(?,?)"
        args.extend(cursor)
    rows = conn.execute(
        SELECT + where + " ORDER BY i.created_at DESC,i.incident_id DESC LIMIT ?",
        (*args, limit + 1),
    ).fetchall()
    items = [project(row) for row in rows[:limit]]
    next_cursor = (
        {"before_time": items[-1]["created_at"], "before_id": items[-1]["incident_id"]}
        if len(rows) > limit
        else None
    )
    pending = [
        dict(row) | {"symptom": "Outcome not recorded"}
        for row in conn.execute(
            "SELECT * FROM reliability_observations WHERE task_id=? AND execution_id=? AND "
            "epoch_id=? AND state='pending' ORDER BY started_at DESC,observation_id DESC LIMIT 26",
            (selected["task"], selected["execution"], selected["epoch"]),
        )
    ]
    return {
        "schema_version": 1,
        "scope": selected,
        "items": items,
        "next_cursor": next_cursor,
        "pending": pending[:25],
        "pending_truncated": len(pending) > 25,
        "coverage": dict(COVERAGE),
    }


def detail(conn, selected, incident):
    row = conn.execute(
        SELECT + " WHERE o.task_id=? AND o.execution_id=? AND o.epoch_id=? AND i.incident_id=?",
        (selected["task"], selected["execution"], selected["epoch"], incident),
    ).fetchone()
    if row is None:
        raise LookupError("reliability scope not found")
    return {
        "schema_version": 1,
        "scope": selected,
        "item": project(row),
        "coverage": dict(COVERAGE),
    }
