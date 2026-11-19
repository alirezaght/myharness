import pytest

from harness.authoring import AuthoringService
from harness.authoring_contract import AuthoringConflict, digest
from harness.db import transaction
from harness.phases import Phase


def initialized(controller, tmp_path):
    task = controller.create_task("Build a scheduler", "trivial")
    service = AuthoringService(controller, tmp_path, task.id)
    result = service.start(request_id="resume-1234", host="codex", expected=service.store.base())
    if result.get("job"):
        service.cancel_job(
            expected=service.store.base(), request_id="cancel-initial", job=result["job"]
        )
    return service


def test_saved_body_replay_history_and_source_conflict(controller, tmp_path):
    service = initialized(controller, tmp_path)
    base = service.store.base()
    first = service.save_spec(expected=base, request_id="save-12345", body="## Exact\nMy draft\n")
    assert (
        service.save_spec(expected=base, request_id="save-12345", body="## Exact\nMy draft\n")
        == first
    )
    with pytest.raises(AuthoringConflict):
        service.save_spec(expected=base, request_id="save-67890", body="late")
    assert service.snapshot()["working"]["body"] == "## Exact\nMy draft\n"
    service.followup(expected=service.store.base(), request_id="follow-1234", body="Keep my words")
    contribution = service.snapshot()["contributions"][-1]
    service.revise(
        expected=service.store.base(),
        request_id="revise-1234",
        target=contribution["id"],
        target_revision=contribution["id"],
        body="Revised words",
    )
    snapshot = service.snapshot()
    assert snapshot["contributions"][-1]["supersedes"] == contribution["id"]
    assert snapshot["contributions"][0]["payload"]["body"] == "Keep my words"


def test_question_revisions_after_phase_never_mutate_structured_decision(controller, tmp_path):
    service = initialized(controller, tmp_path)
    with transaction(controller.conn):
        ws = controller.workspace(service.task_id)
        q = ws.ask("purpose", "Original wording")
        controller._move(service.task_id, Phase.INVESTIGATION, Phase.INTERVIEW, None)
        controller._move(service.task_id, Phase.INTERVIEW, Phase.DESIGN, None)
    service.revise(
        expected=service.store.base(),
        request_id="revise-q1234",
        target=f"question:{q.id}",
        target_revision=digest(__import__("dataclasses").asdict(q)),
        body="New wording",
    )
    assert ws.question(q.id).text == "Original wording"
    assert service.snapshot()["working"]["obligations"]


def test_explicit_planning_actions_apply_revised_question_with_history(controller, tmp_path):
    service = initialized(controller, tmp_path)
    ws = controller.workspace(service.task_id)
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"decisions": []})
    service.planning(
        expected=service.store.base(), request_id="advance-1234", operation={"op": "advance"}
    )
    service.planning(
        expected=service.store.base(),
        request_id="ask-12345678",
        operation={"op": "question-ask", "topic": "custom", "text": "Original?", "decision": False},
    )
    question = ws.questions()[-1]
    service.planning(
        expected=service.store.base(),
        request_id="wording-1234",
        operation={"op": "question-wording", "question_id": question.id, "text": "Revised?"},
    )
    assert ws.question(question.id).text == "Revised?"
    assert service.snapshot()["contributions"][-1]["payload"]["original"]["text"] == "Original?"
    service.planning(
        expected=service.store.base(),
        request_id="answer-12345",
        operation={"op": "question-answer", "question_id": question.id, "text": "Explicit answer"},
    )
    assert ws.question(question.id).answer == "Explicit answer"


def test_decision_file_compensates_if_outer_authoring_transaction_fails(
    controller, tmp_path, monkeypatch
):
    from harness.interview import InterviewEngine

    service = initialized(controller, tmp_path)
    ws = controller.workspace(service.task_id)
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"decisions": []})
    controller.advance(service.task_id)
    engine = InterviewEngine(controller, service.task_id)
    question = engine.ask("storage", "Which storage?", decision=True)
    proposal = engine.propose(question.id, "Storage", "SQLite", "Simple local persistence")
    original = service.store.contribution

    def fail(kind, *args, **kwargs):
        if kind == "decision":
            raise RuntimeError("injected after registry write")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(service.store, "contribution", fail)
    with pytest.raises(RuntimeError):
        service.decide(
            expected=service.store.base(),
            request_id="decide-12345",
            proposal=proposal.id,
            status="accepted",
            response="",
        )
    assert ws.proposal(proposal.id).status == "pending"
    assert not list((tmp_path / "docs/decisions").glob("DEC-*.md"))


