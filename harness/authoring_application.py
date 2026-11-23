"""Explicit semantic replacement with retained history and compensatable registry authority."""

from harness.authoring_context import resolve_target, validate_value
from harness.authoring_contract import AuthoringConflict
from harness.decision_workflow import modified_title
from harness.decisions import DecisionStatus
from harness.effective_records import effective_records
from harness.registry import DecisionRegistry
from harness.requirement_lineage import MODIFIED_LABEL


def apply_record(service, target, target_revision):
    from harness.authoring import TransactionInterview

    record = resolve_target(service, target, target_revision)
    if not record["contribution"] or record["status"] == "applied":
        raise AuthoringConflict("select a pending record correction")
    value = record["effective"]
    engine = TransactionInterview(service.controller, service.task_id)
    projected = effective_records(engine.ws)
    original = record["original"]
    result = {"target": target, "contribution": record["contribution"], "original": original}
    with engine.mutation_in_transaction():
        if record["kind"] == "question_wording":
            projected.require_current("question", original["id"])
            service.conn.execute(
                "UPDATE questions SET text=? WHERE id=? AND task_id=?",
                (value, original["id"], service.task_id),
            )
        else:
            validate_value(service, record["kind"], value)
            if record["kind"] == "question_response":
                projected.require_current("question", original["id"])
                selected = getattr(service, "_selected_decisions", set())
                blockers = [
                    p.id
                    for p in projected.proposals
                    if p.question_id == original["id"]
                    and p.status in {"accepted", "modified"}
                    and p.id not in selected
                    and projected.proposal_roots.get(p.id, p.id) not in selected
                ]
                if blockers:
                    raise AuthoringConflict(
                        "Correct the independently accepted recommendations "
                        "in the same application batch first: "
                        + ", ".join(f"recommendation:{ident}" for ident in blockers)
                    )
                question = engine.ws.ask(original["topic"], original["text"], kind=original["kind"])
                if value["mode"] == "answer":
                    engine.ws.answer(question.id, value["answer"])
                    engine.ws.record_event("question.answered", {"id": question.id})
                elif value["mode"] == "unknown":
                    engine.ws.mark_asked(question.id)
                    engine.ws.record_event("question.unresolved", {"id": question.id})
                else:
                    engine.ws.skip(question.id, value["ref"], value["source"])
                    engine.ws.record_event(
                        "question.skipped", {"id": question.id, "source": value["source"]}
                    )
                result["question"] = question.id
            elif record["kind"] == "recommendation_decision":
                projected.require_current("proposal", original["id"])
                qid = (
                    projected.current("question", original["question_id"])
                    if original["question_id"]
                    else None
                )
                proposal = engine.ws.propose(
                    original["title"],
                    original["recommendation"],
                    f"Explicit developer correction of P{original['id']}",
                    qid,
                    original["impact"],
                )
                proposal = engine.ws.resolve(proposal.id, value["status"], value["response"])
                if qid and proposal.status in {"accepted", "modified"}:
                    question = engine.ws.question(qid)
                    if question.status == "open":
                        engine.ws.mark_decided(qid)
                decided = {
                    "id": proposal.id,
                    "status": proposal.status,
                    "question": qid,
                    "corrects": original["id"],
                }
                if proposal.status == "modified":
                    decided[MODIFIED_LABEL] = True
                engine.ws.record_event("proposal.decided", decided)
                result["proposal"] = proposal.id
                binding = projected.registry.get(original["id"])
                if binding:
                    result["registry"] = _registry_transition(
                        service, binding, original, value, proposal.id, target
                    )
            else:
                raise ValueError("contributions cannot be applied as structured records")
        service.store.contribution("record_application", "developer", result)
        engine.ws.record_event("authoring.record_applied", result)
        effective_records(engine.ws)
    return result


def _registry_transition(service, binding, original, value, proposal_id, target):
    registry = DecisionRegistry(service.root / "docs" / "decisions")
    current = registry.get(binding["id"])
    if (
        current.version != binding["version"]
        or str(current.path) != binding["path"]
        or str(current.status) != binding["status"]
    ):
        raise AuthoringConflict(
            "Registry identity/version/status changed; review current decision before applying"
        )
    status = DecisionStatus.REJECTED if value["status"] == "rejected" else DecisionStatus.APPROVED
    content = (
        (value["response"] or "Developer withdrew this decision.")
        if status is DecisionStatus.REJECTED
        else (value["response"] if value["status"] == "modified" else original["recommendation"])
    )
    title = original["title"]
    if value["status"] == "modified":
        title = modified_title(title)
    record = registry.transition(
        current.id,
        title,
        content,
        f"Explicit developer correction of {current.id} v{current.version} through {target}",
        current.scope,
        original["impact"] or current.impact,
        expected_version=current.version,
        status=status,
        expected_status=current.status,
        mutations=service._registry_mutations,
    )
    result = {
        "id": record.id,
        "version": record.version,
        "path": str(record.path),
        "status": str(record.status),
    }
    service.controller.workspace(service.task_id).record_event(
        "decision.superseded",
        result | {"from_version": current.version, "proposal": proposal_id, "target": target},
    )
    return result
