"""The specification body (SPEC §5 template), built only from developer-provided or approved state.

Every content line starts with `- ` or `### `; continuation lines of multi-line text are indented
two spaces, so developer text can never introduce a `## ` section of its own.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from harness.decisions import DecisionRecord
from harness.design_models import DesignSection, EntryOrigin
from harness.design_report import DesignDocument
from harness.interview_report import InterviewSummary, Requirement
from harness.state_models import Proposal, ProposalStatus

SECTIONS = (
    "Requirements",
    "Architecture",
    "Components and interfaces",
    "Configuration",
    "Acceptance criteria",
    "Test plan",
    "Rationale",
    "Constraints",
    "Unresolved items",
)
ACCEPTANCE_TOPIC = "acceptance_criteria"
NO_TEST_PLAN = "- (no approved test plan recorded)"
NONE = "- (none)"
ARCHITECTURE = (
    DesignSection.RESPONSIBILITIES,
    DesignSection.INTERACTIONS,
    DesignSection.DATA_FLOW,
    DesignSection.FAILURE_HANDLING,
    DesignSection.REQUIRED_BEHAVIOUR,
)
COMPONENTS = (DesignSection.COMPONENTS, DesignSection.INTERFACES)
DECIDED = (ProposalStatus.ACCEPTED, ProposalStatus.MODIFIED)


def flow(line: str) -> str:
    """One logical line; any embedded line break becomes an indented continuation line."""
    return "\n  ".join(line.splitlines() or [""])


def _listed(lines: Iterable[str]) -> list[str]:
    return [flow(line) for line in lines] or [NONE]


def _requirement(r: Requirement) -> str:
    ref = f"Q{r.question_id} " if r.question_id is not None else ""
    return f"- {ref}[{r.topic}] {r.statement} ({r.provenance})"


def _design(design: DesignDocument, sections: Sequence[DesignSection]) -> list[str]:
    lines: list[str] = []
    for section in sections:
        lines += [f"### {section.heading}", *_listed(design.section_lines(section))]
    return lines


def _rationale(
    proposals: Iterable[Proposal], design: DesignDocument, recorded: Iterable[DecisionRecord]
) -> list[str]:
    lines = [
        f"- P{p.id} {p.title}: {p.rationale} ({p.status})" for p in proposals if p.status in DECIDED
    ]
    lines += [
        f"- {e.ref} [{e.section.heading}] {e.text}: {e.rationale} ({e.provenance})"
        for e in design.entries
        if e.origin is EntryOrigin.RECOMMENDATION and e.rationale
    ]
    lines += [f"- {r.id} v{r.version} — {r.title}: {r.rationale}" for r in recorded]
    return _listed(lines)


def _constraints(decisions: Iterable[dict[str, Any]], summary: InterviewSummary) -> list[str]:
    lines = [
        f"- {d['id']} v{d['version']} — {d['title']} ({d.get('scope') or 'not stated'})"
        for d in decisions
        if d.get("status") == "approved"
    ]
    lines += [
        f"- Q{s.question_id} [{s.topic}] {s.text} — {s.skip_source}: {s.skip_reason}"
        for s in summary.answered_elsewhere
    ]
    return _listed(lines)


def _unresolved(summary: InterviewSummary, design: DesignDocument) -> list[str]:
    checklist, coverage = summary.checklist, design.coverage
    interview = []
    for item in checklist.items:
        first, *rest = item.render()
        interview.append("\n".join([f"- {first}", *rest]))
    return [
        "### Interview",
        *_listed(interview),
        f"- blocking: {'yes' if checklist.blocking else 'no'}",
        "### Design",
        *_listed(f"- {item.render()}" for item in coverage.items),
        f"- blocking: {'yes' if coverage.blocking else 'no'}",
    ]


def build_body(
    summary: InterviewSummary,
    design: DesignDocument,
    decisions: Iterable[dict[str, Any]],
    recorded: Iterable[DecisionRecord],
    proposals: Iterable[Proposal] = (),
) -> str:
    """The `## ` sections of the spec, in template order; empty sections say `- (none)`."""
    requirements = [r for r in summary.requirements if r.topic != ACCEPTANCE_TOPIC]
    criteria = [r for r in summary.requirements if r.topic == ACCEPTANCE_TOPIC]
    content = {
        "Requirements": _listed(_requirement(r) for r in requirements),
        "Architecture": [
            *_design(design, ARCHITECTURE),
            "### Delegated to AL",
            *_listed(design.delegated_lines()),
        ],
        "Components and interfaces": _design(design, COMPONENTS),
        "Configuration": _listed(design.section_lines(DesignSection.CONFIGURATION)),
        "Acceptance criteria": _listed(_requirement(r) for r in criteria),
        "Test plan": [NO_TEST_PLAN],
        "Rationale": _rationale(proposals, design, list(recorded)),
        "Constraints": _constraints(decisions, summary),
        "Unresolved items": _unresolved(summary, design),
    }
    parts = [f"## {name}\n\n" + "\n".join(content[name]) + "\n" for name in SECTIONS]
    return "\n".join(parts)
