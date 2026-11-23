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
    from harness.current_decisions import (
        current_investigation_decisions,
        current_recorded_decisions,
    )
    from harness.design import ArchitectureDesigner
    from harness.effective_records import effective_records
    from harness.interview import InterviewEngine
    from harness.investigation_store import InvestigationStore
    from harness.requirement_lineage import lineage
    from harness.spec_document import build_body, proposed_content

    if InvestigationStore(controller, None, task_id).binding():
        return investigation_package(controller, task_id)
    ws = controller.workspace(task_id)
    task = controller.get_task(task_id)
    design = ArchitectureDesigner(controller, task_id).document()
    plan = TestPlanner(controller, task_id).document()
    effective = effective_records(ws)
    decisions = current_investigation_decisions(ws)
    body = build_body(
        InterviewEngine(controller, task_id).summary(),
        design,
        decisions,
        current_recorded_decisions(ws),
        effective.proposals,
        plan,
        lineage(ws),
    )
    body = proposed_content(body, effective.proposals, ws.design_entries(), ws.scenarios(), plan)
    context = ws.latest_checkpoint("context_import")
    content = {
        "schema_version": 1,
        "kind": "task-review-package-v1",
        "task_id": task_id,
        "request": task.request,
        "session_material": __import__(
            "harness.planning_session_material", fromlist=["basis"]
        ).basis(controller.conn, task_id),
        "phase": str(task.phase),
        "complexity": str(task.complexity),
        "proposed_spec": body,
        "effective_lineage": effective.metadata(),
        "questions": [asdict(x) for x in effective.questions],
        "proposals": [asdict(x) for x in effective.proposals],
        "design": [asdict(x) for x in ws.design_entries()],
        "tests": [asdict(x) for x in ws.scenarios()],
        "plan_review": plan.review.to_dict(),
        "context": context.payload if context else None,
    }
    return {"sha256": digest(content), "content": content}


def accept(controller, task_id, approved_sha256, *, source, registry=None):
    if not isinstance(source, str) or len(source.strip()) < 20:
        raise ValueError("batch acceptance requires explicit substantive user approval provenance")
    from harness.investigation_store import InvestigationStore

    if InvestigationStore(controller, None, task_id).binding():
        with transaction(controller.conn):
            return accept_investigation(controller, task_id, approved_sha256, source=source)
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
        from harness.effective_records import effective_records

        for item in effective_records(ws).proposals:
            if item.status is not ProposalStatus.PENDING:
                continue
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


def investigation_package(controller, task_id):
    from harness.authoring_store import AuthoringStore, adopted_revision
    from harness.effective_records import effective_records
    from harness.investigation_store import InvestigationStore, review_basis

    store = InvestigationStore(controller, None, task_id)
    binding, bundle = store.binding(), store.read_active()
    row = controller.conn.execute(
        "SELECT * FROM authoring_tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    authoring = AuthoringStore(controller, row["project"], task_id)
    working = authoring.revision(row["working_id"])
    adopted = adopted_revision(controller, task_id)
    selected = adopted or working
    effective = effective_records(controller.workspace(task_id))
    from harness.investigation_lineage import source_lineage

    source = source_lineage(controller, task_id, effective)
    retired = set(source["retired_ids"])
    # Acceptance is excluded from this semantic package to avoid a self-invalidating hash.
    content = {
        "schema_version": 2,
        "kind": "investigation-review-package-v2",
        "task_id": task_id,
        "basis": review_basis(controller, task_id),
        "proposed_spec": selected["body"],
        "graph": selected["graph"],
        "working_revision": working["id"],
        "working_body": working["body"],
        "adopted_revision": adopted["id"] if adopted else None,
        "bundle": bundle["id"] if bundle else None,
        "selection": binding["selection"],
        "validation_generation": binding["validation_generation"],
        "sources": bundle["content"]["sources"] if bundle else [],
        "evidence": bundle["content"]["evidence"] if bundle else None,
        "limitations": bundle["content"]["limitations"] if bundle else [],
        "complete": bool(bundle and bundle["content"]["complete"] and not binding["recovery"]),
        "questions": [asdict(q) for q in effective.questions if q.id not in retired],
        "proposals": [asdict(p) for p in effective.proposals if p.question_id not in retired],
        "source_lineage": source,
        "historical_questions": [
            asdict(q) for q in effective.historical_questions if q.id in retired
        ],
        "historical_proposals": [
            asdict(p) for p in effective.historical_proposals if p.question_id in retired
        ],
        "effective_lineage": effective.metadata(),
    }
    if bundle and bundle["content"].get("origin") == "attached":
        content["origin"] = "attached"
        content["provenance"] = bundle["content"]["provenance"]
        content["observations"] = bundle["content"]["observations"]
    return {"sha256": digest(content), "content": content}


def accept_investigation(controller, task_id, approved_sha256, *, source):
    from harness.authoring_contract import canonical
    from harness.authoring_contract import digest as evidence_digest
    from harness.authoring_store import identity
    from harness.db import utcnow
    from harness.effective_records import effective_records
    from harness.interview_coverage import current_coverage
    from harness.investigation_store import InvestigationStore, review_basis

    if not controller.conn.in_transaction:
        raise RuntimeError("existing caller transaction required")
    if not isinstance(source, str) or len(source.strip()) < 20:
        raise ValueError("explicit substantive source-review approval provenance required")
    store = InvestigationStore(controller, None, task_id)
    bundle = store.read_active()
    current = investigation_package(controller, task_id)
    prior = store.acceptance()
    if (
        prior
        and prior["package_hash"] == approved_sha256
        and prior["basis"] == review_basis(controller, task_id)
    ):
        return {
            "acceptance": prior["id"],
            "implementation_approval": False,
            "final_review_required": True,
        }
    if (
        current["sha256"] != approved_sha256
        or not current["content"]["complete"]
        or not current["content"]["graph"]
    ):
        raise ValueError("source review is stale or materially incomplete")
    if controller.get_task(task_id).phase == "approval":
        raise ValueError("source acceptance cannot rewrite approved content")
    coverage = current_coverage(controller, task_id)
    if any(
        row["state"] not in {"pending_source_review", "accepted_source_evidence"}
        for row in coverage.rows
    ):
        raise ValueError(
            "unresolved genuine decisions, source conflicts or operational gaps block acceptance"
        )
    ws = controller.workspace(task_id)
    effective = effective_records(ws)
    if any(
        p.status == "pending" and p.question_id not in coverage.retired_ids
        for p in effective.proposals
    ):
        raise ValueError("pending developer recommendations require a separate explicit decision")
    if any(q.status == "open" and not coverage.suppress(q) for q in effective.questions):
        raise ValueError("unresolved genuine decisions require an explicit developer response")
    decision_ids = set(coverage.decision_ids)
    if any(q.id in decision_ids and q.status == "open" for q in effective.questions):
        raise ValueError("unknown genuine decisions cannot be accepted as source evidence")
    ident = identity()
    controller.conn.execute(
        "INSERT INTO investigation_acceptances VALUES(?,?,?,?,?,?,?,?)",
        (
            ident,
            task_id,
            bundle["id"],
            evidence_digest(bundle["content"]["evidence"]),
            canonical(current["content"]["basis"]),
            approved_sha256,
            source,
            utcnow(),
        ),
    )
    result = {
        "acceptance": ident,
        "bundle": bundle["id"],
        "package_sha256": approved_sha256,
        "implementation_approval": False,
        "final_review_required": True,
    }
    ws.record_event("investigation.coverage_accepted", result)
    return result
