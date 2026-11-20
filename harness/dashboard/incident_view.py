"""Read-only canonical incident, investigation and grouping HTTP views."""

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from harness.dashboard.incident_scope import repository_scope, selected_scope
from harness.reliability import catalog, grouping, investigations, legacy_projection, source_queries

COVERAGE = {
    "facts_are_causes": False,
    "investigation_resolution_is_approval": False,
    "operator_identity": "local_dashboard_operator",
    "verified_component_basis": (
        "existing immutable Knowledge observation; not current source stability"
    ),
    "evaluation_source": "pending R7 actual measured-result producer",
    "standalone_sources": "separate R5b provenance domain",
    "legacy_verification_events": "no canonical test incident without an accepted terminal receipt",
    "check_purpose_is_remote_provenance": False,
    "source_content_validation": (
        "at original acceptance; read-only GET does not repair or revalidate source files"
    ),
}


def _query(raw, action):
    values = parse_qs(raw, keep_blank_values=True, strict_parsing=True)
    common = {"task", "execution", "epoch"}
    extra = {
        "incidents": {"limit", "before_time", "before_id", "q", "category", "source_kind"},
        "detail": {"incident"},
        "investigation": {"incident", "limit", "before_revision"},
        "groups": {"limit", "before_time", "before_id"},
        "group": {"group", "limit", "before_revision"},
        "projection": set(),
        "outputs": {"limit", "before_id"},
    }[action]
    if (
        not values.keys() <= common | extra
        or "task" not in values
        or any(len(v) != 1 for v in values.values())
    ):
        raise ValueError("invalid incident query")
    data = {k: v[0] for k, v in values.items()}
    selected = selected_scope({k: data[k] for k in common if k in data})
    if action in {"projection", "outputs"} and set(selected) != common:
        raise ValueError("explicit recorded epoch required")
    for name in ("incident", "group"):
        if name in extra:
            from harness.reliability.investigation_contracts import identity

            identity(data[name])
    if not re.fullmatch(r"[0-9]{1,3}", data.get("limit", "25")):
        raise ValueError("invalid incident page")
    limit = int(data.get("limit", "25"))
    if not 1 <= limit <= 100:
        raise ValueError("invalid incident page")
    if action != "outputs" and ("before_time" in data) != ("before_id" in data):
        raise ValueError("invalid incident cursor")
    cursor = (
        {"created_at": data["before_time"], "incident_id": data["before_id"]}
        if "before_time" in data
        else None
    )
    if cursor:
        stamp = datetime.fromisoformat(cursor["created_at"])
        if stamp.tzinfo is None or not re.fullmatch(r"[0-9a-f]{32}", cursor["incident_id"]):
            raise ValueError("invalid incident cursor")
    revision = int(data["before_revision"]) if "before_revision" in data else None
    if revision is not None and not 1 <= revision <= 2147483647:
        raise ValueError("invalid investigation revision")
    return data, selected, limit, cursor, revision


def read_catalog(database, root, raw_query, action):
    try:
        data, selected, limit, cursor, revision = _query(raw_query, action)
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "Invalid incident query"}
    try:
        with closing(
            sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            repository_scope(conn, selected, root)
            if action == "incidents":
                result = catalog.list_incidents(
                    conn,
                    selected,
                    limit=limit,
                    cursor=cursor,
                    q=data.get("q", ""),
                    category=data.get("category"),
                    source_kind=data.get("source_kind"),
                )
                for item in result["items"]:
                    repository_scope(conn, selected, root, execution=item["execution_id"])
            elif action == "outputs":
                from harness.dashboard.incident_outputs import list_outputs

                result = list_outputs(conn, selected, limit=limit, before_id=data.get("before_id"))
            elif action == "detail":
                item = catalog.detail(conn, selected, data["incident"])
                repository_scope(conn, selected, root, execution=item["execution_id"])
                result = {
                    "item": item,
                    "source": source_queries.source_relation(conn, selected, data["incident"]),
                    "investigation": investigations.current(conn, selected, data["incident"]),
                }
            elif action == "investigation":
                result = investigations.history(
                    conn, selected, data["incident"], limit=limit, before_revision=revision
                )
                repository_scope(conn, selected, root, execution=result["incident"]["execution_id"])
            elif action == "group":
                group = grouping.get(conn, selected, data["group"])
                for metadata in group["member_metadata"].values():
                    repository_scope(conn, selected, root, execution=metadata["execution_id"])
                result = {
                    "group": group,
                    "history": grouping.decisions(
                        conn, selected, data["group"], before_revision=revision, limit=limit
                    ),
                }
            elif action == "groups":
                where, args = "task_id=?", [selected["task"]]
                if "execution" in selected:
                    where += (
                        " AND json_extract(safe_key,'$.execution_id')=? AND "
                        "json_extract(safe_key,'$.epoch_id')=?"
                    )
                    args.extend((selected["execution"], selected["epoch"]))
                if cursor:
                    where += " AND (created_at,group_id)<(?,?)"
                    args.extend((cursor["created_at"], cursor["incident_id"]))
                rows = conn.execute(
                    "SELECT group_id,created_at FROM incident_group_suggestions WHERE "
                    + where
                    + " ORDER BY created_at DESC,group_id DESC LIMIT ?",
                    (*args, limit + 1),
                ).fetchall()
                items = [grouping.get(conn, selected, r["group_id"]) for r in rows[:limit]]
                for group in items:
                    for item in group["member_metadata"].values():
                        repository_scope(conn, selected, root, execution=item["execution_id"])
                result = {
                    "items": items,
                    "next_cursor": {
                        "before_time": rows[limit - 1]["created_at"],
                        "before_id": rows[limit - 1]["group_id"],
                    }
                    if len(rows) > limit
                    else None,
                }
            else:
                values = catalog.selected_values(selected)
                row = conn.execute(
                    "SELECT revision FROM incident_projection_checkpoints WHERE "
                    "task_id=? AND execution_id=? AND epoch_id=?",
                    values,
                ).fetchone()
                result = {
                    "checkpoint_revision": row[0] if row else 0,
                    "unindexed_observed": bool(legacy_projection.pending(conn, selected)),
                    "projection_coverage": "as of this read; later old-pin rows may be unindexed",
                }
            return 200, {
                "schema_version": 1,
                "scope": selected,
                **result,
                "coverage": dict(COVERAGE),
            }
    except (ValueError, LookupError, KeyError, TypeError):
        return 404, {"error": "Incident scope or record not found"}
    except (OSError, sqlite3.Error):
        return 503, {"error": "Incident storage unavailable"}
