"""Investigation-first startup and fenced source publication."""

import pytest

from harness.authoring import AuthoringService
from tests.investigation_fixture import native_fixture as native_fixture


def test_ticket_start_never_queues_unqualified_remote_or_scanner_questions(
    controller, tmp_path, monkeypatch
):
    monkeypatch.setattr("harness.investigation_policy.capability", lambda: None)
    service = AuthoringService(controller, tmp_path)
    result = service.start(request="AR-2906", request_id="investigation-start-001", host="codex")
    snapshot = AuthoringService(controller, tmp_path, result["task"]).snapshot()
    assert result.get("job") is None
    assert snapshot["investigation"]["enabled"]
    assert snapshot["investigation"]["recovery"]["reason"] == "unqualified_transport"
    assert not snapshot["questions"]
    assert snapshot["working"]


def completed(
    controller, db_path, tmp_path, native_fixture, *, decisions=False, before_result=None
):
    import time

    from harness.authoring_worker import AuthoringWorker
    from tests.investigation_fixture import synthesis

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "export.py").write_text("def export_csv(accounts):\n    return accounts\n")
    created = AuthoringService(controller, repo).start(
        request="AR-2906", request_id="source-start-001", host="codex"
    )
    service = AuthoringService(controller, repo, created["task"])

    class Model:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            if before_result:
                before_result(inputs)
            return synthesis(inputs, decisions=decisions)

    worker = AuthoringWorker(db_path, repo, provider_factory=Model)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snap = service.snapshot()
            if snap["jobs"][-1]["state"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert snap["jobs"][-1]["state"] == "succeeded", snap["jobs"][-1]
        return service
    finally:
        worker.close()
        assert not worker.thread.is_alive()


def test_actual_source_pipeline_preserves_working_draft_and_proposes_concrete_graph(
    controller, db_path, tmp_path, native_fixture
):
    service = completed(controller, db_path, tmp_path, native_fixture)
    snapshot = service.snapshot()
    assert snapshot["phase"] == "interview" and snapshot["questions"] == []
    assert snapshot["interview"]["current_question_id"] is None
    assert snapshot["investigation"]["stage"] == "ready"
    assert snapshot["investigation"]["sources"][0]["kind"] == "ticket"
    candidate = next(r for r in snapshot["revisions"] if r["attribution"] == "model_proposal")
    candidate = service.store.revision(candidate["id"])
    assert len(candidate["graph"]["value"]["nodes"]) == 2
    assert snapshot["working"]["id"] != candidate["id"]
    assert snapshot["investigation"]["coverage"]["accepted"] is False


def test_edit_during_synthesis_cannot_activate_sources_or_replace_draft(
    controller, db_path, tmp_path, native_fixture
):
    import threading
    from types import SimpleNamespace

    from harness.authoring_jobs import claim_next
    from harness.investigation_native import HANDOFFS, NativeInvestigator
    from harness.investigation_sources import run
    from tests.investigation_fixture import synthesis

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "export.py").write_text("export_csv = True\n")
    result = AuthoringService(controller, repo).start(
        request="AR-2906", request_id="edit-race-start", host="codex"
    )
    service = AuthoringService(controller, repo, result["task"])
    claim = claim_next(controller.conn, "owned-worker", service.project)

    class Model:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            service.save_spec(
                expected=service.store.base(),
                request_id="edit-during-synthesis",
                body="## Keep my edited draft\n",
            )
            return synthesis(inputs)

    run(
        service,
        claim,
        SimpleNamespace(
            stop=threading.Event(),
            acquisition_factory=NativeInvestigator,
            provider_factory=Model,
            handoffs=HANDOFFS,
        ),
    )
    snapshot = service.snapshot()
    assert snapshot["working"]["body"] == "## Keep my edited draft\n"
    assert snapshot["investigation"]["bundle"] is None
    assert snapshot["jobs"][-1]["state"] == "stale"
    assert not controller.conn.execute("SELECT 1 FROM investigation_bundles").fetchone()