def legacy_records(controller, tmp_path):
    task = controller.create_task("Preserve legacy answers", "trivial")
    ws = controller.workspace(task.id)
    with transaction(controller.conn):
        q = ws.ask("legacy", "Original question")
        ws.answer(q.id, "Original answer")
        deferred = ws.ask("deferred", "Deferred question")
        ws.skip(deferred.id, "Inspect source", "codebase")
        unknown = ws.ask("unknown", "Unresolved question")
        ws.mark_asked(unknown.id)
        ws.record_event("question.unresolved", {"id": unknown.id})
        proposal = ws.propose("Storage", "SQLite", "Local", q.id)
        ws.resolve(proposal.id, "accepted", "Original decision")
        controller._move(task.id, Phase.INVESTIGATION, Phase.INTERVIEW, None)
        controller._move(task.id, Phase.INTERVIEW, Phase.DESIGN, None)
    return AuthoringService(controller, tmp_path, task.id), q, deferred, unknown, proposal


def test_rework1_legacy_typed_revisions_preserve_originals_and_tokens(controller, tmp_path):
    service, q, deferred, unknown, proposal = legacy_records(controller, tmp_path)
    snap = service.snapshot()
    targets = {row["target"]: row for row in snap.get("revision_targets", [])}
    assert f"question-response:{q.id}" in targets, "legacy answers need editable targets"
    assert f"question-response:{deferred.id}" in targets
    assert f"question-response:{unknown.id}" in targets
    target = targets[f"question-response:{q.id}"]
    assert target["original"]["answer"] == "Original answer"
    assert target["contribution"] is None
    value = {"mode": "answer", "answer": "Corrected answer", "source": None, "ref": None}
    result = service.revise_record(
        expected=service.store.base(),
        request_id="legacy-revise-1",
        target=target["target"],
        target_revision=target["revision"],
        value=value,
    )
    assert service.controller.workspace(service.task_id).question(q.id).answer == "Original answer"
    current = next(
        row for row in service.snapshot()["revision_targets"] if row["target"] == target["target"]
    )
    assert current["effective"] == value and current["status"] == "pending"
    with pytest.raises(AuthoringConflict):
        service.revise_record(
            expected=service.store.base(),
            request_id="legacy-revise-2",
            target=target["target"],
            target_revision=target["revision"],
            value=value,
        )
    assert result["contribution"] == current["contribution"]
    decision = targets[f"recommendation:{proposal.id}"]
    service.revise_record(
        expected=service.store.base(),
        request_id="legacy-decide-1",
        target=decision["target"],
        target_revision=decision["revision"],
        value={"status": "modified", "response": "Use a file instead"},
    )
    assert service.controller.workspace(service.task_id).proposal(proposal.id).status == "accepted"


def test_rework1_manual_save_start_and_terminal_retry_are_truthful(controller, tmp_path):
    task = controller.create_task("Manual first", "trivial")
    service = AuthoringService(controller, tmp_path, task.id)
    service.save_spec(
        expected=service.store.base(), request_id="manual-save-1234", body="## My exact body\n"
    )
    first = service.start(
        expected=service.store.base(), request_id="manual-start-1234", host="codex"
    )
    assert first.get("job"), "initialized manual draft must still investigate"
    again = service.start(
        expected=service.store.base(), request_id="manual-start-5678", host="codex"
    )
    assert again["job"] == first["job"]
    assert len(service.snapshot()["jobs"]) == 1
    service.cancel_job(
        expected=service.store.base(), request_id="manual-cancel-1234", job=first["job"]
    )
    ended = service.start(
        expected=service.store.base(), request_id="manual-start-9012", host="codex"
    )
    assert (
        ended["job"] == first["job"] and ended["state"] == "cancelled" and ended["retry_required"]
    )
    assert service.snapshot()["working"]["body"] == "## My exact body\n"


