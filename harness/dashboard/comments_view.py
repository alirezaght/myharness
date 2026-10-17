"""The comments view and the add-comment write (P2-05).

comments_section lists every comment of the task with its element's kind and label taken from
the comment's own diagram version. add_comment is the dashboard's only comment write: a strict
JSON body, then the Comment Service in one transaction; the DB is never created.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from harness.comments import CommentService, RequestConflict, StaleDiagramVersion
from harness.controller import HarnessController, Task
from harness.diagrams import DiagramService
from harness.errors import TaskNotFound
from harness.state_comments import Comment, CommentKind, CommentStatus

KEYS = frozenset({"task", "version", "element", "kind", "text", "request_id"})
REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
BAD_BODY = "body must be a UTF-8 JSON object"
BUSY_CODES = (5, 6)  # SQLITE_BUSY, SQLITE_LOCKED (primary result codes)


def comment_dict(comment: Comment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "version": comment.diagram_version,
        "element": comment.element_id,
        "kind": str(comment.kind),
        "text": comment.text,
        "status": str(comment.status),
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
    }


def comments_section(controller: HarnessController, task: Task) -> dict[str, Any]:
    service = DiagramService(controller, task.id)
    comments = service.ws.comments()
    elements: dict[int, dict[str, tuple[str, str]]] = {}
    items = []
    for comment in comments:
        number = comment.diagram_version
        if number not in elements:
            elements[number] = service.elements(service.version(number))
        kind, label = elements[number].get(comment.element_id, (None, None))
        items.append(comment_dict(comment) | {"element_kind": kind, "element_label": label})
    counts = {str(s): sum(c.status == s for c in comments) for s in CommentStatus}
    return {"kinds": [str(k) for k in CommentKind], "counts": counts, "items": items}


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"duplicate key {key}")
        found[key] = value
    return found


def _problem(data: Any) -> str | None:
    """Why the parsed body is not a valid comment request, or None."""
    if not isinstance(data, dict):
        return BAD_BODY
    if set(data) != KEYS:
        missing, extra = sorted(KEYS - set(data)), sorted(set(data) - KEYS)
        parts = [f"missing {', '.join(missing)}"] if missing else []
        parts += [f"unknown {', '.join(extra)}"] if extra else []
        return f"body must have exactly the keys {', '.join(sorted(KEYS))} ({'; '.join(parts)})"
    for name in ("task", "element"):
        if not isinstance(data[name], str) or not data[name]:
            return f"{name} must be a non-empty string"
    version = data["version"]
    if type(version) is not int or version < 1:
        return "version must be a positive integer"
    if not isinstance(data["kind"], str) or data["kind"] not in {str(k) for k in CommentKind}:
        return f"kind must be one of {', '.join(CommentKind)}"
    if not isinstance(data["text"], str) or not data["text"].strip():
        return "text must not be empty"
    rid = data["request_id"]
    if not isinstance(rid, str) or not REQUEST_ID.fullmatch(rid):
        return "request_id must be 8 to 64 letters, digits or hyphens"
    return None


def _busy(error: sqlite3.OperationalError) -> bool:
    return (getattr(error, "sqlite_errorcode", 0) & 0xFF) in BUSY_CODES


def add_comment(db_path: Path, body: bytes) -> tuple[int, dict[str, Any]]:
    """The HTTP status and JSON body for one add-comment request."""
    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError):
        return 400, {"error": BAD_BODY}
    if problem := _problem(data):
        return 400, {"error": problem}
    if not Path(db_path).exists():
        return 404, {"error": str(TaskNotFound(data["task"]))}
    from harness.dashboard.view import open_state  # lazy: view imports this module

    try:
        with open_state(db_path) as controller:
            comment, created = CommentService(controller, data["task"]).add(
                data["version"], data["element"], data["kind"], data["text"], data["request_id"]
            )
            return (201 if created else 200), {"comment": comment_dict(comment)}
    except StaleDiagramVersion as error:
        return 409, {"error": str(error), "reload": True}
    except RequestConflict as error:
        return 409, {"error": str(error)}
    except LookupError as error:
        return 404, {"error": str(error)}
    except ValueError as error:
        return 400, {"error": str(error)}
    except sqlite3.OperationalError as error:
        if _busy(error):
            return 503, {"error": f"the state database is busy ({error}); retry", "retry": True}
        return 500, {"error": str(error)}
    except (sqlite3.Error, OSError) as error:
        return 500, {"error": str(error)}