@pytest.mark.parametrize("mutation", ["edit", "answer", "cancel"])
def test_barrier_controlled_mutations_fence_source_completion(
    controller, db_path, tmp_path, native_fixture, mutation
):
    import threading
    import time

    from harness.authoring_worker import AuthoringWorker
    from harness.db import transaction
    from tests.investigation_fixture import synthesis

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "export.py").write_text("export_csv = True\n")
    task = controller.create_task("AR-2906", "trivial")
    ws = controller.workspace(task.id)
    with transaction(controller.conn):
        ws.checkpoint("investigation", {"repo_root": str(repo), "decisions": []})
        question = ws.ask(
            "business_requirements", "Should archived accounts be included?", kind="decision"
        )
    controller.advance(task.id)
    service = AuthoringService(controller, repo, task.id)
    result = service.investigate(
        expected=service.store.base(),
        request_id="barrier-start-001",
        host="codex",
        site="cloud-one",
    )
    entered, release = threading.Event(), threading.Event()

    class Model:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            entered.set()
            assert release.wait(5)
            return synthesis(inputs)

    worker = AuthoringWorker(db_path, repo, provider_factory=Model)
    worker.start()
    try:
        assert entered.wait(5)
        if mutation == "edit":
            service.save_spec(
                expected=service.store.base(),
                request_id="barrier-manual-edit",
                body="## Saved during investigation\n",
            )
        elif mutation == "answer":
            service.answer(
                expected=service.store.base(),
                request_id="barrier-manual-answer",
                question=question.id,
                mode="answer",
                answer="Exclude archived accounts",
            )
        else:
            service.cancel_job(
                expected=service.store.base(), request_id="barrier-cancel-job", job=result["job"]
            )
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = service.snapshot()["jobs"][-1]
            if job["state"] in {"stale", "cancelled"} and job["settlement"] == "settled":
                break
            time.sleep(0.02)
        assert service.snapshot()["investigation"]["bundle"] is None
        assert job["state"] == ("cancelled" if mutation == "cancel" else "stale")
        assert job["settlement"] == "settled"
        if mutation == "answer":
            assert ws.question(question.id).answer == "Exclude archived accounts"
        if mutation == "edit":
            assert service.snapshot()["working"]["body"] == "## Saved during investigation\n"
    finally:
        release.set()
        worker.close()
        assert not worker.thread.is_alive()


def test_restart_retry_keeps_prior_attempt_input_and_never_activates_old_claim(
    controller, tmp_path, db_path, native_fixture
):
    from harness.authoring_jobs import claim_next, interrupt_owned
    from harness.authoring_worker import AuthoringWorker
    from harness.investigation_sources import run

    result = AuthoringService(controller, tmp_path).start(
        request="AR-2906", request_id="restart-source-start", host="codex"
    )
    service = AuthoringService(controller, tmp_path, result["task"])
    claim = claim_next(controller.conn, "old-worker", service.project)
    old_input = claim["input_json"]
    interrupt_owned(controller.conn, "old-worker")
    with pytest.raises(ValueError, match="acknowledge"):
        service.retry_job(
            expected=service.store.base(),
            request_id="restart-no-consent",
            job=result["job"],
            acknowledge_unknown=False,
        )
    service.retry_job(
        expected=service.store.base(),
        request_id="restart-with-consent",
        job=result["job"],
        acknowledge_unknown=True,
    )
    current = service.snapshot()["jobs"][-1]
    worker = AuthoringWorker(db_path, tmp_path)
    run(service, claim, worker)
    assert service.snapshot()["jobs"][-1]["attempt"] == 2
    assert service.snapshot()["jobs"][-1]["state"] == current["state"] == "queued"
    assert service.snapshot()["investigation"]["bundle"] is None
    assert (
        controller.conn.execute(
            "SELECT input_json FROM authoring_attempts WHERE job_id=? AND attempt=1", (claim["id"],)
        ).fetchone()[0]
        == old_input
    )


def test_failure_mid_publication_rolls_back_bundle_questions_and_candidate(
    controller, db_path, tmp_path, native_fixture, monkeypatch
):
    import time

    from harness.authoring_worker import AuthoringWorker
    from harness.investigation_store import InvestigationStore
    from tests.investigation_fixture import synthesis

    def fail(self, *args):
        raise ValueError("injected publication failure after source insertion")

    monkeypatch.setattr(InvestigationStore, "_decisions", fail)
    created = AuthoringService(controller, tmp_path).start(
        request="AR-2906", request_id="rollback-source-start", host="codex"
    )
    service = AuthoringService(controller, tmp_path, created["task"])

    class Model:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            return synthesis(inputs)

    worker = AuthoringWorker(db_path, tmp_path, provider_factory=Model)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = service.snapshot()["jobs"][-1]
            if job["state"] == "failed":
                break
            time.sleep(0.02)
        assert job["state"] == "failed"
        for table in ("investigation_bundles", "investigation_sources", "investigation_decisions"):
            assert controller.conn.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
        assert not any(
            r["attribution"] == "model_proposal" for r in service.snapshot()["revisions"]
        )
    finally:
        worker.close()
        assert not worker.thread.is_alive()