@pytest.mark.parametrize(
    "mode,source,ref",
    [("unknown", None, None), ("defer", "codebase", "Inspect source"), ("answer", None, None)],
)
def test_rework1_response_modes_supersession_and_full_targets(
    controller, tmp_path, mode, source, ref
):
    service, q, deferred, unknown, _ = legacy_records(controller, tmp_path)
    question_id = deferred.id if mode == "defer" else unknown.id if mode == "unknown" else q.id
    ident = f"question-response:{question_id}"
    target = next(row for row in service.snapshot()["revision_targets"] if row["target"] == ident)
    value = {
        "mode": mode,
        "answer": "New answer" if mode == "answer" else "",
        "source": source,
        "ref": ref,
    }
    first = service.revise_record(
        expected=service.store.base(),
        request_id="typed-first-1234",
        target=ident,
        target_revision=target["revision"],
        value=value,
    )
    with transaction(controller.conn):
        for _ in range(205):
            service.store.contribution("assistant", "model", {"text": "Later conversation"})
    current = next(row for row in service.snapshot()["revision_targets"] if row["target"] == ident)
    assert current["revision"] == first["contribution"] and current["effective"] == value
    second = service.revise_record(
        expected=service.store.base(),
        request_id="typed-second-1234",
        target=ident,
        target_revision=current["revision"],
        value={"mode": "unknown", "answer": "", "source": None, "ref": None},
    )
    latest = next(row for row in service.snapshot()["revision_targets"] if row["target"] == ident)
    assert latest["lineage"] == [first["contribution"], second["contribution"]]
    assert latest["original"] == target["original"]


def test_rework1_record_application_creates_superseding_domain_decision(controller, tmp_path):
    service, question, _, _, proposal = legacy_records(controller, tmp_path)
    target = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"recommendation:{proposal.id}"
    )
    result = service.revise_record(
        expected=service.store.base(),
        request_id="application-revise-1234",
        target=target["target"],
        target_revision=target["revision"],
        value={"status": "modified", "response": "Use a file instead"},
    )
    with transaction(controller.conn):
        controller.workspace(service.task_id).checkpoint("investigation", {"decisions": []})
    service.planning(
        expected=service.store.base(),
        request_id="application-back-1234",
        operation={"op": "back", "phase": "interview", "reason": "Review explicit correction"},
    )
    service.planning(
        expected=service.store.base(),
        request_id="application-apply-1234",
        operation={
            "op": "revision-apply",
            "target": target["target"],
            "target_revision": result["contribution"],
        },
    )
    ws = controller.workspace(service.task_id)
    assert ws.proposal(proposal.id).status == "accepted"
    assert ws.question(question.id).answer == "Original answer"
    corrected = ws.proposals()[-1]
    assert (
        corrected.id != proposal.id
        and corrected.status == "modified"
        and corrected.response == "Use a file instead"
    )
    current = next(
        row for row in service.snapshot()["revision_targets"] if row["target"] == target["target"]
    )
    assert current["status"] == "applied" and current["applied"]["proposal"] == corrected.id
    assert current["applied"]["original"]["id"] == proposal.id


