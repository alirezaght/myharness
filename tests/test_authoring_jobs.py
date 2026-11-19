import pytest

from harness.authoring_jobs import apply_result, claim_next, interrupt_owned
from tests.test_authoring import initialized


def output():
    def node(ident, kind):
        return {
            "id": ident,
            "label": ident,
            "kind": kind,
            "status": "proposed",
            "sources": ["request"],
            "assumption": "Proposed based on request",
        }

    graph = {
        "nodes": [node("user", "actor"), node("scheduler", "component")],
        "edges": [dict(node("submits", "relationship"), **{"from": "user", "to": "scheduler"})],
    }
    return {
        "assistant": "Proposed scheduler design",
        "questions": [],
        "recommendations": [],
        "body": "## Scheduler\nProposed design\n",
        "planning": [],
        "graph": {"concrete": graph},
    }


def queued(service):
    return service.request_regeneration(
        expected=service.store.base(), request_id="generate-1234", host="codex"
    )["job"]


def test_atomic_candidate_never_overwrites_working_and_application_unique(controller, tmp_path):
    service = initialized(controller, tmp_path)
    working = service.store.base()["working"]
    queued(service)
    claim = claim_next(controller.conn, "test-worker")
    first = apply_result(service, claim, output())
    assert first and apply_result(service, claim, output()) == first
    snap = service.snapshot()
    assert snap["working"]["id"] == working
    assert len([r for r in snap["revisions"] if r["attribution"] == "model_proposal"]) == 1
    candidate = service.store.revision(snap["revisions"][-1]["id"])
    assert candidate["graph_id"] and candidate["body"].startswith("## Scheduler")


def test_late_result_cancel_restart_and_retry_fences(controller, tmp_path):
    service = initialized(controller, tmp_path)
    job = queued(service)
    claim = claim_next(controller.conn, "old-worker")
    service.save_spec(expected=service.store.base(), request_id="save-12345", body="## User wins\n")
    assert apply_result(service, claim, output()) is None
    assert service.snapshot()["jobs"][-1]["state"] == "stale"
    service.retry_job(
        expected=service.store.base(), request_id="retry-12345", job=job, acknowledge_unknown=False
    )
    claim = claim_next(controller.conn, "new-worker")
    interrupt_owned(controller.conn)
    with pytest.raises(ValueError):
        service.retry_job(
            expected=service.store.base(),
            request_id="retry-67890",
            job=job,
            acknowledge_unknown=False,
        )
    service.retry_job(
        expected=service.store.base(), request_id="retry-67890", job=job, acknowledge_unknown=True
    )
    assert apply_result(service, claim, output()) is None
    assert service.snapshot()["jobs"][-1]["state"] == "queued"


def test_invalid_graph_applies_no_partial_candidate(controller, tmp_path):
    service = initialized(controller, tmp_path)
    queued(service)
    claim = claim_next(controller.conn, "worker")
    invalid = output() | {"graph": None}
    before = len(service.snapshot()["revisions"])
    with pytest.raises(ValueError, match="requires a concrete"):
        apply_result(service, claim, invalid)
    assert len(service.snapshot()["revisions"]) == before


def test_worker_exclusive_ownership_applies_and_closes(controller, db_path, tmp_path):
    import threading
    import time

    from harness.authoring_worker import AuthoringWorker

    service = initialized(controller, tmp_path)
    queued(service)
    entered, release = threading.Event(), threading.Event()

    class Provider:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            entered.set()
            assert release.wait(10)
            return output()

    first = AuthoringWorker(db_path, tmp_path, provider_factory=Provider)
    second = AuthoringWorker(db_path, tmp_path, provider_factory=Provider)
    first.start()
    try:
        assert entered.wait(10)
        second.start()
        assert second.status == "another_worker_owns_database"
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if service.snapshot()["jobs"][-1]["state"] == "succeeded":
                break
            time.sleep(0.02)
        assert service.snapshot()["jobs"][-1]["state"] == "succeeded"
    finally:
        release.set()
        first.close()
        second.close()
    assert first.status == "stopped" and not first.thread.is_alive()