def test_http_closed_actions_and_ephemeral_handoff_never_persist_oauth_url(
    controller, db_path, tmp_path, native_fixture, monkeypatch
):
    import json
    import threading

    from harness.authoring_jobs import claim_next
    from harness.authoring_worker import AuthoringWorker
    from harness.dashboard.server import DashboardServer
    from harness.investigation_native import HANDOFFS
    from tests.test_dashboard_authoring_browser import _http_json

    created = AuthoringService(controller, tmp_path).start(
        request="AR-2906", request_id="http-source-start", host="codex"
    )
    service = AuthoringService(controller, tmp_path, created["task"])
    service.cancel_job(
        expected=service.store.base(), request_id="http-cancel-source", job=created["job"]
    )
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: None)
    server = DashboardServer(db_path, service.task_id, project_root=tmp_path)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    claim = None
    try:
        payload = {
            "task": service.task_id,
            "expected": service.store.base(),
            "request_id": "http-reconnect-explicit",
            "host": "codex",
            "site": "cloud-one",
        }
        code, response = _http_json(
            server, "/api/authoring/reconnect-jira", method="POST", body=payload
        )
        assert code == 200 and response["state"] == "queued"
        assert (
            _http_json(server, "/api/authoring/reconnect-jira", method="POST", body=payload)[1]
            == response
        )
        claim = claim_next(controller.conn, "oauth-worker", service.project)
        synthetic_url = "https://auth.atlassian.com/authorize?synthetic=private-handoff"
        HANDOFFS.publish(claim, {"authorization_url": synthetic_url, "login_id": "owned-login"})
        handoff = {
            "task": service.task_id,
            "expected": service.store.base(),
            "request_id": "http-get-handoff",
            "job": claim["id"],
        }
        code, result = _http_json(
            server, "/api/authoring/jira-handoff", method="POST", body=handoff
        )
        assert code == 200 and result["handoff"]["authorization_url"] == synthetic_url
        assert synthetic_url not in json.dumps(service.snapshot())
        assert not controller.conn.execute(
            "SELECT 1 FROM authoring_receipts WHERE result_json LIKE ?", ("%private-handoff%",)
        ).fetchone()
        invalid = dict(payload, request_id="http-inject-rpc", method="mcpServer/tool/call")
        assert (
            _http_json(server, "/api/authoring/reconnect-jira", method="POST", body=invalid)[0]
            == 400
        )
    finally:
        if claim:
            HANDOFFS.discard(claim)
        server.shutdown()
        server.server_close()
        thread.join(10)
        assert not thread.is_alive()


def test_real_approval_history_allows_explicit_back_then_source_refresh(
    controller, sample_repo, native_fixture
):
    from harness.approval import ApprovalGate
    from harness.spec_store import SpecStore
    from tests.complete_task import settle

    task = controller.create_task("AR-2906", "trivial")
    settle(controller, task.id)
    controller.advance(task.id)
    gate = ApprovalGate(
        controller, task.id, SpecStore(sample_repo / "docs/specs" / task.id), sample_repo
    )
    approval, record, _ = gate.approve()
    service = AuthoringService(controller, sample_repo, task.id)
    before = dict(
        controller.conn.execute("SELECT * FROM approvals WHERE task_id=?", (task.id,)).fetchone()
    )
    with pytest.raises(ValueError):
        service.investigate(
            expected=service.store.base(), request_id="approved-refresh-refused", host="codex"
        )
    service.planning(
        expected=service.store.base(),
        request_id="explicit-back-refresh",
        operation={"op": "back", "phase": "interview", "reason": "Refresh source evidence"},
    )
    result = service.investigate(
        expected=service.store.base(), request_id="history-refresh-allowed", host="codex"
    )
    assert result["state"] == "queued"
    assert _run_source(service)["state"] == "succeeded"
    assert service.snapshot()["investigation"]["bundle"]
    assert (
        dict(
            controller.conn.execute(
                "SELECT * FROM approvals WHERE task_id=?", (task.id,)
            ).fetchone()
        )
        == before
    )
    assert gate.manager.store.current().body == record.body
    with pytest.raises(ValueError):
        gate.require()