def test_rework1_revision_validation_and_foreign_target_are_atomic(controller, tmp_path):
    service, question, _, _, proposal = legacy_records(controller, tmp_path)
    targets = {row["target"]: row for row in service.snapshot()["revision_targets"]}
    target = targets[f"question-response:{question.id}"]
    bad_values = [
        {"mode": "defer", "answer": "", "source": "invented", "ref": "reason"},
        {"mode": "answer", "answer": "unknown", "source": None, "ref": None},
        {"mode": "unknown", "answer": "", "source": "codebase", "ref": "reason"},
        {"mode": "defer", "answer": "", "source": "decision", "ref": "DEC-missing"},
        {"mode": "answer", "answer": "Valid", "source": None, "ref": None, "extra": 1},
        {"status": "accepted", "response": ""},
    ]
    before = service.store.base()
    for value in bad_values:
        with pytest.raises(ValueError):
            service.revise_record(
                expected=before,
                request_id="invalid-value-1234",
                target=target["target"],
                target_revision=target["revision"],
                value=value,
            )
        assert service.store.base() == before
    decision = targets[f"recommendation:{proposal.id}"]
    with pytest.raises(ValueError):
        service.revise_record(
            expected=before,
            request_id="invalid-modified-1234",
            target=decision["target"],
            target_revision=decision["revision"],
            value={"status": "modified", "response": ""},
        )
    foreign_task = controller.create_task("Foreign task")
    with transaction(controller.conn):
        foreign = controller.workspace(foreign_task.id).ask("foreign", "Foreign question")
    with pytest.raises(LookupError):
        service.revise_record(
            expected=before,
            request_id="invalid-foreign-1234",
            target=f"question-response:{foreign.id}",
            target_revision="foreign-token",
            value=bad_values[0],
        )
    assert service.store.base() == before
    assert not controller.conn.execute(
        "SELECT 1 FROM authoring_contributions WHERE task_id=?", (service.task_id,)
    ).fetchone()


def test_rework1_start_stale_job_exposes_retry_and_continuation_is_once(controller, tmp_path):
    service = initialized(controller, tmp_path)
    # The initial cancelled investigation stays explicit; start is not an automatic retry.
    state = service.start(
        expected=service.store.base(), request_id="resume-cancelled-1234", host="codex"
    )
    assert state["state"] == "cancelled" and state["retry_required"]
    service.retry_job(
        expected=service.store.base(),
        request_id="retry-start-1234",
        job=state["job"],
        acknowledge_unknown=False,
    )
    service.save_spec(
        expected=service.store.base(),
        request_id="stale-save-1234",
        body="## Preserve manually edited text\n",
    )
    stale = service.start(
        expected=service.store.base(), request_id="stale-resume-1234", host="codex"
    )
    assert stale["state"] == "stale" and stale["retry_required"]
    with transaction(controller.conn):
        controller.workspace(service.task_id).checkpoint("investigation", {"decisions": []})
    first = service.start(
        expected=service.store.base(), request_id="continue-first-1234", host="codex"
    )
    second = service.start(
        expected=service.store.base(), request_id="continue-again-1234", host="codex"
    )
    assert first["job"] == second["job"] and first["state"] == second["state"] == "queued"
    assert service.snapshot()["working"]["body"] == "## Preserve manually edited text\n"


def test_rework1_resume_rejects_host_substitution_atomically(controller, tmp_path):
    task = controller.create_task("Host identity", "trivial")
    service = AuthoringService(controller, tmp_path, task.id)
    first = service.start(expected=service.store.base(), request_id="host-first-1234", host="codex")
    before = service.snapshot()
    with pytest.raises(AuthoringConflict, match="uses codex"):
        service.start(expected=before["base"], request_id="host-change-1234", host="claude")
    assert service.snapshot() == before
    again = service.start(expected=before["base"], request_id="host-repeat-1234", host="codex")
    assert again["job"] == first["job"] and again["host"] == "codex"


def test_rework1_existing_wording_application_has_truthful_target_status(controller, tmp_path):
    service = initialized(controller, tmp_path)
    ws = controller.workspace(service.task_id)
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"decisions": []})
    service.planning(
        expected=service.store.base(), request_id="word-status-advance", operation={"op": "advance"}
    )
    question = ws.questions()[0]
    target = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"question:{question.id}"
    )
    service.revise(
        expected=service.store.base(),
        request_id="word-status-revise",
        target=target["target"],
        target_revision=target["revision"],
        body="Explicit new wording?",
    )
    service.planning(
        expected=service.store.base(),
        request_id="word-status-apply",
        operation={
            "op": "question-wording",
            "question_id": question.id,
            "text": "Explicit new wording?",
        },
    )
    current = next(
        row for row in service.snapshot()["revision_targets"] if row["target"] == target["target"]
    )
    assert (
        current["status"] == "applied" and current["applied"]["original"]["text"] == question.text
    )


