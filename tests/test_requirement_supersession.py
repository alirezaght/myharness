"""Issue #5: the spec shows effective requirement text and marks superseded rows explicitly."""

import re

import pytest

from harness.db import transaction
from harness.decision_workflow import DecisionWorkflow
from harness.interview import InterviewEngine
from harness.registry import DecisionRegistry
from harness.spec_manager import SpecificationManager
from harness.spec_store import SpecStore
from harness.state_decisions import ChangeStatus


def _section(body, name):
    return body.split(f"## {name}\n\n")[1].split("\n\n## ")[0].rstrip("\n")


@pytest.fixture
def setup(controller, tmp_path):
    registry = DecisionRegistry(tmp_path / "docs/decisions")
    task = controller.create_task("Explicit supersession", "trivial")
    ws = controller.workspace(task.id)
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"decisions": []})
    controller.advance(task.id)
    engine = InterviewEngine(controller, task.id, registry)
    flow = DecisionWorkflow(controller, task.id, registry)
    manager = SpecificationManager(controller, task.id, SpecStore(tmp_path / "specs"), tmp_path)
    return engine, flow, manager, registry


def _bodies(controller, manager):
    from harness.review_package import package

    return [
        manager.structured_body(),
        package(controller, manager.task_id)["content"]["proposed_spec"],
    ]


def _decided(engine, text, title, recommendation, verdict="accepted", response=None, **kw):
    question = engine.ask("functional_behavior", text, decision=True)
    proposal = engine.propose(question.id, title, recommendation, "Because")
    engine.decide(proposal.id, verdict, response, **kw)
    return question, proposal


def test_modified_recommendation_title_is_labelled_and_effective_text_shown(controller, setup):
    engine, _, manager, registry = setup
    _, proposal = _decided(
        engine, "Threshold?", "Withhold below 3", "Withhold below 3", "modified", "Withhold below 5"
    )
    record = registry.get(engine.last_record.id)
    assert record.title == "Withhold below 3 (modified)"
    assert record.decision == "Withhold below 5"
    for body in _bodies(controller, manager):
        rationale = _section(body, "Rationale")
        assert (
            f"- P{proposal.id} (modified) Withhold below 5: Because — modified from the proposal"
            " titled Withhold below 3 (modified)"
        ) in rationale
        assert f"{record.id} v1 — Withhold below 3 (modified):" in rationale
        assert "Withhold below 5 (modified_recommendation)" in _section(body, "Requirements")


def test_later_decision_marks_named_rows_superseded(controller, setup):
    engine, flow, manager, _ = setup
    question, proposal = _decided(engine, "Tool name?", "Tool", "Expose old_name")
    answered = engine.ask("functional_behavior", "Where is it called?")
    engine.answer(answered.id, "Callers use old_name")
    record = flow.record(
        "Rename tool",
        "Rename old_name to new_name",
        "Clearer",
        supersedes=[f"p{proposal.id}", f"Q{answered.id}"],
    )
    for body in _bodies(controller, manager):
        rows = _section(body, "Requirements")
        assert (
            f"- Q{question.id} [functional_behavior] [superseded by {record.id} v1] Expose old_name"
            f" (accepted_recommendation)\n  effective ({record.id} v1): Rename old_name to new_name"
        ) in rows
        assert (
            f"- Q{answered.id} [functional_behavior] [superseded by {record.id} v1]"
            " Callers use old_name (developer)"
        ) in rows
        assert f"[superseded by {record.id} v1]" in _section(body, "Rationale")


def test_decision_change_marks_the_row_derived_from_the_decision(controller, setup):
    engine, flow, manager, _ = setup
    question, _ = _decided(engine, "Endpoints?", "Counts", "A counts-only endpoint")
    decision_id = engine.last_record.id
    change, _ = flow.change(decision_id, "Two id-returning endpoints", "Need ids", "API grows")
    flow.decide(change.id, ChangeStatus.ACCEPTED)
    for body in _bodies(controller, manager):
        rows = _section(body, "Requirements")
        assert (
            f"- Q{question.id} [functional_behavior] [superseded by {decision_id} v2]"
            " A counts-only endpoint (accepted_recommendation)\n"
            f"  effective ({decision_id} v2): Two id-returning endpoints"
        ) in rows
        assert re.search(
            rf"^- {decision_id} v2 — Counts: Need ids$", _section(body, "Rationale"), re.M
        )


