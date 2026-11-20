"""Source evidence never manufactures developer answers or implicit approval."""

import pytest

from harness.interview import InterviewEngine
from harness.review_package import accept, package
from tests.investigation_fixture import native_fixture as native_fixture
from tests.test_investigation_jobs import completed


def adopt(service):
    candidate = next(
        r for r in service.snapshot()["revisions"] if r["attribution"] == "model_proposal"
    )
    preview = service.reconcile_preview(candidate["id"])
    return service.reconcile(
        expected=service.store.base(),
        request_id="adopt-source-candidate",
        revision=candidate["id"],
        token=preview["token"],
        confirm_graph=True,
        acknowledge=True,
        actions=[],
    )


def test_pending_sources_block_review_then_exact_acceptance_has_source_provenance(
    controller, db_path, tmp_path, native_fixture
):
    service = completed(controller, db_path, tmp_path, native_fixture)
    engine = InterviewEngine(controller, service.task_id)
    assert engine.checklist().blocking and not engine.sync_due()
    assert engine.upcoming() is None
    assert not engine.summary().requirements
    adopt(service)
    proposal = package(controller, service.task_id)
    assert proposal["content"]["schema_version"] == 2
    accepted = accept(
        controller,
        service.task_id,
        proposal["sha256"],
        source="User explicitly reviewed this exact synthetic source package",
    )
    assert accepted["implementation_approval"] is False
    assert not engine.checklist().blocking
    assert engine.summary().requirements
    assert {r.provenance for r in engine.summary().requirements} == {"accepted_source_evidence"}
    assert engine.checklist().answered == 0
    assert not controller.workspace(service.task_id).questions()
    assert (
        accept(
            controller,
            service.task_id,
            proposal["sha256"],
            source="User explicitly reviewed this exact synthetic source package",
        )["acceptance"]
        == accepted["acceptance"]
    )


def test_genuine_unknown_decision_stays_open_and_blocks_source_acceptance(
    controller, db_path, tmp_path, native_fixture
):
    service = completed(controller, db_path, tmp_path, native_fixture, decisions=True)
    engine = InterviewEngine(controller, service.task_id)
    question = engine.upcoming()
    assert question and len(engine.effective_questions()) == 1
    engine.unknown(question.id)
    assert (
        engine.upcoming() is None
        and controller.workspace(service.task_id).question(question.id).answer is None
    )
    adopt(service)
    proposal = package(controller, service.task_id)
    with pytest.raises(ValueError, match="decisions"):
        accept(
            controller,
            service.task_id,
            proposal["sha256"],
            source="User explicitly reviewed this exact synthetic source package",
        )
    assert controller.workspace(service.task_id).question(question.id).status == "open"


def test_source_invalidation_revokes_review_without_touching_answer_history(
    controller, db_path, tmp_path, native_fixture
):
    from harness.db import transaction
    from harness.investigation_store import InvestigationStore

    service = completed(controller, db_path, tmp_path, native_fixture)
    adopt(service)
    proposal = package(controller, service.task_id)
    accept(
        controller,
        service.task_id,
        proposal["sha256"],
        source="User explicitly reviewed this exact synthetic source package",
    )
    store = InvestigationStore(controller, service.project, service.task_id)
    with transaction(controller.conn):
        store.invalidate(
            store.read_active()["id"], {"reason": "source_changed", "message": "Refresh required"}
        )
    assert InterviewEngine(controller, service.task_id).checklist().blocking
    assert not InterviewEngine(controller, service.task_id).coverage().accepted
    assert store.acceptance() is not None


def refresh(service, db_path, *, transform=lambda value, inputs: value):
    import time

    from harness.authoring_worker import AuthoringWorker
    from tests.investigation_fixture import synthesis

    index = len(service.snapshot()["jobs"])
    service.investigate(
        expected=service.store.base(),
        request_id=f"refresh-source-{index:04}",
        host="codex",
        site="cloud-one",
    )

    class Model:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            return transform(synthesis(inputs, decisions=True), inputs)

    worker = AuthoringWorker(db_path, service.root, provider_factory=Model)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = service.snapshot()["jobs"][-1]
            if job["state"] not in {"queued", "running"}:
                return job
            time.sleep(0.02)
        pytest.fail("refresh did not settle")
    finally:
        worker.close()
        assert not worker.thread.is_alive()


def test_paraphrased_decision_reuses_saved_answer_and_unknown_identity(
    controller, db_path, tmp_path, native_fixture
):
    service = completed(controller, db_path, tmp_path, native_fixture, decisions=True)
    engine = InterviewEngine(controller, service.task_id)
    original = engine.upcoming()
    engine.answer(original.id, "Exclude archived accounts")

    def paraphrase(value, inputs):
        value["investigation"]["decisions"][0]["text"] = "Should archived accounts appear in CSV?"
        return value

    assert refresh(service, db_path, transform=paraphrase)["state"] == "succeeded"
    assert [q.id for q in engine.effective_questions()] == [original.id]
    assert engine.effective_questions()[0].answer == "Exclude archived accounts"
    assert engine.upcoming() is None
    assert (
        controller.conn.execute("SELECT count(*) FROM investigation_decisions").fetchone()[0] == 1
    )