def rework1b_service(controller, tmp_path, *, decision=False, status="accepted"):
    from harness.interview import InterviewEngine
    from harness.registry import DecisionRegistry

    task = controller.create_task("Correct effective requirements", "trivial")
    ws = controller.workspace(task.id)
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"decisions": []})
    controller.advance(task.id)
    registry = DecisionRegistry(tmp_path / "docs/decisions")
    engine = InterviewEngine(controller, task.id, registry)
    question = engine.ask("storage", "Which persistence?", decision=decision)
    proposal = None
    if decision:
        proposal = engine.propose(question.id, "Persistence", "OLD SQLite", "Original rationale")
        engine.decide(proposal.id, status, "OLD modified SQLite" if status == "modified" else "")
    else:
        engine.answer(question.id, "OLD plain answer")
    service = AuthoringService(controller, tmp_path, task.id)
    service.save_spec(
        expected=service.store.base(), request_id="rework1b-seed-body", body="## Working body\n"
    )
    return service, engine, registry, question, proposal


def rework1b_apply(service, target, value, *, suffix="first"):
    record = next(row for row in service.snapshot()["revision_targets"] if row["target"] == target)
    revised = service.revise_record(
        expected=service.store.base(),
        request_id="rework1b-revise-" + suffix,
        target=target,
        target_revision=record["revision"],
        value=value,
    )
    operation = {
        "op": "revision-apply",
        "target": target,
        "target_revision": revised["contribution"],
    }
    service._rework1b_application_base = service.store.base()
    result = service.planning(
        expected=service._rework1b_application_base,
        request_id="rework1b-apply-" + suffix,
        operation=operation,
    )
    return result


@pytest.mark.parametrize("mode", ["answer", "unknown", "defer"])
def test_rework1b_applied_response_has_one_effective_requirement(controller, tmp_path, mode):
    from harness.authoring_jobs import immutable_input
    from harness.dashboard.workspace_view import source_snapshot

    service, engine, _, original, _ = rework1b_service(controller, tmp_path)
    rework1b_apply(
        service,
        f"question-response:{original.id}",
        {
            "mode": mode,
            "answer": "NEW effective answer" if mode == "answer" else "",
            "source": "codebase" if mode == "defer" else None,
            "ref": "NEW inspect code" if mode == "defer" else None,
        },
    )
    statements = [row.statement for row in engine.summary().requirements]
    assert "OLD plain answer" not in statements, "retired answer remains an active requirement"
    assert ("NEW effective answer" in statements) == (mode == "answer")
    assert engine.ws.question(original.id).answer == "OLD plain answer"

    assert "OLD plain answer" not in service.manager().structured_body()
    workspace = source_snapshot(
        controller, controller.get_task(service.task_id), service.manager().store, None
    )
    assert not any(row["text"] == "OLD plain answer" for row in workspace["requirements"])
    with transaction(controller.conn):
        context = immutable_input(service)
    assert not any(row["answer"] == "OLD plain answer" for row in context["questions"])
    if mode == "unknown":
        assert any(item.text == original.text for item in engine.checklist().items)
    if mode == "defer":
        assert any(
            item.skip_reason == "NEW inspect code" for item in engine.summary().answered_elsewhere
        )


@pytest.mark.parametrize("initial", ["accepted", "modified"])
def test_rework1b_corrected_decision_is_effective_without_duplicate_registry_authority(
    controller, tmp_path, initial
):
    service, engine, registry, question, proposal = rework1b_service(
        controller, tmp_path, decision=True, status=initial
    )
    ident = registry.records()[0].id
    rework1b_apply(
        service,
        f"recommendation:{proposal.id}",
        {"status": "modified", "response": "NEW file storage"},
    )
    statements = [row.statement for row in engine.summary().requirements]
    assert statements == ["NEW file storage"], "old recommendation still active after correction"
    assert [row.id for row in registry.records()] == [ident]
    assert registry.get(ident).version == 2
    assert engine.ws.proposal(proposal.id).status == initial
    assert "OLD SQLite" not in service.manager().structured_body()