def test_modified_answer_supersedes_an_earlier_accepted_row(controller, setup):
    engine, _, manager, _ = setup
    first, accepted = _decided(engine, "Languages?", "Two", "German or English")
    second, modified = _decided(
        engine, "Message languages?", "Two", "German or English", "modified",
        "German, English or French", supersedes=[f"P{accepted.id}"],
    )  # fmt: skip
    for body in _bodies(controller, manager):
        rows = _section(body, "Requirements")
        assert (
            f"- Q{first.id} [functional_behavior] [superseded by P{modified.id} (Q{second.id})]"
            " German or English (accepted_recommendation)\n"
            f"  effective (P{modified.id} (Q{second.id})): German, English or French"
        ) in rows
        assert (
            f"- Q{second.id} [functional_behavior] German, English or French"
            " (modified_recommendation)"
        ) in rows


@pytest.mark.parametrize("ref", ["X1", "P99", "Q99"])
def test_supersedes_refuses_unknown_rows_and_writes_nothing(controller, setup, ref):
    engine, flow, _, registry = setup
    _decided(engine, "Tool name?", "Tool", "Expose old_name")
    before = [p.name for p in registry.directory.iterdir()]
    events = len(controller.workspace(flow.task_id).events())
    with pytest.raises(ValueError):
        flow.record("Rename", "Rename it", "Clearer", supersedes=[ref])
    assert [p.name for p in registry.directory.iterdir()] == before
    assert len(controller.workspace(flow.task_id).events()) == events


def test_rejected_recommendation_cannot_supersede(controller, setup):
    engine, _, _, _ = setup
    _, accepted = _decided(engine, "Languages?", "Two", "German or English")
    question = engine.ask("functional_behavior", "Other?", decision=True)
    proposal = engine.propose(question.id, "Other", "Other", "Because")
    with pytest.raises(ValueError, match="only an accepted or modified"):
        engine.decide(proposal.id, "rejected", None, supersedes=[f"P{accepted.id}"])


def test_specs_without_supersession_events_render_unchanged(controller, setup):
    """Approval re-renders the body; legacy modified decisions keep their original layout."""
    engine, _, manager, _ = setup
    question = engine.ask("functional_behavior", "Threshold?", decision=True)
    proposal = engine.propose(question.id, "Withhold below 3", "Withhold below 3", "Because")
    with transaction(controller.conn):
        engine.ws.resolve(proposal.id, "modified", "Withhold below 5")
        engine.ws.mark_decided(question.id)
        engine.ws.record_event("proposal.decided", {"id": proposal.id, "status": "modified"})
    body = manager.structured_body()
    assert _section(body, "Rationale") == f"- P{proposal.id} Withhold below 3: Because (modified)"
    assert "superseded" not in body


def test_cli_supersedes_flags(db_path, tmp_path, capsys):
    from harness.cli import main
    from tests.sample_repo import make_sample_repo

    def ok(*argv):
        code = main(["--db", str(db_path), *argv])
        captured = capsys.readouterr()
        assert (code, captured.err) == (0, ""), captured.err
        return captured.out

    repo = make_sample_repo(tmp_path / "plain", with_git=False)
    task = re.search(r"task (t-[0-9a-f]{8}) created", ok("start", "csv", "export")).group(1)
    ok("investigate", task, "--repo", str(repo))
    ok("phase", task, "next")
    first = re.match(r"Q(\d+)", ok("interview", "ask", task, "scope", "Tool?")).group(1)
    ok("interview", "answer", task, first, "old_name")
    second = re.match(r"Q(\d+)", ok("interview", "ask", task, "scope", "Callers?")).group(1)
    ok("interview", "answer", task, second, "use", "new_name", "--supersedes", f"Q{first}")
    out = ok(
        "decision", "record", task, "--title", "Rename", "--decision", "old_name -> new_name",
        "--rationale", "Clearer", "--supersedes", f"Q{second}",
    )  # fmt: skip
    decision = out.split()[0]
    rows = _section(ok("spec", "show", task), "Requirements")
    assert f"- Q{first} [scope] [superseded by Q{second}] old_name (developer)" in rows
    assert f"- Q{second} [scope] [superseded by {decision} v1] use new_name (developer)" in rows
    code = main(["--db", str(db_path), "decision", "record", task, "--title", "x",
                 "--decision", "y", "--rationale", "z", "--supersedes", "Q999"])  # fmt: skip
    assert code == 1 and "Q999 is not a current requirement row" in capsys.readouterr().err
