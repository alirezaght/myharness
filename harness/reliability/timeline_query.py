"""Bounded, read-only correlation from closed facts; no audit or semantic payload reads."""

from harness.reliability.timeline_contracts import COVERAGE, MEASUREMENTS

FIELDS = (
    "item_id",
    "at",
    "event_kind",
    "provenance",
    "source_kind",
    "source_id",
    "principal_id",
    "role",
    "parent_observation_id",
    "parent_span_id",
    "observation_id",
    "span_id",
    "incident_id",
    "duration_ms",
    "returncode",
    "stop_reason",
    "cleanup_status",
    "reference_id",
    "bundle_hash",
    "snapshot_id",
    "receipt_id",
    "tool",
    "code",
)


def _select(values, tail):
    return "SELECT " + ",".join(values.get(key, "NULL") + " AS " + key for key in FIELDS) + tail


def _source_sql():
    base = {
        "provenance": "o.provenance",
        "source_kind": "'operation'",
        "source_id": "o.source_id",
        "principal_id": "o.principal_id",
        "role": "o.role",
        "observation_id": "o.observation_id",
        "incident_id": "i.incident_id",
        "tool": "o.tool",
        "code": "o.code",
    }
    tail = (
        " FROM reliability_observations o LEFT JOIN reliability_incidents i "
        "ON i.observation_id=o.observation_id WHERE o.execution_id=:execution "
        "AND o.epoch_id=:epoch"
    )
    start = _select(
        base
        | {
            "item_id": "o.observation_id||':start'",
            "at": "o.started_at",
            "event_kind": "'operation.started'",
            "code": "NULL",
        },
        tail,
    )
    end = _select(
        base
        | {
            "item_id": "o.observation_id||':end'",
            "at": "o.finished_at",
            "event_kind": "CASE o.state WHEN 'succeeded' THEN 'operation.returned' "
            "WHEN 'denied' THEN 'operation.denied' ELSE 'operation.failed' END",
        },
        tail + " AND o.finished_at IS NOT NULL",
    )
    links = _select(
        {
            "item_id": "l.link_id",
            "at": "l.created_at",
            "event_kind": "l.event_kind",
            "provenance": "l.provenance",
            "source_kind": "l.source_kind",
            "source_id": "l.source_id",
            "principal_id": "l.principal_id",
            "role": "p.role",
            "parent_observation_id": "l.parent_observation_id",
            "parent_span_id": "l.parent_span_id",
            "span_id": "p.span_id",
            "reference_id": "l.reference_id",
            "bundle_hash": "l.bundle_hash",
            "snapshot_id": "l.snapshot_id",
            "receipt_id": "l.receipt_id",
            **{
                key: "CASE WHEN l.event_kind IN ('process.exited','process.cleanup_unconfirmed') "
                "THEN p." + key + " ELSE NULL END"
                for key in ("duration_ms", "returncode", "stop_reason", "cleanup_status")
            },
        },
        " FROM reliability_timeline_links l LEFT JOIN reliability_process_spans p "
        "ON p.span_id=l.parent_span_id WHERE l.execution_id=:execution AND l.epoch_id=:epoch",
    )
    return start + " UNION ALL " + end + " UNION ALL " + links


def envelope(conn, selected):
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reliability_traces'"
    ).fetchone()
    trace = (
        conn.execute(
            "SELECT trace_id FROM reliability_traces WHERE execution_id=?", (selected["execution"],)
        ).fetchone()
        if table
        else None
    )
    return {
        "schema_version": 1,
        "scope": selected,
        "trace_id": trace[0] if trace else None,
        "coverage": dict(COVERAGE),
        "measurement_availability": dict(MEASUREMENTS),
        "correlation_status": "available" if trace else "historical_correlation_unavailable",
        "comparison": {"available": False, "reason": "no_qualified_comparison"},
    }


def _item(row):
    result = dict(row)
    result["missing_fields"] = [
        key
        for key in (
            "principal_id",
            "parent_observation_id",
            "parent_span_id",
            "duration_ms",
        )
        if result[key] is None
    ] + ["tokens", "cost", "native_children"]
    return result


def list_timeline(conn, selected, *, kind=None, principal=None, limit=25, cursor=None):
    result = envelope(conn, selected)
    if result["trace_id"] is None:
        return result | {"items": [], "next_cursor": None}
    args = {"execution": selected["execution"], "epoch": selected["epoch"], "limit": limit + 1}
    where = " WHERE 1=1"
    if kind:
        where += " AND event_kind=:kind"
        args["kind"] = kind
    if principal:
        if not conn.execute(
            "SELECT 1 FROM reliability_observations WHERE execution_id=? AND epoch_id=? "
            "AND principal_id=? UNION ALL SELECT 1 FROM reliability_timeline_links "
            "WHERE execution_id=? AND epoch_id=? AND principal_id=? "
            "AND provenance='runtime_observed'",
            (
                selected["execution"],
                selected["epoch"],
                principal,
                selected["execution"],
                selected["epoch"],
                principal,
            ),
        ).fetchone():
            raise LookupError("timeline scope not found")
        where += " AND principal_id=:principal"
        args["principal"] = principal
    if cursor:
        where += " AND (at,item_id)<(:before_time,:before_id)"
        args.update(before_time=cursor[0], before_id=cursor[1])
    rows = conn.execute(
        "SELECT * FROM ("
        + _source_sql()
        + ")"
        + where
        + " ORDER BY at DESC,item_id DESC LIMIT :limit",
        args,
    ).fetchall()
    items = [_item(row) for row in rows[:limit]]
    cursor = (
        {"before_time": items[-1]["at"], "before_id": items[-1]["item_id"]}
        if len(rows) > limit
        else None
    )
    return result | {"items": items, "next_cursor": cursor}


def span_detail(conn, selected, span_id):
    result = envelope(conn, selected)
    if result["trace_id"] is None:
        raise LookupError("timeline scope not found")
    row = conn.execute(
        "SELECT * FROM reliability_process_spans WHERE span_id=? AND execution_id=? AND epoch_id=?",
        (span_id, selected["execution"], selected["epoch"]),
    ).fetchone()
    if row is None:
        raise LookupError("timeline scope not found")
    span = dict(row)
    span["missing_fields"] = [
        key for key in ("duration_ms", "returncode", "finished_at") if span[key] is None
    ] + ["tokens", "cost", "native_children"]
    events = [
        dict(row)
        for row in conn.execute(
            "SELECT link_id,event_kind,created_at,provenance FROM reliability_timeline_links "
            "WHERE parent_span_id=? AND execution_id=? AND epoch_id=? "
            "ORDER BY created_at,link_id LIMIT 10",
            (span_id, selected["execution"], selected["epoch"]),
        )
    ]
    return result | {"span": span, "events": events}
