"""Public regressions for interview progression, technical context and model recovery."""

import json

import pytest

from harness.authoring import AuthoringService
from harness.dashboard.authoring_actions import authoring_action
from harness.db import transaction
from harness.interview import InterviewEngine

LEGACY_SCOPE = (
    "Ownership is unresolved; which package or implementation path "
    "does this request target? Keyword and test counts do not establish ownership."
)


def interview_fixture(controller, tmp_path):
    task = controller.create_task("ar-2906", "trivial")
    ws = controller.workspace(task.id)
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"repo_root": str(tmp_path), "decisions": []})
        first = ws.ask("scope", LEGACY_SCOPE, source="investigation")
        second = ws.ask("functional_behavior", "What should users be able to do?")
    controller.advance(task.id)
    return AuthoringService(controller, tmp_path, task.id), first, second


def post_answer(db_path, tmp_path, service, question, mode, answer="", **extra):
    payload = {
        "task": service.task_id,
        "expected": service.store.base(),
        "request_id": f"recovery-{mode}-{question.id}",
        "question": question.id,
        "mode": mode,
        "answer": answer,
        "source": None,
        "ref": None,
    } | extra
    return authoring_action(db_path, tmp_path, json.dumps(payload).encode(), "answer")


def test_unknown_moves_domain_current_question_without_waiving_requirement(
    controller, tmp_path, db_path
):
    service, first, second = interview_fixture(controller, tmp_path)
    assert post_answer(db_path, tmp_path, service, first, "unknown")[0] == 200
    snapshot = service.snapshot()
    assert snapshot["interview"]["current_question_id"] == second.id
    assert first.id in snapshot["interview"]["unknown_question_ids"]
    assert controller.workspace(service.task_id).question(first.id).status == "open"
    assert any(
        item.question_id == first.id
        for item in InterviewEngine(controller, service.task_id).checklist().items
    )
    assert (
        post_answer(db_path, tmp_path, service, second, "answer", "Users can export CSV")[0] == 200
    )
    assert service.snapshot()["interview"]["current_question_id"] != first.id


def test_legacy_scanner_presentation_preserves_original_wording(controller, tmp_path):
    service, first, _ = interview_fixture(controller, tmp_path)
    shown = next(q for q in service.snapshot()["questions"] if q["id"] == first.id)
    assert shown["text"] == LEGACY_SCOPE
    assert "ar-2906" in shown["display_text"]
    assert "optional" in shown["display_text"] and "ticket" in shown["display_text"].lower()
    assert (
        shown["discovery_note"]
        and controller.workspace(service.task_id).question(first.id).text == LEGACY_SCOPE
    )


def test_unavailable_generation_is_refused_atomically_but_manual_work_survives(
    controller, tmp_path, monkeypatch
):
    service, _, _ = interview_fixture(controller, tmp_path)
    service.save_spec(
        expected=service.store.base(), request_id="manual-draft-1234", body="## Manual draft\n"
    )
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"host": host, "available": False}
    )
    before = service.snapshot()
    with pytest.raises(ValueError, match="unavailable"):
        service.request_regeneration(
            expected=before["base"], request_id="unavailable-1234", host="claude"
        )
    assert service.snapshot() == before


def test_multirepo_scan_has_one_plain_scope_request_and_keeps_ownership_unresolved(tmp_path):
    from harness.analyzer import analyze
    from harness.repository_audit import audit

    for name in ("packages/one", "packages/two"):
        directory = tmp_path / name
        directory.mkdir(parents=True)
        (directory / "package.json").write_text(
            json.dumps({"name": name, "scripts": {"test": "fixture"}})
        )
    report = analyze(tmp_path, "ar-2906")
    questions = [q.text for q in report.questions if q.topic == "scope"]
    assert len(questions) == 1
    assert "optional" in questions[0] and "outcome" in questions[0]
    assert "packages/one" not in questions[0] and "Keyword" not in questions[0]
    assert audit(tmp_path, "ar-2906")["ownership"] == "UNRESOLVED"


