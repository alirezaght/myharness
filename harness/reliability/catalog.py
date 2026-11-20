"""Canonical closed incident facts. Insertion helpers never confer publication authority."""

import secrets
from contextlib import contextmanager

from harness.execution_store import get_execution
from harness.reliability.contracts import SYMPTOMS
from harness.reliability.query import scope


class IncidentConflict(ValueError):
    """Request reuse or optimistic revision conflict; reload before a new action."""


@contextmanager
def atomic_source(conn):
    """A source and catalog fact are indivisible inside the owner's transaction."""
    if not conn.in_transaction:
        raise ValueError("incident publication requires an owning transaction")
    conn.execute("SAVEPOINT incident_source")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO incident_source")
        raise
    finally:
        conn.execute("RELEASE incident_source")


def _insert(conn, kind, execution, epoch, category, code, provenance, created, incident=None):
    record = get_execution(conn, execution)
    selected = scope(conn, record["task_id"], execution, epoch)
    incident = incident or secrets.token_hex(16)
    conn.execute(
        "INSERT INTO reliability_incident_catalog VALUES (?,1,?,?,?,?,?,1,?,?,?)",
        (incident, kind, selected["task"], execution, epoch, category, created, code, provenance),
    )
    return incident


def project_operation(conn, incident_id):
    """Index one existing R1 fact unchanged; caller owns scope and transaction."""
    row = conn.execute(
        "SELECT i.*,o.execution_id,o.epoch_id,o.code FROM reliability_incidents i "
        "JOIN reliability_observations o USING(observation_id) WHERE i.incident_id=?",
        (incident_id,),
    ).fetchone()
    if row is None:
        raise LookupError("incident not found")
    with atomic_source(conn):
        prior = conn.execute(
            "SELECT c.* FROM incident_operation_sources s JOIN reliability_incident_catalog c "
            "USING(incident_id) WHERE s.legacy_incident_id=?",
            (incident_id,),
        ).fetchone()
        if prior:
            expected = (
                incident_id,
                row["execution_id"],
                row["epoch_id"],
                row["category"],
                row["code"],
                row["created_at"],
            )
            actual = tuple(
                prior[k]
                for k in (
                    "incident_id",
                    "execution_id",
                    "epoch_id",
                    "category",
                    "symptom_code",
                    "created_at",
                )
            )
            if actual != expected:
                raise IncidentConflict("conflicting legacy projection")
            return incident_id
        _insert(
            conn,
            "operation_dispatch",
            row["execution_id"],
            row["epoch_id"],
            row["category"],
            row["code"],
            "runtime_observed",
            row["created_at"],
            incident_id,
        )
        conn.execute(
            "INSERT INTO incident_operation_sources(incident_id,legacy_incident_id) VALUES (?,?)",
            (incident_id, incident_id),
        )
    return incident_id


def record_budget_decision(conn, decision_id):
    """Only an actual persisted exhaustion denial is eligible; no guessed measurement."""
    row = conn.execute("SELECT * FROM policy_decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None or row["verdict"] != "deny" or row["code"] != "BUDGET_EXHAUSTED":
        raise ValueError("budget exhaustion source unavailable")
    with atomic_source(conn):
        prior = conn.execute(
            "SELECT incident_id FROM incident_policy_sources WHERE policy_decision_id=?",
            (decision_id,),
        ).fetchone()
        if prior:
            return prior[0]
        incident = _insert(
            conn,
            "budget_decision",
            row["execution_id"],
            row["epoch_id"],
            "budget_exhaustion",
            "BUDGET_EXHAUSTED",
            "runtime_observed",
            row["created_at"],
        )
        conn.execute(
            "INSERT INTO incident_policy_sources(incident_id,policy_decision_id) VALUES (?,?)",
            (incident, decision_id),
        )
    return incident


CATALOG_SELECT = """
SELECT incident_id,schema_version,source_kind,task_id,execution_id,epoch_id,category,
 classification_version,created_at,symptom_code,provenance,'indexed' AS indexing
 FROM reliability_incident_catalog
UNION ALL
SELECT i.incident_id,i.schema_version,'operation_dispatch',o.task_id,o.execution_id,o.epoch_id,
 i.category,i.classification_version,i.created_at,o.code,o.provenance,'legacy_unindexed'
 FROM reliability_incidents i JOIN reliability_observations o USING(observation_id)
 WHERE NOT EXISTS(SELECT 1 FROM incident_operation_sources s
 WHERE s.legacy_incident_id=i.incident_id)
"""
SYMPTOM_LABELS = SYMPTOMS | {"BUDGET_EXHAUSTED": "Runtime budget exhausted"}


def detail(conn, selected, incident):
    where, values = scope_filter(conn, selected)
    row = conn.execute(
        "SELECT * FROM (" + CATALOG_SELECT + ") WHERE " + where + " AND incident_id=?",
        (*values, incident),
    ).fetchone()
    if row is None:
        raise LookupError("incident scope not found")
    return dict(row)


def scope_filter(conn, selected):
    if isinstance(selected, dict) and set(selected) == {"task"}:
        row = conn.execute("SELECT 1 FROM tasks WHERE id=?", (selected["task"],)).fetchone()
        if row is None:
            raise LookupError("incident scope not found")
        return "task_id=?", (selected["task"],)
    values = selected_values(selected)
    scope(conn, *values)
    return "task_id=? AND execution_id=? AND epoch_id=?", values


def selected_values(selected):
    if set(selected) != {"task", "execution", "epoch"}:
        raise ValueError("invalid incident scope")
    return tuple(selected[k] for k in ("task", "execution", "epoch"))


def list_incidents(conn, selected, *, limit=25, cursor=None, category=None, source_kind=None, q=""):
    where, values = scope_filter(conn, selected)
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(q, str) or len(q) > 200:
        raise ValueError("invalid incident query")
    args = list(values)
    for name, value in (("category", category), ("source_kind", source_kind)):
        if value is not None:
            if not isinstance(value, str) or len(value) > 80:
                raise ValueError("invalid incident filter")
            where += " AND " + name + "=?"
            args.append(value)
    if q:
        where += (
            " AND instr(lower(incident_id||' '||category||' '||symptom_code||' '||source_kind),?)>0"
        )
        args.append(q.lower())
    if cursor is not None:
        if (
            not isinstance(cursor, dict)
            or set(cursor) != {"created_at", "incident_id"}
            or any(not isinstance(v, str) or len(v) > 128 for v in cursor.values())
        ):
            raise ValueError("invalid incident cursor")
        where += " AND (created_at,incident_id)<(?,?)"
        args.extend((cursor["created_at"], cursor["incident_id"]))
    rows = conn.execute(
        "SELECT * FROM ("
        + CATALOG_SELECT
        + ") WHERE "
        + where
        + " ORDER BY created_at DESC,incident_id DESC LIMIT ?",
        (*args, limit + 1),
    ).fetchall()
    items = [dict(r) for r in rows[:limit]]
    return {
        "schema_version": 1,
        "scope": selected,
        "items": items,
        "next_cursor": {k: items[-1][k] for k in ("created_at", "incident_id")}
        if len(rows) > limit
        else None,
    }
