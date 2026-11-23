"""Explicit supersession of requirement rows, recorded as events and never inferred from text.

A later decision, answer or decision version may replace what an earlier requirement row says.
The developer names the replaced rows (`P<id>` for a decided recommendation, `Q<id>` for every
row of a question); a `decision change` names the rows derived from the decision it versions.
The spec then marks those rows as superseded and shows the effective statement next to them.

Rendering only changes for tasks that carry these events, so specs approved before them render
byte-identically (approval re-renders the body and compares its sealed hash).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from harness.state_models import ProposalStatus, QuestionStatus

SUPERSEDED_EVENT = "requirement.superseded"
DECIDED_EVENT = "proposal.decided"
# Set on `proposal.decided` for a modified recommendation whose stored title is the proposal's.
MODIFIED_LABEL = "modified_label"
DECIDED = (ProposalStatus.ACCEPTED, ProposalStatus.MODIFIED)
_REF = re.compile(r"^\s*([PpQq])(\d+)\s*$")


@dataclass(frozen=True)
class Supersession:
    by: str
    statement: str


@dataclass(frozen=True)
class RequirementLineage:
    superseded: dict[str, Supersession] = field(default_factory=dict)
    labelled: frozenset[int] = frozenset()

    def for_row(self, question_id: int | None, proposal_id: int | None) -> Supersession | None:
        if proposal_id is not None and f"P{proposal_id}" in self.superseded:
            return self.superseded[f"P{proposal_id}"]
        if question_id is not None:
            return self.superseded.get(f"Q{question_id}")
        return None


def normalise(refs: Iterable[str] | None) -> list[str]:
    """`p3`/` Q4 ` -> `P3`/`Q4`; anything else is refused."""
    result: list[str] = []
    for ref in refs or ():
        match = _REF.match(ref or "")
        if match is None:
            raise ValueError(f"--supersedes {ref!r} must name a requirement row as P<id> or Q<id>")
        value = f"{match.group(1).upper()}{int(match.group(2))}"
        if value not in result:
            result.append(value)
    return result


def validate(ws, refs: Iterable[str] | None, exclude: Iterable[str] = ()) -> list[str]:
    """Targets must be current requirement rows of this task; never the superseding row."""
    from harness.effective_records import effective_records

    targets = normalise(refs)
    if not targets:
        return []
    effective = effective_records(ws)
    proposals = {f"P{p.id}": p for p in effective.proposals if p.status in DECIDED}
    questions = {
        f"Q{q.id}"
        for q in effective.questions
        if (q.status is QuestionStatus.ANSWERED and q.answer is not None)
        or any(p.question_id == q.id for p in proposals.values())
    }
    excluded = set(exclude)
    for target in targets:
        if target in excluded:
            raise ValueError(f"{target} cannot supersede itself")
        if target not in proposals and target not in questions:
            raise ValueError(
                f"{target} is not a current requirement row of this task"
                " (P<id>: an accepted or modified recommendation; Q<id>: an answered question)"
            )
    return targets


def record(ws, targets: Iterable[str], by: str, statement: str, source: str) -> None:
    targets = list(targets)
    if targets:
        ws.record_event(
            SUPERSEDED_EVENT,
            {"targets": targets, "by": by, "statement": statement, "source": source},
        )


def decision_rows(ws, decision_id: str) -> list[str]:
    """Rows derived from a registry decision: the current recommendations bound to it."""
    from harness.effective_records import effective_records

    effective = effective_records(ws)
    current = {p.id for p in effective.proposals if p.status in DECIDED}
    return [
        f"P{pid}"
        for pid, binding in sorted(effective.registry.items())
        if pid in current and binding.get("id") == decision_id
    ]


def lineage(ws) -> RequirementLineage:
    superseded: dict[str, Supersession] = {}
    labelled: set[int] = set()
    for event in ws.events():
        payload = event.payload
        if event.kind == SUPERSEDED_EVENT:
            for target in payload.get("targets", []):
                superseded[target] = Supersession(payload["by"], payload["statement"])
        elif event.kind == DECIDED_EVENT and payload.get(MODIFIED_LABEL):
            labelled.add(payload["id"])
    return RequirementLineage(superseded, frozenset(labelled))
