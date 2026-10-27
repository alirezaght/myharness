"""Imported context is visible, source-bound and accepted only by one exact user hash."""

import copy
import hashlib

import pytest

from harness.context_import import import_context
from harness.design_models import DesignSection, EntryStatus
from harness.interview import InterviewEngine
from harness.interview_catalogue import catalogue_for
from harness.interview_report import Provenance
from harness.investigation import InvestigationReport, OpenQuestion
from harness.registry import DecisionRegistry
from harness.review_package import accept, package
from harness.state_models import ProposalStatus, QuestionKind
from harness.testplan import TestPlanner


def context():
    text = "AR-2301: preserve output. AC1: preserve output. I1: keep stored input unchanged."
    anchor = {"source": "audit", "quote": text}
    answers = [
        {
            "id": q.key,
            "topic": q.topic,
            "question": q.text,
            "answer": text,
            "catalogue_key": q.key,
            **anchor,
        }
        for q in catalogue_for(["functional_behavior", "acceptance_criteria"])
    ]
    designs = [
        {
            "id": s.value,
            "section": s.value,
            "statement": "Existing component preserves the reviewed output and ownership boundary.",
            "delegated": False,
            **anchor,
        }
        for s in DesignSection
    ]
    tests = [
        {
            "id": "preserve",
            "category": "regression",
            "statement": "Execute the real component and compare preserved input/output.",
            "covers": ["Q:acceptance_criteria.list", "E:components"],
            "mock": None,
            "mock_reason": None,
            **anchor,
        }
    ]
    return {
        "schema_version": 1,
        "sources": [
            {
                "id": "audit",
                "kind": "audit",
                "content": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "provenance": "User supplied previously reviewed AR-2301 audit.",
            }
        ],
        "answers": answers,
        "design": designs,
        "tests": tests,
        "requirements": {
            "ticket": {"present": True, "id": "AR-2301", "content": text, **anchor},
            "criteria": [{"id": "AC1", "text": "Preserve output", **anchor}],
            "invariants": [{"id": "I1", "text": "Keep stored input unchanged", **anchor}],
        },
    }


def test_import_full_review_and_hash_bound_batch_preserve_individual_decisions(controller):
    task = controller.create_task("Implement AR-2301 from the reviewed audit", "trivial")
    payload = import_context(controller, task.id, context())
    ws = controller.workspace(task.id)
    assert len(ws.questions()) == 4 and len(ws.design_entries()) == 8 and len(ws.scenarios()) == 1
    assert all(p.status is ProposalStatus.PENDING for p in ws.proposals())
    review = package(controller, task.id)
    assert "UNAPPROVED design" in review["content"]["proposed_spec"]
    assert "UNAPPROVED proposed test plan" in review["content"]["proposed_spec"]
    assert context()["tests"][0]["statement"] in review["content"]["proposed_spec"]
    receipt = accept(
        controller,
        task.id,
        review["sha256"],
        source="User explicitly approved this exact complete review package.",
    )
    assert len(receipt["decisions"]) == 13 and receipt["implementation_approval"] is False
    assert all(p.status is ProposalStatus.ACCEPTED for p in ws.proposals())
    assert all(e.status is EntryStatus.ACCEPTED for e in ws.design_entries())
    assert TestPlanner(controller, task.id).document().approved
    assert (
        ws.latest_checkpoint("context_accepted").payload["requirements"]["criteria"][0]["id"]
        == "AC1"
    )
    assert (
        accept(
            controller,
            task.id,
            review["sha256"],
            source="Same explicit user approval replay retained.",
        )
        == receipt
    )
    assert import_context(controller, task.id, context()) == payload