def test_persisted_legacy_duplicates_do_not_repeat_the_same_material_ask(
    controller, tmp_path, db_path
):
    service, first, second = interview_fixture(controller, tmp_path)
    ws = controller.workspace(service.task_id)
    with transaction(controller.conn):
        duplicate = ws.ask(
            "scope",
            "Several packages found (`one/`, `two/`); which one does the request target?",
            source="investigation",
        )
        relevance = ws.ask(
            "scope",
            "No file matches the request keywords (2906); is this new functionality?",
            source="investigation",
        )
    assert post_answer(db_path, tmp_path, service, first, "unknown")[0] == 200
    projection = service.snapshot()["interview"]
    assert projection["current_question_id"] == second.id
    assert projection["scope_response_question_id"] == first.id
    assert set(projection["related_scope_question_ids"]) == {first.id, duplicate.id, relevance.id}
    for question in (first, duplicate, relevance):
        assert ws.question(question.id).status == "open"
        assert any(
            item.question_id == question.id
            for item in InterviewEngine(controller, service.task_id).checklist().items
        )


def test_unrelated_investigation_scope_question_remains_askable(controller, tmp_path, db_path):
    service, first, second = interview_fixture(controller, tmp_path)
    ws = controller.workspace(service.task_id)
    with transaction(controller.conn):
        genuine = ws.ask(
            "scope", "Should exports include archived accounts?", source="investigation"
        )
    assert post_answer(db_path, tmp_path, service, first, "unknown")[0] == 200
    assert genuine.id not in service.snapshot()["interview"]["related_scope_question_ids"]
    assert (
        post_answer(db_path, tmp_path, service, second, "answer", "Users can export CSV")[0] == 200
    )
    assert service.snapshot()["interview"]["current_question_id"] == genuine.id


def test_http_unavailable_host_and_non_answer_are_actionable_and_atomic(
    controller, tmp_path, db_path, monkeypatch
):
    service, first, _ = interview_fixture(controller, tmp_path)
    code, result = post_answer(db_path, tmp_path, service, first, "answer", "I don't know")
    assert code == 409 and "save a non-answer as unresolved" in result["error"]
    assert result["code"] == "non_answer"
    before = service.snapshot()
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"host": host, "available": False}
    )
    code, result = authoring_action(
        db_path,
        tmp_path,
        json.dumps(
            {
                "task": service.task_id,
                "expected": service.store.base(),
                "request_id": "http-unavailable-1234",
                "host": "claude",
            }
        ).encode(),
        "regenerate",
    )
    assert code == 409 and "Choose an available interview model" in result["error"]
    assert service.snapshot() == before


def test_dashboard_task_view_includes_only_public_session_snapshot(controller, db_path, tmp_path):
    import json

    from harness.dashboard.view import combined_view
    from tests.planning_session_fixture import ordinary_owner

    bridge, receipt, _, capability = ordinary_owner(controller, tmp_path)
    view = combined_view(db_path, bridge.task_id, None, root=bridge.root)
    session = view["task"]["session"]

    assert session["base"] == bridge.base()
    assert session["snapshot"]["mode"] == "attached"
    assert session["snapshot"]["attachmentId"] == receipt["attachment_id"]
    assert session["snapshot"]["epoch"] == receipt["epoch"]
    assert session["snapshot"]["events"]
    serialized = json.dumps(session, sort_keys=True)
    assert capability["path"] not in serialized
    assert "capability_verifier" not in serialized
    assert "root_session_id" not in serialized
    assert "current_thread_id" not in serialized


def test_local_startup_keeps_manual_interview_when_model_is_unavailable(
    controller, tmp_path, db_path, monkeypatch
):
    import time

    from harness.authoring_worker import AuthoringWorker

    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"host": host, "available": False}
    )
    service = AuthoringService(controller, tmp_path)
    created = service.start(
        request="Plan CSV exports", request_id="manual-unavailable-start", host="claude"
    )
    calls = []
    worker = AuthoringWorker(db_path, tmp_path, provider_factory=lambda host: calls.append(host))
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = AuthoringService(controller, tmp_path, created["task"]).snapshot()
            if snapshot["model_recovery"]:
                break
            time.sleep(0.02)
        assert snapshot["phase"] == "interview"
        assert len(snapshot["jobs"]) == 1 and snapshot["jobs"][0]["kind"] == "investigate"
        assert snapshot["jobs"][0]["state"] == "succeeded"
        assert snapshot["model_recovery"]["state"] == "unavailable"
        assert snapshot["model_recovery"]["host"] == "claude"
        assert snapshot["questions"] and snapshot["working"]
        assert calls == []
    finally:
        worker.close()
        assert not worker.thread or not worker.thread.is_alive()
