"""Approve & Implement from the dashboard (P2-07): the same snapshot as `harness approve`.

approve_task goes through the CLI's ApprovalGate, so the sealed v<N>.md, the spec_versions and
approvals rows, the spec.approved and approval.granted events and both checkpoints are the CLI's.
A guard runs inside that one BEGIN IMMEDIATE transaction, before anything is written: it refuses
a page that rendered an older diagram version, review state or specification (reload), answers
an approval that is still current without writing (a safe retry), and refuses while any diagram
comment is not resolved. The DB is never created.
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

from harness.approval import ApprovalGate
from harness.cli_approve import approval_lines
from harness.cli_commands import task_repo_root
from harness.cli_spec import spec_store
from harness.controller import HarnessController
from harness.dashboard.bodies import BAD_BODY, busy_failure, keys_problem, parse_body
from harness.diagrams import DiagramService
from harness.errors import TaskNotFound
from harness.review_report import ReviewReport
from harness.spec_manager import Guard
from harness.spec_record import content_hash
from harness.state_approvals import ApprovalRecord
from harness.state_comments import CommentStatus

KEYS = frozenset({"task", "version", "spec_hash", "review_hash"})
HASH = re.compile(r"sha256:[0-9a-f]{64}")


class StaleApproval(ValueError):
    """The page approved something other than the current state; it must reload."""


class UnresolvedComments(ValueError):
    pass


class AlreadyApproved(Exception):  # deliberately not a ValueError: it is a 200, not a refusal
    def __init__(self, record: ApprovalRecord):
        self.record = record
        super().__init__(f"approval #{record.id} is current")


def _problem(data: Any) -> str | None:
    """Why the parsed body is not a valid approve request, or None."""
    if not isinstance(data, dict):
        return BAD_BODY
    if keys := keys_problem(data, KEYS):
        return keys
    if not isinstance(data["task"], str) or not data["task"]:
        return "task must be a non-empty string"
    version = data["version"]
    if version is not None and (type(version) is not int or version < 1):
        return "version must be null or a positive integer"
    for name in ("spec_hash", "review_hash"):
        if not isinstance(data[name], str) or not HASH.fullmatch(data[name]):
            return f"{name} must be a sha256:<64 hex> hash"
    return None


def _guard(controller: HarnessController, gate: ApprovalGate, seen: dict[str, Any]) -> Guard:
    def check(report: ReviewReport) -> None:
        """Read-only, inside the engine's transaction (never sync or a comment write here)."""
        service = DiagramService(controller, gate.task_id)
        latest = service.latest()
        current = latest.version if latest else None
        if seen["version"] != current:
            raise StaleApproval(
                f"diagram version {seen['version'] or 'none'} is no longer current"
                f" (current: {current or 'none'}); reload and review the current version"
                " before approving"
            )
        if DiagramService.needs(service.model(), latest):
            raise StaleApproval(
                f"the design changed since diagram version {current or 'none'}; reload to see"
                " the new version before approving"
            )
        if report.state_hash != seen["review_hash"]:
            raise StaleApproval(
                "the final design review changed since the page was loaded; reload before approving"
            )
        if content_hash(gate.manager.body()) != seen["spec_hash"]:
            raise StaleApproval(
                "the specification changed since the page was loaded; reload before approving"
            )
        try:
            record = gate.require()
        except (ValueError, OSError):
            record = None
        if record is not None:
            raise AlreadyApproved(record)
        comments = gate.ws.comments()
        opened = sum(c.status == CommentStatus.OPEN for c in comments)
        addressed = sum(c.status == CommentStatus.ADDRESSED for c in comments)
        if opened + addressed:
            raise UnresolvedComments(
                f"{opened + addressed} diagram comment(s) are not resolved (open {opened}"
                f" · addressed {addressed}); resolve every comment before approving"
            )

    return check


def approval_dict(approval: ApprovalRecord) -> dict[str, Any]:
    return {
        "id": approval.id,
        "spec_version": approval.spec_version,
        "path": approval.path,
        "revision": approval.revision,
        "content_hash": approval.content_hash,
        "review_state_hash": approval.review_state_hash,
        "created_at": approval.created_at,
    }


def approve_task(db_path: Path, body: bytes, specs: str | None) -> tuple[int, dict[str, Any]]:
    """The HTTP status and JSON body for one Approve & Implement request."""
    try:
        data = parse_body(body)
    except ValueError:
        return 400, {"error": BAD_BODY}
    if problem := _problem(data):
        return 400, {"error": problem}
    if not Path(db_path).exists():
        return 404, {"error": str(TaskNotFound(data["task"]))}
    from harness.dashboard.view import open_state  # lazy, like comments_view

    try:
        with open_state(db_path) as controller:
            task_id = controller.get_task(data["task"]).id
            store = spec_store(controller, task_id, specs)
            gate = ApprovalGate(controller, task_id, store, task_repo_root(controller, task_id))
            approval, record, previous = gate.approve(guard=_guard(controller, gate, data))
            return 201, {
                "created": True,
                "approval": approval_dict(approval),
                "spec": {"version": record.version, "path": str(record.path)},
                "superseded": previous.version if previous else None,
                "output": approval_lines(approval, record, previous),
            }
    except AlreadyApproved as error:
        current = error.record
        line = (
            f"approval #{current.id} is current: spec v{current.spec_version}"
            f" {current.content_hash}; nothing was written"
        )
        return 200, {"created": False, "approval": approval_dict(current), "output": [line]}
    except StaleApproval as error:
        return 409, {"error": str(error), "reload": True}
    except UnresolvedComments as error:
        return 409, {"error": str(error)}
    except LookupError as error:
        return 404, {"error": str(error)}
    except ValueError as error:
        return 409, {"error": str(error)}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except (sqlite3.Error, OSError) as error:
        return 500, {"error": str(error)}