def _run_source(service):
    import threading
    from types import SimpleNamespace

    from harness.authoring_jobs import claim_next
    from harness.investigation_native import HANDOFFS, NativeInvestigator
    from harness.investigation_sources import run
    from tests.investigation_fixture import InvestigationModelFixture

    claim = claim_next(service.conn, "correction-worker", service.project)
    assert claim is not None
    run(
        service,
        claim,
        SimpleNamespace(
            stop=threading.Event(),
            acquisition_factory=NativeInvestigator,
            provider_factory=InvestigationModelFixture,
            handoffs=HANDOFFS,
        ),
    )
    return service.snapshot()["jobs"][-1]


def _approved_source_service(controller, sample_repo):
    from harness.approval import ApprovalGate
    from harness.spec_store import SpecStore
    from tests.complete_task import settle

    task = controller.create_task("AR-2906", "trivial")
    settle(controller, task.id)
    controller.advance(task.id)
    gate = ApprovalGate(
        controller, task.id, SpecStore(sample_repo / "docs/specs" / task.id), sample_repo
    )
    approval, _, _ = gate.approve()
    service = AuthoringService(controller, sample_repo, task.id)
    service.planning(
        expected=service.store.base(),
        request_id="history-phase-back",
        operation={
            "op": "back",
            "phase": "interview",
            "reason": "Investigate changed source evidence",
        },
    )
    return service, approval


def _execution_record(service, approval, *, status="Failed", attempt="PreSpawnFailed", child=None):
    from harness.execution_store import reserve_execution

    execution, _ = reserve_execution(
        service.conn,
        execution_id="history-execution",
        task_id=service.task_id,
        approval_id=approval.id,
        host="codex",
        binding={},
        handoff_path=service.root / "fixture-handoff.json",
        handoff_hash="fixture",
        al_session_id="fixture-history",
    )
    service.conn.execute("UPDATE executions SET status=? WHERE id=?", (status, execution["id"]))
    if attempt:
        service.conn.execute(
            "INSERT INTO execution_attempts"
            "(id,execution_id,token,mode,status,child_pid,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                "history-attempt",
                execution["id"],
                "history-token",
                "start",
                attempt,
                child,
                "fixture",
                "fixture",
            ),
        )


def test_settled_failed_history_preserved_across_public_refresh_and_publication(
    controller, sample_repo, native_fixture
):
    service, approval = _approved_source_service(controller, sample_repo)
    _execution_record(service, approval)
    # A known pre-spawn failure has no child or owned tool transport to tear down.
    service._refuse_execution()
    before = [tuple(row) for row in service.conn.execute("SELECT * FROM executions")]
    attempts = [tuple(row) for row in service.conn.execute("SELECT * FROM execution_attempts")]
    result = service.investigate(
        expected=service.store.base(), request_id="settled-history-refresh", host="codex"
    )
    assert result["state"] == "queued"
    assert _run_source(service)["state"] == "succeeded"
    assert service.snapshot()["investigation"]["bundle"]
    assert [tuple(row) for row in service.conn.execute("SELECT * FROM executions")] == before
    assert [
        tuple(row) for row in service.conn.execute("SELECT * FROM execution_attempts")
    ] == attempts


@pytest.mark.parametrize(
    "status,attempt,child",
    [
        ("Running", "Running", None),
        ("Failed", None, None),
        ("Completed", "Reserved", None),
        ("Completed", "Exited", 123456),
    ],
)
def test_unsettled_execution_refuses_admission_without_mutation(
    controller, sample_repo, native_fixture, status, attempt, child
):
    service, approval = _approved_source_service(controller, sample_repo)
    _execution_record(service, approval, status=status, attempt=attempt, child=child)
    before = service.conn.total_changes
    with pytest.raises(ValueError, match="execution"):
        service.investigate(
            expected=service.store.base(), request_id="unsettled-history-refused", host="codex"
        )
    assert service.conn.total_changes == before
    assert service.conn.execute("SELECT count(*) FROM investigation_bundles").fetchone()[0] == 0


def test_phase_change_after_queue_never_publishes_sources(controller, tmp_path, native_fixture):
    from harness.phases import Phase

    created = AuthoringService(controller, tmp_path).start(
        request="AR-2906", request_id="phase-race-start", host="codex"
    )
    service = AuthoringService(controller, tmp_path, created["task"])
    # An external authority transition invalidates the captured base without auto-returning.
    controller.conn.execute(
        "UPDATE tasks SET phase=? WHERE id=?", (Phase.APPROVAL, service.task_id)
    )
    assert _run_source(service)["state"] == "stale"
    assert service.snapshot()["investigation"]["bundle"] is None
    assert controller.get_task(service.task_id).phase is Phase.APPROVAL


