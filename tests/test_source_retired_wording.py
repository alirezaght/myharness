"""The direct wording operation must honor source retirement on every public path."""

import json
from dataclasses import asdict

import pytest

from harness.authoring import AuthoringService
from harness.dashboard.authoring_actions import authoring_action
from harness.investigation_store import InvestigationStore
from tests.planning_session_fixture import session as session
from tests.test_investigation_review_regressions import source_publication


def seeded(session, *, corrected=False, retire=True):
    bridge, receipt, caller, cap = session
    bridge._test_caller, bridge._test_cap = caller, cap
    source_publication(bridge, receipt, "wording-original-source")
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    store = InvestigationStore(bridge.controller, bridge.project, bridge.task_id)
    original = store.decisions()[0]["question_id"]
    service.answer(
        expected=service.store.base(),
        request_id="wording-original-answer",
        question=original,
        mode="answer",
        answer="Preserve existing CSV behavior exactly",
    )
    predecessor = original
    if corrected:
        from harness.interview import InterviewEngine
        from tests.test_authoring import rework1b_apply

        rework1b_apply(
            service,
            f"question-response:{original}",
            {"mode": "unknown", "answer": "", "source": None, "ref": None},
            suffix="wording-corrected",
        )
        predecessor = (
            InterviewEngine(bridge.controller, bridge.task_id)
            .effective()
            .current("question", original)
        )
    if retire:
        source_publication(
            bridge,
            receipt,
            "wording-changed-source",
            premise="Changed CSV premise",
            predecessor=predecessor,
        )
    return service, predecessor, store.decisions()[0]["question_id"]


def database(service):
    return tuple(service.conn.iterdump())


def wording(service, question, request_id="wording-operation", text="Clear current wording"):
    return dict(
        expected=service.store.base(),
        request_id=request_id,
        operation={"op": "question-wording", "question_id": question, "text": text},
    )


@pytest.mark.parametrize("corrected", [False, True])
@pytest.mark.parametrize("route", ["service", "http"])
def test_source_retired_wording_refused_atomically(session, db_path, corrected, route):
    service, retired, current = seeded(session, corrected=corrected)
    before = database(service)
    args = wording(service, retired)
    if route == "http":
        status, response = authoring_action(
            db_path,
            service.root,
            json.dumps({"task": service.task_id, **args}).encode(),
            "planning",
        )
        assert status == 409, response
    else:
        with pytest.raises(ValueError, match="source premise"):
            service.planning(**args)
    assert database(service) == before
    assert retired != current


def test_reconciliation_with_retired_wording_rolls_back_all_material_and_actions(session):
    service, retired, current = seeded(session)
    service.followup(
        expected=service.store.base(),
        request_id="wording-material-followup",
        body="Preserve exact archived-account permission behavior in the specification",
    )
    selected = service.store.binding()["working_id"]
    preview = service.reconcile_preview(selected)
    pending = preview["material"]["pending_material_ids"]
    assert pending
    before = database(service)
    with pytest.raises(ValueError, match="source premise"):
        service.reconcile(
            expected=service.store.base(),
            request_id="wording-reconcile-refused",
            revision=selected,
            token=preview["token"],
            confirm_graph=True,
            acknowledge=True,
            actions=[
                {"op": "question-wording", "question_id": current, "text": "Earlier valid action"},
                {"op": "question-wording", "question_id": retired, "text": "Forbidden old wording"},
            ],
            handling=[
                {
                    "input_id": ident,
                    "disposition": "no_change",
                    "rationale": "The current selected content preserves this existing behavior.",
                    "references": ["revision:" + selected],
                }
                for ident in pending
            ],
        )
    assert database(service) == before
    assert service.reconcile_preview(selected)["token"] == preview["token"]


@pytest.mark.parametrize("answered", [False, True])
def test_current_wording_preserves_response_and_provenance(session, answered):
    service, _, current = seeded(session)
    if answered:
        service.answer(
            expected=service.store.base(),
            request_id="wording-current-answer",
            question=current,
            mode="answer",
            answer="Keep the exact saved current response",
        )
    ws = service.controller.workspace(service.task_id)
    before = asdict(ws.question(current))
    result = service.planning(**wording(service, current))
    after = asdict(ws.question(current))
    assert after == before | {"text": "Clear current wording"}
    row = service.conn.execute(
        "SELECT payload_json FROM authoring_contributions "
        "WHERE task_id=? AND kind='wording_applied' ORDER BY rowid DESC LIMIT 1",
        (service.task_id,),
    ).fetchone()
    assert json.loads(row[0]) == {"original": before, "text": "Clear current wording"}
    event = [e for e in ws.events() if e.kind == "question.wording_revised"][-1]
    assert event.payload == {
        "id": current,
        "original": before["text"],
        "text": "Clear current wording",
    }
    assert result["base"] == service.store.base()


def test_committed_wording_replays_after_source_retirement_without_reapplying(session):
    service, original, _ = seeded(session, retire=False)
    args = wording(service, original, "wording-before-retirement")
    result = service.planning(**args)
    bridge, receipt, _, _ = session
    source_publication(
        bridge,
        receipt,
        "wording-after-committed-operation",
        premise="Later source premise",
        predecessor=original,
    )
    before = database(service)
    assert service.planning(**args) == result
    assert database(service) == before
    with pytest.raises(ValueError):
        service.planning(
            **(args | {"operation": args["operation"] | {"text": "Conflicting reuse"}})
        )
    with pytest.raises(ValueError, match="source premise"):
        service.planning(**wording(service, original, "new-wording-after-retirement"))
    assert database(service) == before


@pytest.mark.parametrize("case", ["missing", "foreign", "developer_retired", "phase", "base"])
def test_existing_wording_refusals_remain_atomic(session, case):
    from harness.db import transaction

    service, original, current = seeded(
        session, corrected=case == "developer_retired", retire=False
    )
    target = original
    if case == "developer_retired":
        target = InvestigationStore(
            service.controller, service.project, service.task_id
        ).decisions()[0]["original_question_id"]
    if case == "missing":
        target = 999999
    elif case == "foreign":
        foreign = service.controller.create_task("Another task")
        with transaction(service.conn):
            target = (
                service.controller.workspace(foreign.id).ask("business_requirements", "Foreign?").id
            )
    elif case == "phase":
        from harness.planning_session_events import mutation_scope

        with transaction(service.conn), mutation_scope(service.conn, service.task_id, "system"):
            service.conn.execute("UPDATE tasks SET phase='design' WHERE id=?", (service.task_id,))
    args = wording(service, target)
    if case == "base":
        args["expected"] = args["expected"] | {"generation": args["expected"]["generation"] - 1}
    before = database(service)
    with pytest.raises((ValueError, LookupError)):
        service.planning(**args)
    assert database(service) == before


def test_active_execution_still_refuses_wording(controller, sample_repo):
    from tests.test_investigation_jobs import _approved_source_service, _execution_record

    service, approval = _approved_source_service(controller, sample_repo)
    question = service.controller.workspace(service.task_id).questions()[0]
    _execution_record(service, approval, status="Running", attempt="Running")
    before = database(service)
    with pytest.raises(ValueError, match="execution"):
        service.planning(**wording(service, question.id))
    assert database(service) == before
