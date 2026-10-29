# ruff: noqa: F811
"""Production epoch fences tested at deterministic cross-process action boundaries."""

import multiprocessing
import sys
from contextlib import contextmanager

import pytest

from al.tests.runtime_run_helpers import launched  # noqa: F401
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution
from tests.test_browser_validation import delivery, prepared  # noqa: F401
from tests.test_dashboard_decision_retry import decision_fixture  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401


def _hold(execution, exclusive, ready, release):
    with epoch_action_lock(execution, exclusive=exclusive):
        ready.set()
        assert release.wait(30)


@contextmanager
def held(execution, *, exclusive=True):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold, args=(execution, exclusive, ready, release))
    child.start()
    try:
        assert ready.wait(15), "lock holder did not reach the barrier"
        yield
    finally:
        release.set()
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join()
        assert child.exitcode == 0


def test_shared_claims_remain_parallel_but_exclude_transition(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    with held(execution, exclusive=False):
        with epoch_action_lock(execution):
            pass
        with pytest.raises(ExecutionConflict, match="in progress"):
            with epoch_action_lock(execution, exclusive=True):
                pass


@pytest.mark.skipif(sys.platform != "darwin", reason="genuine macOS successor preparation")
@pytest.mark.parametrize("action", ["resume", "reconnect", "reconcile", "delivery"])
def test_activation_between_public_check_and_worker_lock_refuses_old_request(
    registered, monkeypatch, action
):
    import harness.execution_recovery as service
    import harness.execution_worker as worker
    from harness.execution_runtime_recovery import activate_runtime, prepare_runtime
    from harness.execution_store import bind_host

    _, _, controller, _, _, execution, _ = registered[0]
    eid = execution["id"]
    bind_host(controller.conn, eid, registered[0][0].registry.root, "test-owner")
    if action == "delivery":
        import harness.delivery_store as delivery_service
        from harness.delivery_models import DeliveryConfig

        delivery_service.configure_delivery(
            controller,
            eid,
            DeliveryConfig(
                "https://gitlab.example", "group/project", "candidate", "main", "staging"
            ),
        )
        old_delivery = delivery_service.get_delivery(controller.conn, eid)
    prepared = prepare_runtime(
        controller,
        eid,
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
        request_id="race-prepare",
        reason="Repair the immutable fixture runtime without resuming its native session",
    )
    target = (
        delivery_service if action == "delivery" else (service if action == "reconcile" else worker)
    )
    lock_name = "epoch_action_lock" if action == "delivery" else "worker_lock"
    real_lock = getattr(target, lock_name)
    activated = []

    @contextmanager
    def interleaved(execution, **kwargs):
        # This is exactly the public-check/reservation boundary. Restore only our seam
        # before the genuine issuer takes the same lock; keep fixture HOME intact.
        monkeypatch.setattr(target, lock_name, real_lock)
        activated.append(
            activate_runtime(
                controller,
                eid,
                request_id="race-prepare",
                preparation_sha256=prepared["preparation_sha256"],
                expected_epoch_id="legacy-0",
                expected_authority_generation=0,
            )
        )
        with real_lock(execution, **kwargs):
            yield

    monkeypatch.setattr(target, lock_name, interleaved)
    if action == "delivery":

        def call():
            return delivery_service.claim_operation(
                controller.conn, old_delivery, "push", "a" * 40, {"branch": "race"}
            )
    elif action == "reconcile":

        def call():
            return service.reconcile_execution(
                controller,
                eid,
                outcome="exited",
                evidence="Inspected exact disposable native fixture exit; no child remains",
            )
    else:

        def call():
            return getattr(service, action + "_execution")(controller, eid)

    with pytest.raises(ValueError, match="stale_epoch"):
        call()
    assert activated[0]["state"] == "ACTIVE"
    assert controller.conn.execute("SELECT COUNT(*) FROM delivery_operations").fetchone()[0] == 0
    current = get_execution(controller.conn, eid)
    assert current["epoch_id"] == activated[0]["epoch_id"] and current["status"] == "Interrupted"
    assert (
        controller.conn.execute(
            "SELECT COUNT(*) FROM execution_attempts WHERE execution_id=?", (eid,)
        ).fetchone()[0]
        == 0
    )


def test_reconcile_rechecks_same_epoch_terminal_after_lock(controller, sample_repo, monkeypatch):
    import harness.execution_recovery as service

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    real_lock = service.worker_lock

    @contextmanager
    def completed(execution):
        controller.conn.execute(
            "UPDATE executions SET status='Completed',result='{}' WHERE id=?", (eid,)
        )
        with real_lock(execution):
            yield

    monkeypatch.setattr(service, "worker_lock", completed)
    assert (
        service.reconcile_execution(
            controller,
            eid,
            outcome="not-started",
            evidence="Inspected disposable fixture launch reservation before native spawn",
        )["status"]
        == "Completed"
    )
    assert get_execution(controller.conn, eid)["error"] is None


def test_decision_retry_and_direct_materialization_do_not_cross_transition(
    controller, decision_fixture
):
    from harness.execution_decisions import (
        materialize_decision,
        record_execution_decision,
        retry_execution_decision,
    )

    _, eid, row = decision_fixture
    before = dict(
        controller.conn.execute(
            "SELECT * FROM execution_decisions WHERE id=?", (row["id"],)
        ).fetchone()
    )
    with held(get_execution(controller.conn, eid)):
        for call in (
            lambda: record_execution_decision(
                controller,
                eid,
                "environment",
                blocker_revision=1,
                request_id="saved-request",
                verdict="alternative",
                decision_text="I will fix harness myself.",
                tasks=["A"],
                criteria=["AC-01"],
            ),
            lambda: retry_execution_decision(controller, eid, row["id"]),
            lambda: materialize_decision(controller, row["id"]),
        ):
            with pytest.raises(ExecutionConflict, match="in progress"):
                call()
    assert (
        dict(
            controller.conn.execute(
                "SELECT * FROM execution_decisions WHERE id=?", (row["id"],)
            ).fetchone()
        )
        == before
    )


def test_browser_claim_and_delivery_claim_cannot_enter_transition(delivery, monkeypatch):
    from harness.browser_store import claim_run
    from harness.browser_validation import approve_browser
    from harness.delivery_store import claim_operation, get_delivery
    from harness.execution_runtime_recovery import _locks, _settled

    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="race-browser", attempt=1, client=api
    )
    existing_delivery = get_delivery(controller.conn, eid)
    with held(get_execution(controller.conn, eid)):
        with pytest.raises(ExecutionConflict, match="in progress"):
            claim_run(controller, saved["id"])
        with pytest.raises(ExecutionConflict, match="in progress"):
            claim_operation(
                controller.conn, existing_delivery, "push", "a" * 40, {"branch": "race"}
            )
    assert controller.conn.execute("SELECT COUNT(*) FROM browser_runs").fetchone()[0] == 0
    # Inverse ordering: durable claims exclude the actual recovery census.
    claim_run(controller, saved["id"])
    with (
        _locks(get_execution(controller.conn, eid)),
        pytest.raises(ExecutionConflict, match="settled browser"),
    ):
        _settled(controller, get_execution(controller.conn, eid))
    controller.conn.execute("UPDATE browser_runs SET state='Completed'")
    operation, _ = claim_operation(
        controller.conn, existing_delivery, "push", "a" * 40, {"branch": "race"}
    )
    for state in ("Dispatching", "Uncertain"):
        controller.conn.execute(
            "UPDATE delivery_operations SET state=? WHERE id=?", (state, operation)
        )
        with (
            _locks(get_execution(controller.conn, eid)),
            pytest.raises(ExecutionConflict, match="settled external"),
        ):
            _settled(controller, get_execution(controller.conn, eid))


def test_resume_rechecks_process_settlement_inside_reservation(
    controller, sample_repo, monkeypatch
):
    import os
    import uuid

    import harness.execution_worker as worker
    from harness.execution_recovery import resume_execution

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    controller.conn.execute(
        "UPDATE executions SET status='Interrupted',native_id=? WHERE id=?",
        (str(uuid.uuid4()), eid),
    )
    real_lock = worker.worker_lock

    @contextmanager
    def newly_uncertain(execution, **kwargs):
        controller.conn.execute(
            "INSERT INTO execution_attempts "
            "(id,execution_id,token,mode,status,child_pid,created_at,updated_at) "
            "VALUES ('uncertain',?,'token','resume','Running',?,'now','now')",
            (eid, os.getpid()),
        )
        with real_lock(execution, **kwargs):
            yield

    monkeypatch.setattr(worker, "worker_lock", newly_uncertain)
    with pytest.raises(ExecutionConflict, match="live or its outcome"):
        resume_execution(controller, eid)
    assert get_execution(controller.conn, eid)["status"] == "Interrupted"
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 1