def test_rework1b_repeated_corrections_retire_targets_and_replay_once(controller, tmp_path):
    from harness.effective_records import effective_records

    service, engine, registry, _, proposal = rework1b_service(controller, tmp_path, decision=True)
    first_target = f"recommendation:{proposal.id}"
    rework1b_apply(service, first_target, {"status": "modified", "response": "B file storage"})
    current = next(
        row for row in service.snapshot()["revision_targets"] if row["target"] == first_target
    )
    assert not current["editable"] and current["current_target"] != first_target
    base = service.store.base()
    with pytest.raises(AuthoringConflict) as retired:
        service.revise_record(
            expected=base,
            request_id="rework1b-retired-revise",
            target=first_target,
            target_revision=current["revision"],
            value={"status": "accepted", "response": ""},
        )
    assert retired.value.current_target == current["current_target"]
    replayed = service.planning(
        expected=service._rework1b_application_base,
        request_id="rework1b-apply-first",
        operation={
            "op": "revision-apply",
            "target": first_target,
            "target_revision": current["contribution"],
        },
    )
    assert replayed["task"] == service.task_id
    rework1b_apply(
        service,
        current["current_target"],
        {"status": "rejected", "response": "C withdrawn"},
        suffix="second",
    )
    projection = effective_records(engine.ws)
    assert len([p for p in projection.proposals if p.title == "Persistence"]) == 1
    assert not engine.summary().requirements
    [record] = registry.records()
    assert record.version == 3 and record.status == "rejected"
    assert [row.version for row in registry.chain(record.id)] == [1, 2, 3]
    latest = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == current["current_target"]
    )
    rework1b_apply(
        service,
        latest["current_target"],
        {"status": "modified", "response": "D approved memory"},
        suffix="third",
    )
    assert [r.statement for r in engine.summary().requirements] == ["D approved memory"]
    assert registry.get(record.id).version == 4 and registry.get(record.id).status == "approved"


def test_rework1b_independent_answer_and_decision_require_explicit_batch(controller, tmp_path):
    service, engine, _, question, proposal = rework1b_service(controller, tmp_path, decision=True)
    with transaction(controller.conn):
        engine.ws.answer(question.id, "Independent user requirement")
    rework1b_apply(
        service,
        f"recommendation:{proposal.id}",
        {"status": "modified", "response": "New separate decision"},
    )
    assert {r.statement for r in engine.summary().requirements} == {
        "Independent user requirement",
        "New separate decision",
    }
    response = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"question-response:{question.id}"
    )
    revised = service.revise_record(
        expected=service.store.base(),
        request_id="rework1b-independent-answer",
        target=response["target"],
        target_revision=response["revision"],
        value={"mode": "unknown", "answer": "", "source": None, "ref": None},
    )
    operation = {
        "op": "revision-apply",
        "target": response["target"],
        "target_revision": revised["contribution"],
    }
    base = service.store.base()
    with pytest.raises(AuthoringConflict, match="independently accepted"):
        service.planning(
            expected=base, request_id="rework1b-independent-refusal", operation=operation
        )
    assert service.store.base() == base
    decision = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["kind"] == "recommendation_decision" and row["editable"]
    )
    correction = service.revise_record(
        expected=base,
        request_id="rework1b-independent-withdraw",
        target=decision["target"],
        target_revision=decision["revision"],
        value={"status": "rejected", "response": "Withdraw alongside answer"},
    )
    revision = service.store.base()["working"]
    preview = service.reconcile_preview(revision)
    service.reconcile(
        expected=service.store.base(),
        request_id="rework1b-independent-batch",
        revision=revision,
        token=preview["token"],
        confirm_graph=True,
        acknowledge=True,
        actions=[
            operation,
            {
                "op": "revision-apply",
                "target": decision["target"],
                "target_revision": correction["contribution"],
            },
        ],
    )
    assert not engine.summary().requirements
    assert engine.ws.question(question.id).answer == "Independent user requirement"


