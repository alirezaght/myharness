"""Strict JSON request bodies and DB-busy failures shared by the dashboard's write routes."""

import json
import sqlite3
from typing import Any

from harness.comments import busy

BAD_BODY = "body must be a UTF-8 JSON object"


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"duplicate key {key}")
        found[key] = value
    return found


def keys_problem(data: dict[str, Any], keys: frozenset[str]) -> str | None:
    if set(data) == keys:
        return None
    missing, extra = sorted(keys - set(data)), sorted(set(data) - keys)
    parts = [f"missing {', '.join(missing)}"] if missing else []
    parts += [f"unknown {', '.join(extra)}"] if extra else []
    return f"body must have exactly the keys {', '.join(sorted(keys))} ({'; '.join(parts)})"


def parse_body(body: bytes) -> Any:
    """The JSON body (duplicate keys refused); ValueError when it is not valid UTF-8 JSON."""
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except RecursionError as error:
        raise ValueError(BAD_BODY) from error


def busy_failure(error: sqlite3.OperationalError) -> tuple[int, dict[str, Any]]:
    if busy(error):
        return 503, {"error": f"the state database is busy ({error}); retry", "retry": True}
    return 500, {"error": str(error)}