def test_cancel_fences_output_and_retry_does_not_duplicate_stale_history(controller, tmp_path):
    service = initialized(controller, tmp_path)
    job = queued(service)
    claim = claim_next(controller.conn, "worker")
    service.cancel_job(expected=service.store.base(), request_id="cancel-12345", job=job)
    assert apply_result(service, claim, output()) is None
    assert apply_result(service, claim, output()) is None
    snapshot = service.snapshot()
    assert snapshot["jobs"][-1]["state"] == "cancelled"
    assert len([c for c in snapshot["contributions"] if c["kind"] == "stale_model"]) == 1


def test_concurrent_start_on_one_worker_publishes_one_thread_and_lock(
    controller, db_path, tmp_path, monkeypatch
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from harness.authoring_worker import AuthoringWorker

    service = initialized(controller, tmp_path)
    queued(service)
    entered, release = threading.Event(), threading.Event()

    class Provider:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            entered.set()
            assert release.wait(10)
            return output()

    worker = AuthoringWorker(db_path, tmp_path, provider_factory=Provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker.start(), range(2)))
    try:
        assert entered.wait(10)
        assert worker.thread and worker.thread.is_alive()
        assert worker.lock and not worker.lock.closed
        other = AuthoringWorker(db_path, tmp_path)
        other.start()
        assert other.status == "another_worker_owns_database"
        other.close()
    finally:
        release.set()
        worker.close()
    assert not worker.thread.is_alive() and worker.lock is None
    worker.start()
    assert worker.lock is None


def test_planning_only_output_still_persists_concrete_graph_candidate(controller, tmp_path):
    service = initialized(controller, tmp_path)
    queued(service)
    claim = claim_next(controller.conn, "worker")
    candidate = output() | {
        "body": None,
        "planning": [
            {
                "kind": "design",
                "text": "Scheduler receives jobs",
                "rationale": "Proposed responsibility",
            }
        ],
    }
    assert apply_result(service, claim, candidate)
    snapshot = service.snapshot()
    assert snapshot["jobs"][-1]["candidate"]
    assert snapshot["jobs"][-1]["content_complete"]
    revision = service.store.revision(snapshot["jobs"][-1]["candidate"])
    assert revision["body"] == snapshot["working"]["body"]
    assert revision["graph"]


def test_restart_recovers_committed_pending_draft_projection(
    controller, db_path, tmp_path, monkeypatch
):
    import time

    from harness.authoring_worker import AuthoringWorker
    from harness.spec_store import SpecStore

    service = initialized(controller, tmp_path)
    service.save_spec(
        expected=service.store.base(),
        request_id="save-project-1234",
        body="## Recover exact body\n",
    )
    revision = service.store.base()["working"]
    preview = service.reconcile_preview(revision)
    with monkeypatch.context() as patch:

        def failed_write(*args):
            raise OSError("injected interruption")

        patch.setattr(SpecStore, "write_draft", failed_write)
        with pytest.raises(OSError):
            service.reconcile(
                expected=service.store.base(),
                request_id="adopt-project-1234",
                revision=revision,
                token=preview["token"],
                confirm_graph=True,
                acknowledge=True,
                actions=[],
            )
    assert service.snapshot()["adopted"]["body"] == "## Recover exact body\n"
    assert service.snapshot()["projection"]["state"] == "pending"
    worker = AuthoringWorker(db_path, tmp_path)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if service.snapshot()["projection"]["state"] == "projected":
                break
            time.sleep(0.02)
        assert service.snapshot()["projection"]["state"] == "projected"
        assert service.manager().store.draft().body == "## Recover exact body\n"
    finally:
        worker.close()