def test_changed_premise_requires_explicit_predecessor_and_retires_only_effective_view(
    controller, db_path, tmp_path, native_fixture
):
    import json
    from pathlib import Path

    service = completed(controller, db_path, tmp_path, native_fixture, decisions=True)
    engine = InterviewEngine(controller, service.task_id)
    old = engine.upcoming()
    engine.answer(old.id, "Exclude archived accounts")
    settings = Path(native_fixture[0].executable["path"] + ".json")
    data = json.loads(settings.read_text())
    data["description"] = "CSV export must include a new archived-account mode."
    settings.write_text(json.dumps(data))
    assert refresh(service, db_path)["state"] == "failed"
    assert len(engine.effective_questions()) == 1

    def reopen(value, inputs):
        value["investigation"]["decisions"][0]["supersedes"] = old.id
        value["investigation"]["decisions"][0]["reason"] = (
            "The cited source now requires a new archived-account mode"
        )
        return value

    assert refresh(service, db_path, transform=reopen)["state"] == "succeeded"
    current = engine.upcoming()
    assert current.id != old.id and current.answer is None
    assert (
        controller.workspace(service.task_id).question(old.id).answer == "Exclude archived accounts"
    )
    assert old.id not in {q.id for q in engine.effective_questions()}
    with pytest.raises(ValueError, match="successor"):
        engine.answer(old.id, "Do not rewrite retired response")
    assert all(r.statement != "Exclude archived accounts" for r in engine.summary().requirements)


def test_accepted_technical_evidence_can_cover_raw_unknown_without_fabricating_answer(
    controller, db_path, tmp_path, native_fixture
):
    # Pause at synthesis so a historical technical unknown exists in the captured base.
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import claim_next
    from harness.authoring_worker import AuthoringWorker
    from harness.db import transaction
    from harness.interview_catalogue import catalogue_for
    from harness.investigation_sources import run
    from tests.investigation_fixture import synthesis

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "export.py").write_text("export_csv = True\n")
    task = controller.create_task("AR-2906", "trivial")
    ws = controller.workspace(task.id)
    from harness.classification import interview_depth

    entry = catalogue_for(interview_depth("trivial").topics)[0]
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"repo_root": str(repo), "decisions": []})
        question = ws.ask(entry.topic, entry.text, catalogue_key=entry.key)
    controller.advance(task.id)
    InterviewEngine(controller, task.id).unknown(question.id)
    service = AuthoringService(controller, repo, task.id)
    service.investigate(
        expected=service.store.base(),
        request_id="investigate-legacy-unknown",
        host="codex",
        site="cloud-one",
    )
    claim = claim_next(controller.conn, "legacy-worker", service.project)

    class Model:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            return synthesis(inputs)

    worker = AuthoringWorker(db_path, repo, provider_factory=Model)
    run(service, claim, worker)
    assert service.snapshot()["jobs"][-1]["state"] == "succeeded"
    adopt(service)
    proposal = package(controller, task.id)
    accept(
        controller,
        task.id,
        proposal["sha256"],
        source="User explicitly reviewed all source-backed technical findings",
    )
    engine = InterviewEngine(controller, task.id)
    assert not engine.checklist().blocking
    assert ws.question(question.id).status == "open" and ws.question(question.id).answer is None
    assert question.id in engine.question_projection()["unknown_question_ids"]
    assert engine.checklist().answered == 0


def test_repository_change_is_stale_in_read_only_consumers_and_registry(
    controller, db_path, tmp_path, native_fixture
):
    from harness.requirement_registry import registry
    from harness.review import FinalReviewer

    service = completed(controller, db_path, tmp_path, native_fixture)
    adopt(service)
    proposal = package(controller, service.task_id)
    accept(
        controller,
        service.task_id,
        proposal["sha256"],
        source="User explicitly reviewed the exact source-bound proposal",
    )
    (service.root / "export.py").write_text("export_csv = False\n")
    assert (
        InterviewEngine(controller, service.task_id).coverage().rows[0]["state"] == "stale_evidence"
    )
    assert FinalReviewer(controller, service.task_id).report().blocking
    with pytest.raises(ValueError, match="coverage"):
        registry(controller, service.task_id, service.snapshot()["working"]["body"])


def test_exact_legacy_ownership_and_package_dump_prompts_become_reviewable_findings(
    controller, tmp_path, db_path, native_fixture
):
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import claim_next
    from harness.authoring_worker import AuthoringWorker
    from harness.db import transaction
    from harness.investigation_sources import run
    from tests.investigation_fixture import synthesis
    from tests.test_dashboard_interview_recovery import LEGACY_SCOPE

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "export.py").write_text("export_csv = True\n")
    task = controller.create_task("AR-2906", "trivial")
    ws = controller.workspace(task.id)
    package_dump = "Several packages found (`one/`, `two/`); which one does the request target?"
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"repo_root": str(repo), "decisions": []})
        one = ws.ask("scope", LEGACY_SCOPE, source="investigation")
        two = ws.ask("scope", package_dump, source="investigation")
    controller.advance(task.id)
    service = AuthoringService(controller, repo, task.id)
    service.investigate(
        expected=service.store.base(),
        request_id="legacy-scanner-investigate",
        host="codex",
        site="cloud-one",
    )
    assert InterviewEngine(controller, task.id).upcoming() is None

    class Model:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            return synthesis(inputs)

    worker = AuthoringWorker(db_path, repo, provider_factory=Model)
    run(service, claim_next(controller.conn, "legacy-scanner-worker", service.project), worker)
    engine = InterviewEngine(controller, task.id)
    assert engine.upcoming() is None
    assert any(
        item.catalogue_key == "component_responsibilities.owner"
        for item in engine.checklist().items
    )
    assert ws.question(one.id).text == LEGACY_SCOPE and ws.question(two.id).text == package_dump
    assert all(
        ws.question(q.id).status == "open" and ws.question(q.id).answer is None for q in (one, two)
    )
    assert len(ws.questions()) == 2
