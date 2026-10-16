import pytest

from harness.design import ArchitectureDesigner, DesignError
from harness.design_models import Binding, DesignSection, EntryKind, EntryStatus
from harness.design_report import CoverageState
from harness.interview_report import Provenance
from harness.phases import Phase
from harness.state import TaskWorkspace

S = DesignSection
TABLES = ("design_entries", "events", "checkpoints", "tasks")
SPEC_STEP_3 = (
    "Which components exist",
    "What each component does",
    "How components interact",
    "How information moves through the system",
    "What interfaces and contracts are required",
    "Which configurations are needed",
    "How failures are handled",
    "What behavior must be implemented",
)


def _snapshot(conn):
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in TABLES}


def _designer(controller, phase=Phase.DESIGN):
    task = controller.create_task("Add CSV export")
    while controller.get_task(task.id).phase is not phase:
        controller.advance(task.id)
    return ArchitectureDesigner(controller, task.id)


@pytest.fixture
def designer(controller):
    return _designer(controller)


def _states(designer):
    return [(i.ref, i.state) for i in designer.coverage().items]


def test_sections_cover_every_item_of_spec_phase_3(designer):
    assert len(DesignSection) == len(SPEC_STEP_3) == 8
    rendered = designer.document().render()
    headings = [line[4:] for line in rendered.splitlines() if line.startswith("### ")]
    assert headings == [s.heading for s in DesignSection] + ["Delegated to AL", "Coverage"]


def test_fresh_coverage_blocks_and_each_statement_covers_its_section(designer):
    coverage = designer.coverage()
    assert coverage.blocking and coverage.covered == ()
    assert _states(designer) == [(f"section:{s}", CoverageState.UNCOVERED) for s in S]
    for section in S:
        if section is S.CONFIGURATION:
            designer.none(section, "no settings needed")
        else:
            designer.add(section, f"{section} design")
    coverage = designer.coverage()
    assert not coverage.blocking and coverage.covered == tuple(S)
    assert coverage.to_dict()["counts"] == {
        "unresolved": 0,
        "stated": 8,
        "decided": 0,
        "not_applicable": 1,
        "delegated": 0,
    }
    assert "- not applicable: no settings needed (developer)" in designer.document().render()


def test_pending_and_rejected_recommendations_are_never_design_content(designer):
    pending = designer.propose(S.COMPONENTS, "an Exporter service", "single writer")
    rejected = designer.propose(S.DATA_FLOW, "stream rows to disk", "memory bound")
    designer.decide(rejected.id, EntryStatus.REJECTED, None)
    document = designer.document()
    assert document.entries == ()
    assert all(document.section(s) == [] for s in S)
    rendered = document.render()
    assert "Exporter" not in rendered.split("### Coverage")[0]
    assert "stream rows" not in rendered
    assert rendered.split("### Delegated to AL")[0].count("- (uncovered)") == 8
    states = dict(_states(designer))
    assert states[pending.ref] is CoverageState.AWAITING_DECISION
    assert states[f"section:{S.COMPONENTS}"] is CoverageState.UNCOVERED
    assert states[f"section:{S.DATA_FLOW}"] is CoverageState.RECOMMENDATION_REJECTED
    assert designer.coverage().blocking


def test_accept_and_modify_carry_provenance_and_resolve_once(designer):
    a = designer.propose(S.COMPONENTS, "an Exporter service", "single writer")
    m = designer.propose(S.INTERFACES, "CSV with header", "spreadsheet friendly", delegated=True)
    with pytest.raises(ValueError, match="must not be empty"):
        designer.decide(m.id, EntryStatus.MODIFIED, " ")
    accepted = designer.decide(a.id, EntryStatus.ACCEPTED, None)
    modified = designer.decide(m.id, EntryStatus.MODIFIED, "CSV, RFC 4180, with header")
    assert (accepted.text, accepted.provenance) == (
        "an Exporter service",
        Provenance.ACCEPTED_RECOMMENDATION,
    )
    assert (modified.text, modified.provenance, modified.binding) == (
        "CSV, RFC 4180, with header",
        Provenance.MODIFIED_RECOMMENDATION,
        Binding.DELEGATED,
    )
    with pytest.raises(ValueError, match="already accepted"):
        designer.decide(a.id, EntryStatus.REJECTED, None)
    with pytest.raises(ValueError, match="accepted, rejected or modified"):
        designer.decide(m.id, EntryStatus.PENDING, None)
    rendered = designer.document().render()
    assert "- an Exporter service (fixed; accepted_recommendation)" in rendered
    assert "CSV with header" not in rendered


def test_delegated_entries_are_tagged_and_listed(designer):
    designer.add(S.COMPONENTS, "an export module", delegated=True)
    designer.add(S.COMPONENTS, "a single Exporter boundary")
    document = designer.document()
    rendered = document.render()
    assert "- an export module (delegated to AL; developer)" in rendered
    assert "- a single Exporter boundary (fixed; developer)" in rendered
    delegated = rendered.split("### Delegated to AL")[1].split("### Coverage")[0]
    assert "- [Components] an export module (developer)" in delegated
    assert "Exporter boundary" not in delegated
    assert [e["text"] for e in document.to_dict()["delegated"]] == ["an export module"]


