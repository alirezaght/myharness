"""One hash-bound explicit approval, individual decisions, atomic failure handling."""

from dataclasses import asdict

from harness.db import transaction
from harness.decision_workflow import discard_on_failure, record_approved_proposal
from harness.design_models import EntryStatus
from harness.execution_contract import digest
from harness.state_models import ProposalStatus, QuestionStatus
from harness.testplan import TestPlanner
from harness.testplan_report import ReviewState, plan_hash


def package(controller, task_id):
    from harness.design import ArchitectureDesigner
    from harness.interview import InterviewEngine
    from harness.spec_document import build_body, proposed_content
    from harness.spec_manager import _recorded_decisions

    ws = controller.workspace(task_id)
    task = controller.get_task(task_id)
    design = ArchitectureDesigner(controller, task_id).document()
    plan = TestPlanner(controller, task_id).document()
    investigation = ws.latest_checkpoint("investigation")
    decisions = investigation.payload.get("decisions", []) if investigation else []
    body = build_body(
        InterviewEngine(controller, task_id).summary(),
        design,
        decisions,
        _recorded_decisions(ws.events()),
        ws.proposals(),
        plan,
    )
    body = proposed_content(body, ws.proposals(), ws.design_entries(), ws.scenarios(), plan)
    context = ws.latest_checkpoint("context_import")
    content = {
        "schema_version": 1,
        "kind": "task-review-package-v1",
        "task_id": task_id,
        "request": task.request,
        "phase": str(task.phase),
        "complexity": str(task.complexity),
        "proposed_spec": body,
        "questions": [asdict(x) for x in ws.questions()],
        "proposals": [asdict(x) for x in ws.proposals()],
        "design": [asdict(x) for x in ws.design_entries()],
        "tests": [asdict(x) for x in ws.scenarios()],
        "plan_review": plan.review.to_dict(),
        "context": context.payload if context else None,
    }
    return {"sha256": digest(content), "content": content}


def accept(controller, task_id, approved_sha256, *, source, registry=None):
    if not isinstance(source, str) or len(source.strip()) < 20:
        raise ValueError("batch acceptance requires explicit substantive user approval provenance")
    ws = controller.workspace(task_id)
    with discard_on_failure() as written, transaction(controller.conn):
        prior = ws.latest_checkpoint("batch_acceptance")
        current = package(controller, task_id)
        if prior and prior.payload["approved_sha256"] == approved_sha256:
            if current["sha256"] != prior.payload["result_sha256"]:
                raise ValueError("accepted package changed; review current content")
            return prior.payload
        if current["sha256"] != approved_sha256:
            raise ValueError(
                "review package is stale; approval hash must match exact current proposed content"
            )
        if controller.get_task(task_id).phase == "approval":
            raise ValueError("batch acceptance cannot rewrite an approved specification")
        decisions = []
        for item in ws.proposals(ProposalStatus.PENDING):
            proposal = ws.resolve(item.id, ProposalStatus.ACCEPTED)
            if item.question_id is not None:
                question = ws.question(item.question_id)
                if question.status is QuestionStatus.OPEN:
                    ws.mark_decided(question.id)
                if registry is not None:
                    record = record_approved_proposal(ws, registry, proposal, question)
                    if record:
                        written.append(record)
            event = {
                "id": item.id,
                "status": "accepted",
                "question": item.question_id,
                "source": source,
                "package_sha256": approved_sha256,
            }
            ws.record_event("proposal.decided", event)
            decisions.append({"kind": "proposal", **event})
        for item in ws.design_entries(EntryStatus.PENDING):
            ws.resolve_design(item.id, EntryStatus.ACCEPTED)
            event = {
                "id": item.id,
                "status": "accepted",
                "source": source,
                "package_sha256": approved_sha256,
            }
            ws.record_event("design.decided", event)
            decisions.append({"kind": "design", **event})
        for item in ws.scenarios(EntryStatus.PENDING):
            ws.resolve_scenario(item.id, EntryStatus.ACCEPTED)
            event = {
                "id": item.id,
                "status": "accepted",
                "source": source,
                "package_sha256": approved_sha256,
            }
            ws.record_event("scenario.decided", event)
            decisions.append({"kind": "scenario", **event})
        document = TestPlanner(controller, task_id).document()
        if any(
            item.state in {ReviewState.AWAITING_DECISION, ReviewState.MOCK_FLAGGED}
            for item in document.review.items
        ):
            raise ValueError("batch cannot approve undecided or flagged test boundaries")
        if document.scenarios:
            approval = ws.record_plan_approval(
                plan_hash(document.scenarios), len(document.scenarios)
            )
            ws.record_event(
                "test_plan.approved",
                {
                    "approval_id": approval.id,
                    "content_hash": approval.content_hash,
                    "source": source,
                    "package_sha256": approved_sha256,
                },
            )
        context = ws.latest_checkpoint("context_import")
        if context:
            ws.checkpoint("context_accepted", context.payload)
        receipt = {
            "schema_version": 1,
            "kind": "batch-acceptance-v1",
            "approved_sha256": approved_sha256,
            "source": source,
            "decisions": decisions,
            "result_sha256": package(controller, task_id)["sha256"],
            "implementation_approval": False,
            "final_review_required": True,
        }
        ws.checkpoint("batch_acceptance", receipt)
        ws.record_event("batch.accepted", receipt)
    return receipt