def test_rework1b_catalogue_and_review_package_do_not_resurrect_retired_records(
    controller, tmp_path
):
    from harness.review_package import accept, package

    service, engine, _, _, _ = rework1b_service(controller, tmp_path)
    question = next(q for q in engine.effective_questions() if q.catalogue_key)
    engine.answer(question.id, "OLD catalogue answer")
    rework1b_apply(
        service,
        f"question-response:{question.id}",
        {"mode": "answer", "answer": "NEW catalogue answer", "source": None, "ref": None},
    )
    current = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"question-response:{question.id}"
    )
    successor = int(current["current_target"].split(":")[1])
    assert (
        next(q for q in engine.effective_questions() if q.id == successor).catalogue_key
        == question.catalogue_key
    )
    for _ in range(2):
        engine.next()
    assert len([q for q in engine.ws.questions() if q.catalogue_key == question.catalogue_key]) == 1
    assert not any(item.question_id == question.id for item in engine.checklist().items)
    shown = package(controller, service.task_id)
    assert question.id not in {q["id"] for q in shown["content"]["questions"]}
    assert "OLD catalogue answer" not in shown["content"]["proposed_spec"]
    accept(
        controller,
        service.task_id,
        shown["sha256"],
        source="Explicit developer approves this current package",
    )
    assert engine.ws.question(question.id).answer == "OLD catalogue answer"


def test_rework1b_unbacked_recommendation_correction_stays_unbacked(controller, tmp_path):
    service, engine, registry, question, _ = rework1b_service(controller, tmp_path)
    with transaction(controller.conn):
        proposal = engine.ws.propose("Unbacked", "OLD suggestion", "Reason", question.id)
        engine.ws.resolve(proposal.id, "accepted", "")
    rework1b_apply(
        service, f"recommendation:{proposal.id}", {"status": "modified", "response": "NEW unbacked"}
    )
    assert registry.records() == []
    assert "OLD suggestion" not in {r.statement for r in engine.summary().requirements}
    assert "OLD plain answer" in {r.statement for r in engine.summary().requirements}


def test_rework1b_malformed_and_branching_application_links_fail_visibly(controller, tmp_path):
    from harness.effective_records import effective_records

    service, engine, _, original, _ = rework1b_service(controller, tmp_path)
    rework1b_apply(
        service,
        f"question-response:{original.id}",
        {"mode": "answer", "answer": "NEW single successor", "source": None, "ref": None},
    )
    event = next(e for e in engine.ws.events() if e.kind == "authoring.record_applied")
    with pytest.raises(ValueError, match="Branching"), transaction(controller.conn):
        engine.ws.record_event("authoring.record_applied", event.payload)
        effective_records(engine.ws)
    with pytest.raises(ValueError, match="Malformed"), transaction(controller.conn):
        engine.ws.record_event("authoring.record_applied", {"target": "question-response:1"})
        effective_records(engine.ws)
    assert [r.statement for r in engine.summary().requirements] == ["NEW single successor"]


def test_rework1b_active_execution_refuses_effective_application(controller, tmp_path, monkeypatch):
    service, _, registry, _, proposal = rework1b_service(controller, tmp_path, decision=True)
    target = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"recommendation:{proposal.id}"
    )
    revised = service.revise_record(
        expected=service.store.base(),
        request_id="rework1b-active-revise",
        target=target["target"],
        target_revision=target["revision"],
        value={"status": "rejected", "response": "Pending withdrawal"},
    )
    monkeypatch.setattr(
        "harness.execution_store.latest_execution",
        lambda *args: {"id": "active", "status": "Running"},
    )
    before = service.store.base()
    with pytest.raises(AuthoringConflict, match="active or unsettled"):
        service.planning(
            expected=before,
            request_id="rework1b-active-apply",
            operation={
                "op": "revision-apply",
                "target": target["target"],
                "target_revision": revised["contribution"],
            },
        )
    assert service.store.base() == before
    assert registry.records()[0].version == 1 and registry.records()[0].status == "approved"