def test_stale_batch_and_bad_source_quote_are_atomic(controller):
    task = controller.create_task(
        "Keep imported review decisions transactional and source-bound", "trivial"
    )
    value = context()
    bad = copy.deepcopy(value)
    bad["design"][2]["quote"] = "not in the source"
    with pytest.raises(ValueError, match="quote"):
        import_context(controller, task.id, bad)
    ws = controller.workspace(task.id)
    assert not ws.questions() and not ws.proposals() and not ws.design_entries()
    import_context(controller, task.id, value)
    review = package(controller, task.id)
    ws.propose_design(
        DesignSection.COMPONENTS,
        "A changed proposed component",
        "New evidence needs another review.",
    )
    before = [p.status for p in ws.proposals()]
    with pytest.raises(ValueError, match="stale"):
        accept(
            controller,
            task.id,
            review["sha256"],
            source="User approved an older exact package hash only.",
        )
    assert [p.status for p in ws.proposals()] == before


def test_batch_answers_are_not_reasked_but_missing_material_questions_remain(controller):
    task = controller.create_task("Implement the supplied reviewed requirements", "trivial")
    controller.record_investigation(
        task.id,
        InvestigationReport(
            "/repo",
            "abc",
            task.request,
            (),
            (),
            (),
            (OpenQuestion("functional_behavior", "Which existing output format?"),),
        ),
    )
    import_context(controller, task.id, context())
    ws = controller.workspace(task.id)
    imported = {p.question_id for p in ws.proposals()}
    review = package(controller, task.id)
    accept(
        controller,
        task.id,
        review["sha256"],
        source="User approved the complete imported review package explicitly.",
    )
    controller.advance(task.id)
    engine = InterviewEngine(controller, task.id)
    asked = engine.next()
    assert asked and all(q.id not in imported for q in asked)
    assert asked[0].text == "Which existing output format?"
    assert all(q.id not in imported for q in ws.open_questions())
    requirements = [r for r in engine.summary().requirements if r.question_id in imported]
    assert len(requirements) == len(imported)
    assert all(r.provenance is Provenance.ACCEPTED_RECOMMENDATION for r in requirements)
    assert engine.checklist().blocking


def test_batch_failure_rolls_back_questions_and_all_individual_decisions(
    controller, monkeypatch, tmp_path
):
    from harness.state import TaskWorkspace

    task = controller.create_task("Keep a failed batch approval atomic", "trivial")
    import_context(controller, task.id, context())
    ws = controller.workspace(task.id)
    question = ws.ask("decisions", "Which storage boundary?", kind=QuestionKind.DECISION)
    ws.propose(
        "Storage boundary", "Keep the existing storage.", "Preserve existing data.", question.id
    )
    registry = DecisionRegistry(tmp_path / "decisions")
    review = package(controller, task.id)
    events = ws.events()

    def unavailable(*args, **kwargs):
        raise OSError("scenario storage unavailable")

    monkeypatch.setattr(TaskWorkspace, "resolve_scenario", unavailable)
    with pytest.raises(OSError, match="scenario storage unavailable"):
        accept(
            controller,
            task.id,
            review["sha256"],
            source="User explicitly approved this exact proposed package.",
            registry=registry,
        )
    assert package(controller, task.id) == review
    assert ws.events() == events
    assert ws.latest_checkpoint("batch_acceptance") is None
    assert not registry.records()


def test_batch_accepted_architecture_decision_uses_the_normal_git_registry(
    controller, tmp_path, db_path
):
    from harness.cli import main

    task = controller.create_task("Retain every approved architecture decision", "trivial")
    controller.record_investigation(
        task.id, InvestigationReport(str(tmp_path), "abc", task.request, (), (), (), ())
    )
    ws = controller.workspace(task.id)
    question = ws.ask("decisions", "Which storage boundary?", kind=QuestionKind.DECISION)
    ws.propose(
        "Storage boundary", "Keep the existing storage.", "Preserve existing data.", question.id
    )
    review = package(controller, task.id)
    registry = DecisionRegistry(tmp_path / "docs/decisions")
    assert (
        main(
            [
                "--db",
                str(db_path),
                "review-package",
                "accept",
                task.id,
                "--sha256",
                review["sha256"],
                "--source",
                "User explicitly approved this exact architecture recommendation.",
            ]
        )
        == 0
    )
    (record,) = registry.records()
    assert record.decision == "Keep the existing storage."
    assert record.rationale == "Preserve existing data."
    assert record.path.is_file() and not ws.open_questions()
    assert any(e.kind == "decision.recorded" for e in ws.events())