@pytest.mark.parametrize("phase", [Phase.INVESTIGATION, Phase.INTERVIEW, Phase.TEST_PLAN])
def test_mutations_outside_design_phase_write_nothing(controller, conn, phase):
    designer = _designer(controller, phase)
    proposal = None
    if phase is Phase.TEST_PLAN:
        controller.go_back(designer.task_id, Phase.DESIGN, "seed")
        proposal = designer.propose(S.COMPONENTS, "x", "y")
        controller.advance(designer.task_id)
    before = _snapshot(conn)
    hint = "next" if phase is not Phase.TEST_PLAN else "back design --reason"
    ops = [
        lambda: designer.add(S.COMPONENTS, "x"),
        lambda: designer.none(S.CONFIGURATION, "none"),
        lambda: designer.propose(S.COMPONENTS, "x", "y"),
    ]
    if proposal is not None:
        ops += [
            lambda: designer.decide(proposal.id, EntryStatus.ACCEPTED, None),
            lambda: designer.withdraw(proposal.id),
        ]
    for op in ops:
        with pytest.raises(DesignError, match=f"phase {phase}; run: harness phase .* {hint}"):
            op()
    assert _snapshot(conn) == before
    designer.coverage(), designer.document().render(), designer.document().to_dict()
    assert _snapshot(conn) == before


def test_mutation_succeeds_after_advance_and_after_going_back(controller):
    designer = _designer(controller, Phase.INTERVIEW)
    controller.advance(designer.task_id)
    designer.add(S.COMPONENTS, "an Exporter")
    controller.advance(designer.task_id)
    controller.go_back(designer.task_id, Phase.DESIGN, "missed a component")
    assert designer.add(S.COMPONENTS, "a Formatter").status is EntryStatus.STATED


def test_each_mutation_records_one_event_and_one_design_checkpoint(designer, conn):
    def counts():
        return [len(designer.ws.events()), len(designer.ws.checkpoints())]

    start = counts()
    entry = designer.propose(S.COMPONENTS, "an Exporter", "why")
    designer.decide(entry.id, EntryStatus.ACCEPTED, None)
    designer.add(S.DATA_FLOW, "rows -> csv")
    designer.withdraw(entry.id)
    designer.none(S.CONFIGURATION, "no config")
    assert counts() == [start[0] + 5, start[1] + 5]
    kinds = [e.kind for e in designer.ws.events()][-5:]
    assert kinds == [
        "design.proposed",
        "design.decided",
        "design.stated",
        "design.withdrawn",
        "design.not_applicable",
    ]
    latest = designer.ws.latest_checkpoint("design")
    assert latest.phase is Phase.DESIGN and latest.payload["blocking"] is True
    assert S.COMPONENTS in latest.payload["uncovered"]
    assert S.DATA_FLOW not in latest.payload["uncovered"]


def test_failing_event_rolls_back_the_whole_command(designer, conn, monkeypatch):
    before = _snapshot(conn)

    def broken(self, kind, payload=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(TaskWorkspace, "record_event", broken)
    with pytest.raises(RuntimeError):
        designer.add(S.COMPONENTS, "x")
    assert _snapshot(conn) == before


def test_invalid_input_writes_nothing(designer, conn):
    entry = designer.add(S.COMPONENTS, "x")
    before = _snapshot(conn)
    for op in (
        lambda: designer.add(S.COMPONENTS, "  "),
        lambda: designer.none(S.CONFIGURATION, ""),
        lambda: designer.propose(S.COMPONENTS, "x", " "),
        lambda: designer.add("bogus", "x"),
        lambda: designer.decide(entry.id, EntryStatus.ACCEPTED, None),
    ):
        with pytest.raises(ValueError):
            op()
    assert _snapshot(conn) == before


def test_not_applicable_conflicts_withdraw_and_conflicting_coverage(designer):
    element = designer.add(S.CONFIGURATION, "EXPORT_DIR setting")
    with pytest.raises(DesignError, match="already has design elements"):
        designer.none(S.CONFIGURATION, "nothing")
    designer.withdraw(element.id)
    assert dict(_states(designer))[f"section:{S.CONFIGURATION}"] is CoverageState.UNCOVERED
    with pytest.raises(ValueError, match="withdrawn; nothing to withdraw"):
        designer.withdraw(element.id)
    pending = designer.propose(S.CONFIGURATION, "EXPORT_DIR setting", "why")
    na = designer.none(S.CONFIGURATION, "no settings")
    assert (na.kind, na.binding) == (EntryKind.NOT_APPLICABLE, Binding.FIXED)
    with pytest.raises(DesignError, match=f"withdraw {na.ref} first"):
        designer.add(S.CONFIGURATION, "y")
    with pytest.raises(DesignError, match=f"withdraw {na.ref} first"):
        designer.propose(S.CONFIGURATION, "y", "why")
    designer.decide(pending.id, EntryStatus.ACCEPTED, None)
    assert dict(_states(designer))[f"section:{S.CONFIGURATION}"] is CoverageState.CONFLICTING
    with pytest.raises(ValueError, match="pending; nothing to withdraw"):
        designer.withdraw(designer.propose(S.COMPONENTS, "z", "why").id)