def test_retry_retains_immutable_prior_attempt_input_and_error(controller, tmp_path):
    import json

    service = initialized(controller, tmp_path)
    job = queued(service)
    claim = claim_next(controller.conn, "worker")
    before = claim["input_json"]
    service.save_spec(
        expected=service.store.base(), request_id="new-edit-1234", body="## New attempt input\n"
    )
    assert apply_result(service, claim, output()) is None
    service.retry_job(
        expected=service.store.base(), request_id="retry-123456", job=job, acknowledge_unknown=False
    )
    attempts = controller.conn.execute(
        "SELECT * FROM authoring_attempts WHERE job_id=? ORDER BY attempt", (job,)
    ).fetchall()
    assert attempts[0]["input_json"] == before
    assert attempts[0]["error"] == "Input revision changed"
    assert json.loads(attempts[1]["input_json"])["body"] == "## New attempt input\n"
    assert attempts[0]["input_json"] != attempts[1]["input_json"]


def test_rework1_complete_context_is_not_recent_history(controller, tmp_path):
    import json

    from harness.db import transaction

    service = initialized(controller, tmp_path)
    with transaction(controller.conn):
        old = service.store.contribution("followup", "developer", {"body": "EARLY EFFECTIVE INPUT"})
        for index in range(205):
            service.store.contribution("assistant", "model", {"text": f"Later {index}"})
    assert not any(row["id"] == old for row in service.snapshot()["contributions"])
    job = queued(service)
    context = json.loads(
        controller.conn.execute(
            "SELECT input_json FROM authoring_jobs WHERE id=?", (job,)
        ).fetchone()[0]
    )
    assert any(row["id"] == old for row in context["contributions"]), (
        "effective early input was lost"
    )


def test_rework1_oversize_context_refuses_queue_atomically(controller, tmp_path):
    from harness.db import transaction

    service = initialized(controller, tmp_path)
    with transaction(controller.conn):
        for _ in range(36):
            service.store.contribution("followup", "developer", {"body": "x" * 16000})
    before = service.store.base()
    jobs = controller.conn.execute("SELECT count(*) FROM authoring_jobs").fetchone()[0]
    with pytest.raises(ValueError, match="complete.*context|context.*fit"):
        queued(service)
    assert service.store.base() == before
    assert controller.conn.execute("SELECT count(*) FROM authoring_jobs").fetchone()[0] == jobs
    assert not controller.conn.execute(
        "SELECT 1 FROM authoring_receipts WHERE request_id='generate-1234'"
    ).fetchone()


def test_rework1_worker_does_not_recover_another_project(
    controller, db_path, tmp_path, monkeypatch
):
    from harness.authoring import AuthoringService
    from harness.authoring_worker import AuthoringWorker

    own = initialized(controller, tmp_path)
    queued(own)
    own_claim = claim_next(controller.conn, "own", own.project)
    other_task = controller.create_task("Other project")
    other = AuthoringService(controller, tmp_path / "other", other_task.id)
    other.start(expected=other.store.base(), request_id="other-start-1234", host="codex")
    foreign_claim = claim_next(controller.conn, "foreign", other.project)
    worker = AuthoringWorker(db_path, tmp_path)

    def no_claim(*args):
        worker.stop.set()
        return None

    monkeypatch.setattr("harness.authoring_worker.claim_next", no_claim)
    worker._run()
    assert (
        controller.conn.execute(
            "SELECT state FROM authoring_jobs WHERE id=?", (own_claim["id"],)
        ).fetchone()[0]
        == "interrupted"
    )
    assert (
        controller.conn.execute(
            "SELECT state FROM authoring_jobs WHERE id=?", (foreign_claim["id"],)
        ).fetchone()[0]
        == "running"
    )


def test_rework1_oversize_retry_preserves_attempts_and_receipts(controller, tmp_path):
    from harness.db import transaction

    service = initialized(controller, tmp_path)
    job = service.snapshot()["jobs"][-1]["id"]
    attempts = [
        tuple(row)
        for row in controller.conn.execute(
            "SELECT * FROM authoring_attempts WHERE job_id=?", (job,)
        )
    ]
    with transaction(controller.conn):
        for _ in range(36):
            service.store.contribution("followup", "developer", {"body": "y" * 16000})
    base = service.store.base()
    with pytest.raises(ValueError, match="Complete authoring context"):
        service.retry_job(
            expected=base, request_id="oversize-retry-1234", job=job, acknowledge_unknown=False
        )
    assert service.store.base() == base
    assert [
        tuple(row)
        for row in controller.conn.execute(
            "SELECT * FROM authoring_attempts WHERE job_id=?", (job,)
        )
    ] == attempts
    assert not controller.conn.execute(
        "SELECT 1 FROM authoring_receipts WHERE request_id='oversize-retry-1234'"
    ).fetchone()