@pytest.mark.parametrize("action", ["unknown", "skip", "propose"])
def test_rework1b_withdrawn_recommendation_allows_current_question_actions(
    controller, tmp_path, action
):
    service, engine, _, question, proposal = rework1b_service(controller, tmp_path, decision=True)
    rework1b_apply(
        service, f"recommendation:{proposal.id}", {"status": "rejected", "response": "Withdraw"}
    )
    assert next(q for q in engine.effective_questions() if q.id == question.id).status == "open"
    if action == "unknown":
        engine.unknown(question.id)
        assert engine.ws.events()[-1].kind == "question.unresolved"
    elif action == "skip":
        engine.skip(question.id, "codebase", "Inspect persistence")
        assert engine.ws.question(question.id).skip_reason == "Inspect persistence"
    else:
        replacement = engine.propose(question.id, "New storage", "Files", "Developer revisits")
        assert replacement.question_id == question.id and replacement.status == "pending"
    assert engine.ws.proposal(proposal.id).status == "accepted"


def test_rework1b_deciding_remapped_pending_proposal_settles_current_question(controller, tmp_path):
    service, engine, _, original, _ = rework1b_service(controller, tmp_path)
    question = engine.ask("storage", "Which current storage?")
    proposal = engine.propose(question.id, "Storage", "Files", "Explicit option")
    before = engine.ws.question(question.id)
    rework1b_apply(
        service,
        f"question-response:{question.id}",
        {"mode": "unknown", "answer": "", "source": None, "ref": None},
    )
    current_id = engine.effective().current("question", question.id)
    service.decide(
        expected=service.store.base(),
        request_id="rework1b-current-decision",
        proposal=proposal.id,
        status="accepted",
        response="",
    )
    assert engine.ws.question(question.id) == before
    assert engine.ws.question(current_id).status == "answered"
    assert (
        next(p for p in engine.effective_proposals() if p.id == proposal.id).question_id
        == current_id
    )
    assert not any(item.question_id == current_id for item in engine.checklist().items)
    assert engine.ws.question(original.id).answer == "OLD plain answer"


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("action", ["unknown", "skip", "propose", "decide"])
@pytest.mark.parametrize("boundary", ["engine", "http"])
def test_rework1b_missing_or_foreign_current_records_keep_safe_refusal(
    controller, tmp_path, db_path, foreign, action, boundary
):
    import json

    from harness.dashboard.authoring_actions import authoring_action
    from harness.errors import RecordNotFound

    service, engine, _, _, _ = rework1b_service(controller, tmp_path)
    other = controller.create_task("Foreign record owner", "trivial")
    with transaction(controller.conn):
        ws = controller.workspace(other.id)
        question = ws.ask("storage", "Foreign storage?")
        proposal = ws.propose("Foreign", "Files", "Foreign reason", question.id)
    qid, pid = (question.id, proposal.id) if foreign else (999999, 999999)
    operations = {
        "unknown": {"op": "question-unknown", "question_id": qid},
        "skip": {"op": "question-defer", "question_id": qid, "source": "codebase", "ref": "Read"},
        "propose": {
            "op": "interview-propose",
            "question_id": qid,
            "title": "Files",
            "recommendation": "Files",
            "rationale": "Reason",
            "impact": None,
        },
    }
    calls = {
        "unknown": lambda: engine.unknown(qid),
        "skip": lambda: engine.skip(qid, "codebase", "Read"),
        "propose": lambda: engine.propose(qid, "Files", "Files", "Reason"),
        "decide": lambda: engine.decide(pid, "accepted", ""),
    }
    before = service.snapshot()
    if boundary == "engine":
        with pytest.raises(RecordNotFound):
            calls[action]()
        assert service.snapshot() == before
        return
    payload = {
        "task": service.task_id,
        "expected": before["base"],
        "request_id": "rework1b-missing-" + action,
    }
    if action == "decide":
        payload.update(proposal=pid, status="accepted", response="")
    else:
        payload["operation"] = operations[action]
    status, error = authoring_action(
        db_path,
        tmp_path,
        json.dumps(payload).encode(),
        "decide" if action == "decide" else "planning",
    )
    assert status == 404 and "error" in error
    assert service.snapshot() == before
