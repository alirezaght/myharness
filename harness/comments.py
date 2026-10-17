"""Comment Service (P2-05): comments on diagram elements and their states (DEC-0008, DEC-0011).

A comment is tied to an element id of one diagram version and may only be added against the
task's current version while the design is unchanged since it; otherwise it is refused with a
reload hint and nothing is written. States move open -> addressed -> resolved, and an
addressed or resolved comment can be re-opened. Every write is one transaction holding the row
change and its event; a request id makes a retried add safe (DEC-0014).
"""

import sqlite3
import uuid

from harness.controller import HarnessController
from harness.db import transaction
from harness.diagrams import DiagramService
from harness.errors import RecordNotFound
from harness.state_comments import Comment, CommentKind, CommentStatus
from harness.state_proposals import required


class StaleDiagramVersion(ValueError):
    """The comment refers to a diagram version that is no longer current; reload."""


class RequestConflict(ValueError):
    """The request id was already used for a comment with different content."""


class IllegalTransition(ValueError):
    """The comment's state does not allow the requested move."""


class UnknownElement(LookupError):
    """The element id is not part of the diagram version."""


TRANSITIONS = {
    CommentStatus.ADDRESSED: (CommentStatus.OPEN,),
    CommentStatus.RESOLVED: (CommentStatus.ADDRESSED,),
    CommentStatus.OPEN: (CommentStatus.ADDRESSED, CommentStatus.RESOLVED),
}
EVENTS = {
    CommentStatus.ADDRESSED: "comment.addressed",
    CommentStatus.RESOLVED: "comment.resolved",
    CommentStatus.OPEN: "comment.reopened",
}
VERBS = {
    CommentStatus.ADDRESSED: "addressed",
    CommentStatus.RESOLVED: "resolved",
    CommentStatus.OPEN: "re-opened",
}


class CommentService:
    def __init__(self, controller: HarnessController, task_id: str):
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(self.task_id)
        self.diagrams = DiagramService(controller, self.task_id)

    def _begin(self) -> None:
        if self.conn.in_transaction:
            raise sqlite3.OperationalError("a comment write must run outside a transaction")

    def add(
        self,
        version: int,
        element_id: str,
        kind: CommentKind | str,
        text: str,
        request_id: str | None = None,
    ) -> tuple[Comment, bool]:
        """The comment and whether it was created now (False: a replay of the same request)."""
        kind, text = CommentKind(kind), required(text, "comment text")
        rid = request_id or str(uuid.uuid4())
        self._begin()
        with transaction(self.conn):
            if request_id:
                prior = self.ws.comment_by_request(request_id)
                if prior is not None:
                    same = (prior.diagram_version, prior.element_id, prior.kind, prior.text)
                    if same == (version, element_id, kind, text):
                        return prior, False
                    raise RequestConflict(
                        f"request {request_id} was already used for comment {prior.id}"
                        " with different content"
                    )
            latest = self.diagrams.latest()
            if latest is None or version < 1 or version > latest.version:
                raise RecordNotFound("diagram version", version, self.task_id)
            if version < latest.version:
                raise StaleDiagramVersion(
                    f"diagram version {version} is no longer current (current: version"
                    f" {latest.version}); reload and comment on the current version"
                )
            if DiagramService.needs(self.diagrams.model(), latest):
                raise StaleDiagramVersion(
                    f"the design changed since diagram version {version}; reload to see the"
                    " new version and comment there"
                )
            if element_id not in self.diagrams.elements(latest):
                raise UnknownElement(f"element {element_id} is not in diagram version {version}")
            comment = self.ws.insert_comment(version, element_id, kind, text, rid)
            payload = {"id": comment.id, "version": version, "element": element_id}
            self.ws.record_event("comment.added", payload | {"kind": str(kind)})
            return comment, True

    def address(self, comment_id: int) -> tuple[Comment, bool]:
        return self._move(comment_id, CommentStatus.ADDRESSED)

    def resolve(self, comment_id: int) -> tuple[Comment, bool]:
        return self._move(comment_id, CommentStatus.RESOLVED)

    def reopen(self, comment_id: int) -> tuple[Comment, bool]:
        return self._move(comment_id, CommentStatus.OPEN)

    def _move(self, comment_id: int, target: CommentStatus) -> tuple[Comment, bool]:
        """The comment and whether it moved; a move to its current state changes nothing."""
        self._begin()
        with transaction(self.conn):
            comment = self.ws.comment(comment_id)
            if comment.status == target:
                return comment, False
            allowed = TRANSITIONS[target]
            if comment.status not in allowed:
                states = " or ".join(str(s) for s in allowed)
                raise IllegalTransition(
                    f"comment {comment_id} is {comment.status}: only an {states} comment"
                    f" can be {VERBS[target]}"
                )
            moved = self.ws.set_comment_status(comment_id, comment.status, target)
            payload = {"id": comment_id, "from": str(comment.status), "to": str(target)}
            self.ws.record_event(EVENTS[target], payload)
            return moved, True