def test_rework1_manual_text_survives_investigation_and_generation(
    controller, db_path, tmp_path, monkeypatch
):
    import threading

    from harness.authoring import AuthoringService
    from harness.authoring_worker import AuthoringWorker

    task = controller.create_task("Manual draft investigation", "trivial")
    service = AuthoringService(controller, tmp_path, task.id)
    body = "## Exact developer text\nPreserve all spacing.\n"
    service.save_spec(
        expected=service.store.base(), request_id="manual-worker-save-1234", body=body
    )
    service.start(
        expected=service.store.base(), request_id="manual-worker-start-1234", host="codex"
    )
    observed = threading.Event()
    captured = []

    def investigate(controller, task_id, root):
        controller.workspace(task_id).checkpoint(
            "investigation", {"decisions": [], "repo_root": str(root)}
        )

    monkeypatch.setattr("harness.knowledge.integration.investigate_with_context", investigate)

    class Provider:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            captured.append(inputs)
            observed.set()
            return output()

    worker = AuthoringWorker(db_path, tmp_path, provider_factory=Provider)
    worker.start()
    try:
        assert observed.wait(10)
    finally:
        worker.close()
    assert not worker.thread.is_alive()
    assert captured[0]["body"] == body
    assert service.snapshot()["working"]["body"] == body
    assert all(job["state"] == "succeeded" for job in service.snapshot()["jobs"])


def test_rework1_foreign_only_work_does_not_create_lock(controller, db_path, tmp_path):
    from harness.authoring import AuthoringService
    from harness.authoring_worker import AuthoringWorker

    task = controller.create_task("Foreign only")
    foreign = AuthoringService(controller, tmp_path / "foreign", task.id)
    foreign.start(expected=foreign.store.base(), request_id="foreign-only-1234", host="codex")
    worker = AuthoringWorker(db_path, tmp_path)
    worker.start()
    worker.close()
    assert worker.thread is None and worker.lock is None
    assert not db_path.with_suffix(db_path.suffix + ".authoring.lock").exists()


def test_rework1b_schema17_eligibility_is_read_only_and_ineligible(tmp_path, monkeypatch):
    import sqlite3

    from harness import db
    from harness.authoring_worker import AuthoringWorker
    from harness.controller import HarnessController

    original = db._migrations
    path = tmp_path / "schema17.db"
    with monkeypatch.context() as patch:
        patch.setattr(db, "_migrations", lambda: [m for m in original() if m[0] <= 17])
        connection = db.connect(path)
        task = HarnessController(connection).create_task("Retained old task")
        connection.close()
    worker = AuthoringWorker(path, tmp_path)
    assert worker._eligible() is False
    worker.start()
    assert worker.thread is None and worker.lock is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 17
        assert (
            connection.execute("SELECT request FROM tasks WHERE id=?", (task.id,)).fetchone()[0]
            == "Retained old task"
        )
    assert not path.with_suffix(".db.authoring.lock").exists()


@pytest.mark.parametrize("broken", ["recorded18", "partial"])
def test_rework1b_broken_authoring_schema_is_not_ineligible(tmp_path, monkeypatch, broken):
    import sqlite3

    from harness import db
    from harness.authoring_worker import AuthoringWorker

    path = tmp_path / "broken.db"
    original = db._migrations
    with monkeypatch.context() as patch:
        if broken == "partial":
            patch.setattr(db, "_migrations", lambda: [m for m in original() if m[0] <= 17])
        connection = db.connect(path)
        if broken == "recorded18":
            connection.execute("DROP TABLE authoring_jobs")
        else:
            connection.execute("CREATE TABLE authoring_jobs(id TEXT)")
        connection.close()
    with pytest.raises(sqlite3.OperationalError, match="schema"):
        AuthoringWorker(path, tmp_path)._eligible()
