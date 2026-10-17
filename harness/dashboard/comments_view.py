"""The comments view and the dashboard's comment writes (P2-05, P2-06).

comments_section lists every comment of the task with its element's kind and label taken from
the comment's own diagram version, and the version each addressed comment was addressed in.
add_comment adds a comment; move_comment resolves or re-opens one (never addresses: that is the
harness's `python -m harness.redesign address`). Each takes a strict JSON body, then the Comment
Service in one transaction; the DB is never created.
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

from harness.comments import (
    CommentService,
    IllegalTransition,
    RequestConflict,
    StaleDiagramVersion,
    element_labels,
)
from harness.controller import HarnessController, Task
from harness.dashboard.bodies import BAD_BODY, busy_failure, keys_problem, parse_body
from harness.diagrams import DiagramService
from harness.errors import TaskNotFound
from harness.state_comments import Comment, CommentKind, CommentStatus

KEYS = frozenset({"task", "version", "element", "kind", "text", "request_id"})
REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
MOVE_KEYS = frozenset({"task", "comment"})
MOVES = ("resolve", "reopen")


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
    labels = element_labels(service, comments)
    items = []
    for comment in comments:
        kind, label = labels[comment.id]
        items.append(comment_dict(comment) | {"element_kind": kind, "element_label": label})
    counts = {str(s): sum(c.status == s for c in comments) for s in CommentStatus}
    return {
        "kinds": [str(k) for k in CommentKind],
        "counts": counts,
        "items": items,
        "addressed_in": _addressed_in(service, comments),
    }


def _addressed_in(service: DiagramService, comments: list[Comment]) -> dict[str, int]:
    """{comment id: the version it was last addressed in} for comments that are not open."""
    seen: dict[int, int] = {}
    for event in service.ws.events():
        if event.kind == "comment.addressed" and "version" in event.payload:
            seen[event.payload["id"]] = event.payload["version"]
    moved = {c.id for c in comments if c.status != CommentStatus.OPEN}
    return {str(i): v for i, v in seen.items() if i in moved}


def _problem(data: Any) -> str | None:
    """Why the parsed body is not a valid comment request, or None."""
    if not isinstance(data, dict):
        return BAD_BODY
    if keys := keys_problem(data, KEYS):
        return keys
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


def add_comment(db_path: Path, body: bytes) -> tuple[int, dict[str, Any]]:
    """The HTTP status and JSON body for one add-comment request."""
    try:
        data = parse_body(body)
    except ValueError:
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
        return busy_failure(error)
    except (sqlite3.Error, OSError) as error:
        return 500, {"error": str(error)}


def _move_problem(data: Any) -> str | None:
    """Why the parsed body is not a valid resolve/re-open request, or None."""
    if not isinstance(data, dict):
        return BAD_BODY
    if keys := keys_problem(data, MOVE_KEYS):
        return keys
    if not isinstance(data["task"], str) or not data["task"]:
        return "task must be a non-empty string"
    if type(data["comment"]) is not int or data["comment"] < 1:
        return "comment must be a positive integer"
    return None


def move_comment(db_path: Path, body: bytes, action: str) -> tuple[int, dict[str, Any]]:
    """The HTTP status and JSON body for one resolve or re-open request (no stale check, Q7c).

    A move to the comment's current state is a 200 with changed false and writes nothing.
    """
    if action not in MOVES:
        raise ValueError(f"unknown comment action {action}")
    try:
        data = parse_body(body)
    except ValueError:
        return 400, {"error": BAD_BODY}
    if problem := _move_problem(data):
        return 400, {"error": problem}
    if not Path(db_path).exists():
        return 404, {"error": str(TaskNotFound(data["task"]))}
    from harness.dashboard.view import open_state  # lazy: view imports this module

    try:
        with open_state(db_path) as controller:
            service = CommentService(controller, data["task"])
            comment, changed = getattr(service, action)(data["comment"])
            return 200, {"comment": comment_dict(comment), "changed": changed}
    except IllegalTransition as error:
        return 409, {"error": str(error)}
    except LookupError as error:
        return 404, {"error": str(error)}
    except ValueError as error:
        return 400, {"error": str(error)}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except (sqlite3.Error, OSError) as error:
        return 500, {"error": str(error)}