@pytest.mark.parametrize(
    "status,attempt,child",
    [
        ("Running", "Running", None),
        ("Failed", None, None),
        ("Completed", "Reserved", None),
        ("Completed", "Exited", 123456),
    ],
)
def test_new_unsettled_execution_fences_queued_publication(
    controller, sample_repo, native_fixture, status, attempt, child
):
    service, approval = _approved_source_service(controller, sample_repo)
    service.investigate(
        expected=service.store.base(), request_id="before-authority-race", host="codex"
    )
    _execution_record(service, approval, status=status, attempt=attempt, child=child)
    assert _run_source(service)["state"] == "stale"
    for table in ("investigation_bundles", "investigation_sources", "investigation_decisions"):
        assert service.conn.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert not any(r["attribution"] == "model_proposal" for r in service.snapshot()["revisions"])


def test_actual_exited_fixture_child_teardown_allows_historical_refresh(
    controller, db_path, sample_repo, native_fixture
):
    import subprocess
    import sys
    from types import SimpleNamespace

    from harness.execution_settlement import record_closed_context
    from harness.execution_store import latest_execution

    service, approval = _approved_source_service(controller, sample_repo)
    child = subprocess.Popen(
        [sys.executable, "-c", "pass"], cwd=sample_repo, start_new_session=True
    )
    try:
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    _execution_record(service, approval, status="Completed", attempt="Exited", child=child.pid)
    execution = latest_execution(service.conn, service.task_id)
    record_closed_context(
        execution,
        "history-token",
        child,
        SimpleNamespace(
            operations=SimpleNamespace(
                database=db_path,
                processes=SimpleNamespace(records={}),
            )
        ),
    )
    service._refuse_execution()
    before = [tuple(r) for r in service.conn.execute("SELECT * FROM execution_attempts")]
    service.investigate(
        expected=service.store.base(), request_id="exited-child-source-refresh", host="codex"
    )
    assert _run_source(service)["state"] == "succeeded"
    assert [tuple(r) for r in service.conn.execute("SELECT * FROM execution_attempts")] == before


@pytest.mark.parametrize(
    "case", ["unbound-execution", "unbound-phase", "changed-root-execution", "never-initialized"]
)
def test_managed_artifact_binding_never_precedes_refused_admission(
    controller, sample_repo, tmp_path, native_fixture, case
):
    from harness.approval import ApprovalGate
    from harness.authoring_jobs import immutable_input
    from harness.db import transaction
    from harness.investigation_artifacts import stored
    from harness.phases import Phase
    from harness.spec_store import SpecStore
    from tests.complete_task import settle

    task = controller.create_task("AR-2906", "trivial")
    settle(controller, task.id)
    controller.advance(task.id)
    approval, _, _ = ApprovalGate(
        controller, task.id, SpecStore(sample_repo / "docs/specs" / task.id), sample_repo
    ).approve()
    if case != "unbound-phase":
        controller.go_back(task.id, Phase.INTERVIEW, "Explicit source refresh fixture")
    service = AuthoringService(controller, sample_repo, task.id)
    if case != "never-initialized":
        with transaction(controller.conn):
            service._initial()
    assert stored(controller.conn, task.id) is None
    before = controller.conn.total_changes
    service.snapshot()
    if case != "never-initialized":
        immutable_input(service)
    assert stored(controller.conn, task.id) is None and controller.conn.total_changes == before
    if case == "changed-root-execution":
        # Bind through an admitted actual task mutation, then change server configuration.
        service.followup(
            expected=service.store.base(),
            request_id="bind-before-execution-fixture",
            body="Preserve existing exports",
        )
        prior = stored(controller.conn, task.id)
        service.specs = tmp_path / "changed-configured-root"
    else:
        prior = None
    if case != "unbound-phase":
        _execution_record(service, approval, status="Running", attempt="Running")
    before = controller.conn.total_changes
    with pytest.raises(ValueError, match="execution|phase|approval"):
        service.investigate(
            expected=service.store.base(), request_id="refuse-before-artifact-bind", host="codex"
        )
    assert stored(controller.conn, task.id) == prior
    assert controller.conn.total_changes == before
