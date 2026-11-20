"""Strict, read-only incident HTTP contract; fixed failures never disclose storage prose."""

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from harness.reliability import query


def read_incidents(database, raw_query, *, detail=False):
    try:
        values = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True)
        required = {"task", "execution", "epoch"} | ({"incident"} if detail else set())
        allowed = (
            required
            if detail
            else required
            | {
                "category",
                "q",
                "limit",
                "before_time",
                "before_id",
            }
        )
        if (
            not required <= values.keys()
            or not values.keys() <= allowed
            or any(len(v) != 1 for v in values.values())
        ):
            raise ValueError
        data = {k: v[0] for k, v in values.items()}
        if any(not data[k] or len(data[k]) > 128 for k in required):
            raise ValueError
        options = {}
        if not detail:
            if data.get("category", "") not in {"", "operation_denied", "operation_failed"}:
                raise ValueError
            if len(data.get("q", "")) > 128:
                raise ValueError
            raw_limit = data.get("limit", "25")
            if not re.fullmatch(r"[0-9]{1,3}", raw_limit) or not 1 <= int(raw_limit) <= 100:
                raise ValueError
            if ("before_time" in data) != ("before_id" in data):
                raise ValueError
            cursor = None
            if "before_time" in data:
                stamp = datetime.fromisoformat(data["before_time"])
                if (
                    stamp.tzinfo is None
                    or len(data["before_time"]) > 64
                    or not re.fullmatch(r"[0-9a-f]{32}", data["before_id"])
                ):
                    raise ValueError
                cursor = (data["before_time"], data["before_id"])
            options = {
                "category": data.get("category"),
                "q": data.get("q", ""),
                "limit": int(raw_limit),
                "cursor": cursor,
            }
    except (ValueError, TypeError):
        return 400, {"error": "Invalid reliability query"}
    try:
        path = Path(database)
        # Do not call the migration-enabled read/write connector on GET.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            selected = query.scope(conn, data["task"], data["execution"], data["epoch"])
            result = (
                query.detail(conn, selected, data["incident"])
                if detail
                else query.list_incidents(conn, selected, **options)
            )
            return 200, result
    except (LookupError, ValueError):
        return 404, {"error": "Reliability scope not found"}
    except (sqlite3.Error, OSError):
        return 503, {"error": "Reliability storage unavailable"}


def read_timeline(database, raw_query, *, detail=False):
    from harness.reliability import timeline_query
    from harness.reliability.timeline_contracts import KINDS

    try:
        values = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True)
        required = {"task", "execution", "epoch"} | ({"span"} if detail else set())
        allowed = (
            required
            if detail
            else required
            | {
                "kind",
                "principal",
                "limit",
                "before_time",
                "before_id",
            }
        )
        if (
            not required <= values.keys()
            or not values.keys() <= allowed
            or any(len(v) != 1 for v in values.values())
        ):
            raise ValueError
        data = {k: v[0] for k, v in values.items()}
        if any(not data[k] or len(data[k]) > 128 for k in required):
            raise ValueError
        options = {}
        if not detail:
            if data.get("kind") and data["kind"] not in KINDS:
                raise ValueError
            raw_limit = data.get("limit", "25")
            if not re.fullmatch(r"[0-9]{1,3}", raw_limit) or not 1 <= int(raw_limit) <= 100:
                raise ValueError
            principal = data.get("principal")
            if principal and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", principal):
                raise ValueError
            if ("before_time" in data) != ("before_id" in data):
                raise ValueError
            cursor = None
            if "before_time" in data:
                stamp = datetime.fromisoformat(data["before_time"])
                if (
                    stamp.tzinfo is None
                    or len(data["before_time"]) > 64
                    or not re.fullmatch(r"[a-z0-9:]{1,128}", data["before_id"])
                ):
                    raise ValueError
                cursor = data["before_time"], data["before_id"]
            options = {
                "kind": data.get("kind"),
                "principal": principal,
                "limit": int(raw_limit),
                "cursor": cursor,
            }
    except (ValueError, TypeError):
        return 400, {"error": "Invalid timeline query"}
    try:
        with closing(
            sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            selected = query.scope(conn, data["task"], data["execution"], data["epoch"])
            result = (
                timeline_query.span_detail(conn, selected, data["span"])
                if detail
                else timeline_query.list_timeline(conn, selected, **options)
            )
            return 200, result
    except (LookupError, ValueError):
        return 404, {"error": "Timeline scope not found"}
    except (sqlite3.Error, OSError):
        return 503, {"error": "Timeline storage unavailable"}
