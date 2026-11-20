"""Cooperative session delivery uses committed domain state, never another provider."""

import pytest

from tests.planning_session_fixture import new_cap, request
from tests.planning_session_fixture import session as session


def test_start_attaches_atomically_without_standalone_job(session, controller):
    service, receipt, caller, cap = session
    assert receipt["session"]["mode"] == "attached"
    assert controller.conn.execute("SELECT count(*) FROM authoring_jobs").fetchone()[0] == 0
    assert service.status()["pendingCount"] == 1
    assert "secret" not in str(receipt)


def test_received_ack_does_not_apply_saved_browser_input(session):
    from harness.authoring import AuthoringService

    service, receipt, caller, cap = session
    authoring = AuthoringService(service.controller, service.root, receipt["task"])
    authoring.followup(
        expected=authoring.store.base(),
        request_id="browser-followup-001",
        body="Keep archived exports unchanged",
    )
    delivery = service.wait(
        request(service, receipt, "wait-fixture-001"), caller, cap, timeout_seconds=0
    )
    assert any(e["kind"] == "authoring.followup" for e in delivery["events"])
    before = service.base()
    ack = request(service, receipt, "ack-fixture-001") | {
        "delivery_id": delivery["delivery_id"],
        "event_ids": [e["id"] for e in delivery["events"]],
    }
    service.ack_received(ack, caller, cap)
    assert service.status()["pendingCount"] == len(delivery["events"])
    assert service.status()["appliedCount"] == 0
    assert service.base() == before
    publication = request(service, receipt, "publish-fixture-001") | {
        "consumes": ack["event_ids"],
        "content": {
            "task_message": {"text": "I received the saved requirement and will preserve it."}
        },
    }
    result = service.publish(publication, caller, cap)
    assert service.status()["pendingCount"] == 0
    assert service.publish(publication, caller, cap) == result
    assert service.status()["appliedCount"] == len(delivery["events"])


def test_start_replay_and_rollback_leave_one_task(session, tmp_path, monkeypatch):
    from harness.planning_session import PlanningSessionService
    from harness.planning_session_store import PlanningSessionStore

    service, receipt, caller, cap = session
    payload = {
        "schema_version": 1,
        "project": service.project,
        "request_id": "session-start-fixture",
        "request": "AR-2906",
        "complexity": "trivial",
    }
    assert service.start(payload, caller, cap) == receipt
    with pytest.raises(ValueError, match="SESSION_REQUEST_CONFLICT"):
        service.start(payload | {"request": "AR-2907"}, caller, cap)
    original = PlanningSessionStore.record_receipt

    def fail(self, *args):
        if args[0].startswith("session-start:"):
            raise ValueError("injected startup receipt failure")
        return original(self, *args)

    monkeypatch.setattr(PlanningSessionStore, "record_receipt", fail)
    replacement = new_cap(service, caller, "rollback-start-001", tmp_path / "capabilities")
    with pytest.raises(ValueError, match="injected"):
        PlanningSessionService(service.controller, service.root).start(
            payload | {"request_id": "rollback-start-001"}, caller, replacement
        )
    assert service.conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert service.conn.execute("SELECT count(*) FROM planning_session_epochs").fetchone()[0] == 1


def test_failed_publication_after_received_ack_remains_retryable(session, monkeypatch):
    from harness.planning_session_store import PlanningSessionStore

    service, receipt, caller, cap = session
    delivery = service.wait(
        request(service, receipt, "wait-before-failure"), caller, cap, timeout_seconds=0
    )
    ids = [e["id"] for e in delivery["events"]]
    service.ack_received(
        request(service, receipt, "ack-before-failure")
        | {"delivery_id": delivery["delivery_id"], "event_ids": ids},
        caller,
        cap,
    )
    before = service.conn.execute("SELECT count(*) FROM authoring_contributions").fetchone()[0]
    original = PlanningSessionStore.record_receipt

    def fail(self, *args):
        if args[0].endswith(":publish"):
            raise ValueError("injected application receipt failure")
        return original(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(PlanningSessionStore, "record_receipt", fail)
        with pytest.raises(ValueError, match="injected"):
            service.publish(
                request(service, receipt, "publish-will-rollback")
                | {
                    "consumes": ids,
                    "content": {"task_message": {"text": "A response that must roll back"}},
                },
                caller,
                cap,
            )
    assert (
        service.conn.execute("SELECT count(*) FROM authoring_contributions").fetchone()[0] == before
    )
    assert service.status()["pendingCount"] == len(ids) and service.status()["appliedCount"] == 0
    again = service.wait(
        request(service, receipt, "wait-redelivered-001"), caller, cap, timeout_seconds=0
    )
    assert [e["id"] for e in again["events"]] == ids


def test_takeover_preserves_pending_input_and_old_epoch_cannot_publish(session, tmp_path):
    service, receipt, caller, cap = session
    delivery = service.wait(
        request(service, receipt, "wait-before-takeover"), caller, cap, timeout_seconds=0
    )
    replacement = new_cap(service, caller, "explicit-takeover-001", tmp_path / "capabilities")
    attach = {
        "schema_version": 1,
        "project": service.project,
        "task": service.task_id,
        "request_id": "explicit-takeover-001",
        "expected": service.base(),
        "previous_epoch": receipt["epoch"],
    }
    newer = service.attach(attach, caller, replacement, takeover=True)
    assert newer["epoch"] == receipt["epoch"] + 1
    with pytest.raises(ValueError, match="SESSION_OWNER_LOST"):
        service.publish(
            request(service, receipt, "stale-owner-publish")
            | {"consumes": [], "content": {"task_message": {"text": "Must not commit"}}},
            caller,
            cap,
        )
    next_delivery = service.wait(
        request(service, newer, "wait-new-owner-001"), caller, replacement, timeout_seconds=0
    )
    assert [e["id"] for e in next_delivery["events"]] == [e["id"] for e in delivery["events"]]


@pytest.mark.parametrize(
    "wall_delta,mono_delta,expected",
    [
        (301, 301, "expired"),
        (-10, 1, "clock_uncertain"),
        (1, -1, "clock_uncertain"),
        (20, 1, "clock_uncertain"),
    ],
)
def test_clock_expiry_never_grants_standalone_or_revives_owner(
    session, wall_delta, mono_delta, expected
):
    service, receipt, caller, cap = session
    contact = service.store.contact()
    service.clock = lambda: (
        contact["last_wall"] + wall_delta,
        contact["last_monotonic"] + mono_delta,
    )
    assert service.status()["state"] == expected
    assert service.status()["mode"] == "attached"
    with pytest.raises(ValueError, match="SESSION_CLOCK_UNCERTAIN|SESSION_LEASE_EXPIRED"):
        service.wait(
            request(service, receipt, "wait-expired-owner"), caller, cap, timeout_seconds=0
        )


def test_large_saved_spec_delivers_reference_and_metadata_does_not_change_base(session):
    from harness.authoring import AuthoringService

    service, receipt, caller, cap = session
    authoring = AuthoringService(service.controller, service.root, service.task_id)
    body = "## Full saved specification\n" + "a" * 90000
    authoring.save_spec(expected=authoring.store.base(), request_id="large-browser-spec", body=body)
    before = service.base()
    delivery = service.wait(
        request(service, receipt, "wait-large-spec-001"), caller, cap, timeout_seconds=0
    )
    assert any(e["kind"] == "authoring.save-spec" for e in delivery["events"])
    assert body not in str(delivery)
    assert authoring.snapshot()["working"]["body"] == body
    assert service.base() == before


def test_actual_foreground_wait_wakes_on_other_connection_browser_save(session, db_path):
    import threading
    import time

    from harness.authoring import AuthoringService
    from harness.controller import HarnessController
    from harness.db import connect
    from harness.planning_session import PlanningSessionService

    service, receipt, caller, cap = session
    initial = service.wait(
        request(service, receipt, "drain-initial-event"), caller, cap, timeout_seconds=0
    )
    service.publish(
        request(service, receipt, "consume-initial-event")
        | {
            "consumes": [e["id"] for e in initial["events"]],
            "content": {"task_message": {"text": "Ready for input"}},
        },
        caller,
        cap,
    )
    entered = threading.Event()
    result = []
    errors = []
    payload = request(service, receipt, "foreground-wait-001")

    def waiting():
        conn = connect(db_path)
        try:
            bridge = PlanningSessionService(HarnessController(conn), service.root, service.task_id)
            entered.set()
            result.append(bridge.wait(payload, caller, cap, timeout_seconds=3))
        except BaseException as error:
            errors.append(error)
        finally:
            conn.close()

    thread = threading.Thread(target=waiting)
    thread.start()
    try:
        assert entered.wait(2)
        deadline = time.monotonic() + 2
        while not service.store.contact().get("wait_id") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.store.contact()["wait_id"]
        authoring = AuthoringService(service.controller, service.root, service.task_id)
        authoring.followup(
            expected=authoring.store.base(),
            request_id="wake-owner-browser",
            body="Save this input once",
        )
    finally:
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert len(result) == 1 and any(e["kind"] == "authoring.followup" for e in result[0]["events"])
    assert (
        service.conn.execute(
            "SELECT count(*) FROM authoring_contributions WHERE kind='followup'"
        ).fetchone()[0]
        == 1
    )


def test_browser_and_tui_answer_race_keeps_exactly_one_committed_response(session, db_path):
    import threading

    from harness.authoring import AuthoringService
    from harness.controller import HarnessController
    from harness.db import connect
    from harness.planning_session import PlanningSessionService
    from tests.planning_session_fixture import attached_payload

    bridge, receipt, caller, cap = session
    bridge.publish(attached_payload(bridge, receipt, "source-before-answer-race"), caller, cap)
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    qid = service.snapshot()["interview"]["current_question_id"]
    target = next(
        t
        for t in service.snapshot()["revision_targets"]
        if t["target"] == f"question-response:{qid}"
    )
    expected = bridge.base()
    publication = request(bridge, receipt, "tui-concurrent-answer") | {
        "consumes": [],
        "content": {
            "user_response": {
                "target": target["target"],
                "target_revision": target["revision"],
                "mode": "answer",
                "text": "  Exact TUI user answer  ",
            }
        },
    }
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def save(tui):
        conn = connect(db_path)
        try:
            controller = HarnessController(conn)
            barrier.wait(3)
            if tui:
                results.append(
                    PlanningSessionService(controller, bridge.root, bridge.task_id).publish(
                        publication, caller, cap
                    )
                )
            else:
                s = AuthoringService(controller, bridge.root, bridge.task_id)
                results.append(
                    s.answer(
                        expected=expected["authoring"],
                        request_id="browser-concurrent-answer",
                        question=qid,
                        mode="answer",
                        answer="Exact browser answer",
                    )
                )
        except ValueError as error:
            errors.append(error)
        finally:
            conn.close()

    threads = [threading.Thread(target=save, args=(tui,)) for tui in (False, True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1 and len(errors) == 1
    assert bridge.controller.workspace(bridge.task_id).question(qid).answer in {
        "Exact browser answer",
        "Exact TUI user answer",
    }
    assert (
        bridge.conn.execute(
            "SELECT count(*) FROM authoring_contributions WHERE kind='answer'"
        ).fetchone()[0]
        == 1
    )
    if bridge.controller.workspace(bridge.task_id).question(qid).answer == "Exact TUI user answer":
        import json

        raw = bridge.conn.execute(
            "SELECT payload_json FROM authoring_contributions WHERE kind='answer'"
        ).fetchone()[0]
        assert json.loads(raw)["answer"] == "  Exact TUI user answer  "
    assert bridge.base()["input_revision"] == expected["input_revision"] + 1


def test_real_http_save_history_regenerate_and_explicit_recovery(session, db_path, monkeypatch):
    import threading

    from harness.authoring_worker import AuthoringWorker
    from harness.dashboard.server import DashboardServer
    from tests.test_dashboard_authoring_browser import _http_json

    bridge, receipt, caller, cap = session
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: None)
    server = DashboardServer(db_path, bridge.task_id, project_root=bridge.root)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        code, result = _http_json(
            server,
            "/api/authoring/followup",
            method="POST",
            body={
                "task": bridge.task_id,
                "request_id": "http-session-save",
                "expected": bridge.base()["authoring"],
                "body": "Saved browser message",
            },
        )
        assert code == 200, result
        code, history = _http_json(server, "/api/session/events?task=" + bridge.task_id)
        assert code == 200 and any(
            e["kind"] == "authoring.followup" and e["deliveryState"] == "saved"
            for e in history["session"]["events"]
        )
        code, result = _http_json(
            server,
            "/api/authoring/regenerate",
            method="POST",
            body={
                "task": bridge.task_id,
                "request_id": "http-session-regenerate",
                "expected": bridge.base()["authoring"],
                "host": "claude",
            },
        )
        assert (
            code == 200
            and result["state"] == "waiting_for_attached_agent"
            and result["job"] is None
        )
        assert bridge.conn.execute("SELECT count(*) FROM authoring_jobs").fetchone()[0] == 0
        payload = {
            "task": bridge.task_id,
            "request_id": "http-explicit-pause",
            "expected": bridge.base(),
            "epoch": receipt["epoch"],
            "action": "pause",
        }
        code, result = _http_json(server, "/api/session/action", method="POST", body=payload)
        assert (
            code == 200
            and result["session"]["mode"] == "attached"
            and result["session"]["state"] in {"expired", "disconnected"}
        )
        with pytest.raises(ValueError, match="SESSION_LEASE_EXPIRED"):
            bridge.publish(
                request(bridge, receipt, "publish-after-pause")
                | {"consumes": [], "content": {"task_message": {"text": "Must not commit"}}},
                caller,
                cap,
            )
        payload.update(
            request_id="http-explicit-standalone",
            expected=bridge.base(),
            action="choose-standalone",
        )
        code, result = _http_json(server, "/api/session/action", method="POST", body=payload)
        assert (
            code == 200
            and result["session"]["mode"] == "standalone"
            and result["provider_invoked"] is False
        )
        assert _http_json(server, "/api/session/action", method="POST", body=payload)[1] == result
        assert bridge.conn.execute("SELECT count(*) FROM authoring_jobs").fetchone()[0] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def test_workspace_proposal_event_replay_atomicity_and_input_revision_fence(session, monkeypatch):
    import sqlite3

    from harness.authoring_contract import digest
    from harness.cli_spec import spec_store
    from harness.dashboard.workspace_view import source_snapshot
    from harness.workspace_proposals import WorkspaceProposalService

    bridge, receipt, caller, cap = session
    store = spec_store(bridge.controller, bridge.task_id, str(bridge.root / "docs/specs"))

    def source():
        return source_snapshot(
            bridge.controller, bridge.controller.get_task(bridge.task_id), store, None
        )

    initial = bridge.base()
    service = WorkspaceProposalService(bridge.controller, bridge.task_id)
    operation = [
        {"type": "requirement", "target": "request", "text": "Explicit proposed request edit"}
    ]
    base = digest(source())
    proposal, created = service.add(base, operation, "session-graph-proposal", store, lambda: None)
    assert created
    replay, created = service.add(base, operation, "session-graph-proposal", store, lambda: None)
    assert not created and replay == proposal
    events = [e for e in bridge.status()["events"] if e["kind"] == "workspace.proposed"]
    assert len(events) == 1 and events[0]["payload"]["payload"]["id"] == proposal["id"]
    assert bridge.base()["authoring"] == initial["authoring"]
    assert bridge.base()["input_revision"] == initial["input_revision"] + 1
    stale = request(bridge, receipt, "publish-before-graph-change") | {
        "expected": initial,
        "consumes": [],
        "content": {"task_message": {"text": "Stale response"}},
    }
    with pytest.raises(ValueError, match="SESSION_BASE_STALE"):
        bridge.publish(stale, caller, cap)
    bridge.conn.execute(
        "CREATE TRIGGER fail_session_event BEFORE INSERT ON "
        "planning_session_events WHEN NEW.kind='workspace.proposed' BEGIN "
        "SELECT RAISE(ABORT,'fixture session event failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="fixture session event"):
        service.add(digest(source()), operation, "graph-event-rollback", store, lambda: None)
    assert bridge.conn.execute("SELECT count(*) FROM workspace_proposals").fetchone()[0] == 1


def test_received_does_not_consume_arbitrary_or_undelivered_events(session):
    service, receipt, caller, cap = session
    event = service.status()["events"][0]["id"]
    with pytest.raises(ValueError, match="SESSION_OPERATION_INCOMPATIBLE"):
        service.publish(
            request(service, receipt, "consume-undelivered-event")
            | {"consumes": [event], "content": {"task_message": {"text": "Must refuse"}}},
            caller,
            cap,
        )
    assert service.status()["pendingCount"] == 1


def test_reboot_requires_new_epoch_even_when_clock_deltas_look_continuous(session, monkeypatch):
    bridge, receipt, caller, cap = session
    before = bridge.base()
    from harness.planning_session_clock import BootObservation

    monkeypatch.setattr(
        "harness.planning_session_clock._validated_observation",
        lambda: BootObservation("different-boot"),
    )
    assert bridge.status()["state"] == "clock_uncertain" and bridge.status()["mode"] == "attached"
    with pytest.raises(ValueError, match="SESSION_CLOCK_UNCERTAIN"):
        bridge.publish(
            request(bridge, receipt, "post-reboot-publish")
            | {"consumes": [], "content": {"task_message": {"text": "Must not commit"}}},
            caller,
            cap,
        )
    assert bridge.base() == before


def test_concurrent_wait_refuses_and_takeover_wakes_old_wait_without_ack(
    session, db_path, tmp_path
):
    import threading
    import time

    from harness.controller import HarnessController
    from harness.db import connect
    from harness.planning_session import PlanningSessionService

    bridge, receipt, caller, cap = session
    delivery = bridge.wait(
        request(bridge, receipt, "drain-before-long-wait"), caller, cap, timeout_seconds=0
    )
    bridge.publish(
        request(bridge, receipt, "resolve-before-long-wait")
        | {
            "consumes": [e["id"] for e in delivery["events"]],
            "content": {"task_message": {"text": "Ready"}},
        },
        caller,
        cap,
    )
    errors = []
    entered = threading.Event()
    payload = request(bridge, receipt, "old-owner-foreground-wait")

    def waiting():
        conn = connect(db_path)
        try:
            entered.set()
            PlanningSessionService(HarnessController(conn), bridge.root, bridge.task_id).wait(
                payload, caller, cap, timeout_seconds=3
            )
        except ValueError as error:
            errors.append(str(error))
        finally:
            conn.close()

    thread = threading.Thread(target=waiting)
    thread.start()
    try:
        assert entered.wait(2)
        deadline = time.monotonic() + 2
        while not bridge.store.contact().get("wait_id") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bridge.store.contact()["wait_id"]
        with pytest.raises(ValueError, match="SESSION_WAIT_ACTIVE"):
            bridge.wait(
                request(bridge, receipt, "competing-foreground-wait"),
                caller,
                cap,
                timeout_seconds=0,
            )
        replacement = new_cap(bridge, caller, "takeover-during-wait", tmp_path / "capabilities")
        newer = bridge.attach(
            {
                "schema_version": 1,
                "project": bridge.project,
                "task": bridge.task_id,
                "request_id": "takeover-during-wait",
                "expected": bridge.base(),
                "previous_epoch": receipt["epoch"],
            },
            caller,
            replacement,
            takeover=True,
        )
    finally:
        thread.join(5)
    assert not thread.is_alive() and errors and "SESSION_OWNER_LOST" in errors[0]
    assert bridge.store.contact()["wait_id"] is None and newer["epoch"] == 2


def test_session_schema_absence_partial_and_read_only_projection():
    import sqlite3

    from harness.planning_session_store import PlanningSessionStore, schema_available

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        assert not schema_available(conn)
        assert PlanningSessionStore(conn, "missing").snapshot()["mode"] == "manual"
        assert conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0] == 0
        conn.execute("CREATE TABLE planning_session_owners(task_id TEXT)")
        with pytest.raises(sqlite3.OperationalError, match="Incomplete"):
            schema_available(conn)
    finally:
        conn.close()


def test_explicit_detach_replay_returns_receipt_without_reviving_old_owner(session):
    bridge, receipt, caller, cap = session
    payload = request(bridge, receipt, "detach-exact-replay")
    result = bridge.detach(payload, caller, cap, destination="manual")
    assert bridge.detach(payload, caller, cap, destination="manual") == result
    assert bridge.status()["mode"] == "manual" and bridge.status()["epoch"] == 2
    with pytest.raises(ValueError, match="SESSION_OWNER_LOST"):
        bridge.publish(
            request(bridge, receipt, "detached-old-owner")
            | {"consumes": [], "content": {"task_message": {"text": "Must refuse"}}},
            caller,
            cap,
        )


def test_local_boot_token_and_separate_connection_restart_preserve_same_boot(session, db_path):
    from harness.controller import HarnessController
    from harness.db import connect
    from harness.planning_session import PlanningSessionService
    from harness.planning_session_clock import boot_identity

    bridge, receipt, caller, cap = session
    assert boot_identity() is not None  # actual supported local kernel/file path, not injected
    conn = connect(db_path)
    try:
        restarted = PlanningSessionService(HarnessController(conn), bridge.root, bridge.task_id)
        assert restarted.status()["state"] in {"listening", "busy"}
        assert restarted.base() == bridge.base()
        restarted.publish(
            request(restarted, receipt, "restart-same-boot-message")
            | {
                "consumes": [],
                "content": {"task_message": {"text": "Same owner after local client restart"}},
            },
            caller,
            cap,
        )
    finally:
        conn.close()


def test_lost_capability_cannot_duplicate_start_request(session, tmp_path):
    from harness.planning_session import PlanningSessionService

    service, receipt, caller, cap = session
    replacement = new_cap(
        service, caller, "session-start-fixture", tmp_path / "different-capabilities"
    )
    with pytest.raises(ValueError, match="SESSION_REQUEST_CONFLICT"):
        PlanningSessionService(service.controller, service.root).start(
            {
                "schema_version": 1,
                "project": service.project,
                "request_id": "session-start-fixture",
                "request": "AR-2906",
                "complexity": "trivial",
            },
            caller,
            replacement,
        )
    assert service.conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_protocol_compatible_connection_still_requires_exact_owner_capability(session, tmp_path):
    service, receipt, caller, cap = session
    wrong = new_cap(service, caller, "wrong-owner-capability", tmp_path / "capabilities")
    before = service.base()
    with pytest.raises(ValueError, match="SESSION_CAPABILITY_INVALID"):
        service.publish(
            request(service, receipt, "wrong-owner-publication")
            | {"consumes": [], "content": {"task_message": {"text": "Not authorized"}}},
            caller,
            wrong,
        )
    assert service.base() == before
    assert service.conn.execute("SELECT count(*) FROM authoring_contributions").fetchone()[0] == 0


def test_conversation_cursor_keeps_all_published_messages_accessible(session):
    bridge, receipt, caller, cap = session
    for index in range(35):
        bridge.publish(
            request(bridge, receipt, f"conversation-message-{index:03}")
            | {
                "consumes": [],
                "content": {"task_message": {"text": str(index) + " " + "x" * 3900}},
            },
            caller,
            cap,
        )
    newest = bridge.status()
    assert newest["nextBefore"] is not None
    collected = []
    page = newest
    while True:
        collected.extend(page["events"])
        if page["nextBefore"] is None:
            break
        page = bridge.status(before=page["nextBefore"])
    assert len({e["id"] for e in collected}) == 36
    assert len([e for e in collected if e["kind"] == "session.task_message"]) == 35


def test_prior_epoch_received_ack_is_history_not_current_owner_receipt(session, tmp_path):
    bridge, receipt, caller, cap = session
    delivery = bridge.wait(
        request(bridge, receipt, "receive-before-takeover"), caller, cap, timeout_seconds=0
    )
    ids = [e["id"] for e in delivery["events"]]
    bridge.ack_received(
        request(bridge, receipt, "ack-before-owner-change")
        | {"delivery_id": delivery["delivery_id"], "event_ids": ids},
        caller,
        cap,
    )
    replacement = new_cap(bridge, caller, "change-received-owner", tmp_path / "capabilities")
    newer = bridge.attach(
        {
            "schema_version": 1,
            "project": bridge.project,
            "task": bridge.task_id,
            "request_id": "change-received-owner",
            "expected": bridge.base(),
            "previous_epoch": receipt["epoch"],
        },
        caller,
        replacement,
        takeover=True,
    )
    event = next(e for e in bridge.status()["events"] if e["id"] == ids[0])
    assert event["deliveryState"] == "saved" and event["receivedAt"] is None
    assert event["historicalReceivedEpoch"] == receipt["epoch"]
    assert bridge.status()["receivedCount"] == 0 and bridge.status()["pendingCount"] == 1
    redelivered = bridge.wait(
        request(bridge, newer, "redeliver-after-owner-change"),
        caller,
        replacement,
        timeout_seconds=0,
    )
    assert [e["id"] for e in redelivered["events"]] == ids


def test_concurrent_identical_startup_retries_commit_one_task(controller, tmp_path, db_path):
    import threading
    import uuid

    from harness.authoring import project_identity
    from harness.controller import HarnessController
    from harness.db import connect
    from harness.planning_session import PlanningSessionService
    from harness.planning_session_identity import prepare_capability_artifact, resolve_caller

    root = tmp_path / "project"
    root.mkdir()
    caller = resolve_caller(
        "codex", {"CODEX_SESSION_ID": str(uuid.uuid4()), "CODEX_THREAD_ID": str(uuid.uuid4())}
    )
    request = {
        "schema_version": 1,
        "project": project_identity(root),
        "request_id": "concurrent-same-start",
        "request": "AR-2906",
        "complexity": "trivial",
    }
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def start():
        conn = connect(db_path)
        try:
            barrier.wait(3)
            cap = prepare_capability_artifact(
                {"project": request["project"], "request_id": request["request_id"]},
                caller,
                tmp_path / "capabilities",
            )
            results.append(
                PlanningSessionService(HarnessController(conn), root).start(request, caller, cap)
            )
        except BaseException as error:
            errors.append(str(error))
        finally:
            conn.close()

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(6)
    assert all(not t.is_alive() for t in threads)
    if errors:
        pytest.fail(repr(errors))
    assert len(results) == 2 and results[0] == results[1]
    assert controller.conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_takeover_between_prepare_and_commit_rejects_old_publication(
    session, tmp_path, db_path, monkeypatch
):
    import threading

    import harness.planning_session_publication as publication
    from harness.controller import HarnessController
    from harness.db import connect
    from harness.planning_session import PlanningSessionService

    bridge, receipt, caller, cap = session
    entered = threading.Event()
    release = threading.Event()
    errors = []
    original = publication.prepare

    def held(*args):
        result = original(*args)
        entered.set()
        assert release.wait(4)
        return result

    monkeypatch.setattr(publication, "prepare", held)
    payload = request(bridge, receipt, "old-publication-race") | {
        "consumes": [],
        "content": {"task_message": {"text": "Old epoch must not commit"}},
    }

    def publishing():
        conn = connect(db_path)
        try:
            PlanningSessionService(HarnessController(conn), bridge.root, bridge.task_id).publish(
                payload, caller, cap
            )
        except ValueError as error:
            errors.append(str(error))
        finally:
            conn.close()

    thread = threading.Thread(target=publishing)
    thread.start()
    try:
        assert entered.wait(3)
        replacement = new_cap(
            bridge, caller, "takeover-before-old-commit", tmp_path / "capabilities"
        )
        bridge.attach(
            {
                "schema_version": 1,
                "project": bridge.project,
                "task": bridge.task_id,
                "request_id": "takeover-before-old-commit",
                "expected": bridge.base(),
                "previous_epoch": receipt["epoch"],
            },
            caller,
            replacement,
            takeover=True,
        )
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and errors and "SESSION_OWNER_LOST" in errors[0]
    assert bridge.conn.execute("SELECT count(*) FROM authoring_contributions").fetchone()[0] == 0


def test_ordinary_owner_preparation_and_genuine_question_flow(controller, tmp_path):
    import uuid

    from harness.authoring import AuthoringService, project_identity
    from harness.planning_session import PlanningSessionService
    from harness.planning_session_identity import prepare_capability_artifact, resolve_caller

    root = tmp_path / "ordinary-project"
    root.mkdir()
    (root / "export.py").write_text("def export_csv(accounts):\n    return accounts\n")
    caller = resolve_caller(
        "codex", {"CODEX_SESSION_ID": str(uuid.uuid4()), "CODEX_THREAD_ID": str(uuid.uuid4())}
    )
    cap = prepare_capability_artifact(
        {"project": project_identity(root), "request_id": "ordinary-start-001"},
        caller,
        tmp_path / "capabilities",
    )
    bridge = PlanningSessionService(controller, root)
    receipt = bridge.start(
        {
            "schema_version": 1,
            "project": project_identity(root),
            "request_id": "ordinary-start-001",
            "request": "Add CSV export for accounts",
            "complexity": "trivial",
        },
        caller,
        cap,
    )
    prepared = bridge.prepare_interview(
        request(bridge, receipt, "prepare-ordinary-interview"), caller, cap
    )
    assert prepared["phase"] == "interview"
    payload = request(bridge, receipt, "ordinary-genuine-question") | {
        "consumes": [],
        "content": {
            "question_proposal": {
                "concern": "functional_behavior.success_path",
                "text": "Should archived accounts be exported?",
                "rationale": "The request does not decide archive inclusion.",
                "citations": [],
                "supersedes": None,
                "changed_premise_reason": None,
            }
        },
    }
    published = bridge.publish(payload, caller, cap)
    qid = published["result"]["question"]
    authoring = AuthoringService(controller, root, bridge.task_id)
    assert authoring.snapshot()["interview"]["current_question_id"] == qid
    authoring.answer(
        expected=authoring.store.base(),
        request_id="ordinary-browser-answer",
        question=qid,
        mode="answer",
        answer="Exclude archived accounts",
    )
    assert controller.workspace(bridge.task_id).question(qid).answer == "Exclude archived accounts"
    assert authoring.snapshot()["interview"]["current_question_id"] is None
    from tests.planning_session_fixture import attached_payload

    publication = bridge.publish(
        attached_payload(
            bridge, receipt, "ordinary-repository-source", decisions=False, repository_only=True
        ),
        caller,
        cap,
    )
    assert publication["result"]["origin"] == "attached"
    assert authoring.snapshot()["investigation"]["origin"] == "attached"


def test_repository_selection_refuses_ticket_bypass(session):
    from tests.planning_session_fixture import attached_payload

    bridge, receipt, caller, cap = session
    payload = attached_payload(bridge, receipt, "repository-bypass-ticket", repository_only=True)
    with pytest.raises(ValueError, match="SESSION_OPERATION_INCOMPATIBLE"):
        bridge.publish(payload, caller, cap)
    assert bridge.conn.execute("SELECT count(*) FROM investigation_bundles").fetchone()[0] == 0


@pytest.mark.parametrize("destination", ["manual", "standalone"])
def test_material_requirement_remains_blocking_after_manual_detach(session, destination):
    from harness.authoring import AuthoringService
    from harness.review import FinalReviewer

    bridge, receipt, caller, cap = session
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    service.followup(
        expected=service.store.base(),
        request_id="new-browser-requirement",
        body="Exports must exclude archived accounts",
    )
    before = bridge.status()["material"]
    assert before["pending_material_ids"]
    bridge.detach(
        request(bridge, receipt, "mode-switch-with-pending-input"),
        caller,
        cap,
        destination=destination,
    )
    assert bridge.status()["material"] == before
    report = FinalReviewer(bridge.controller, bridge.task_id).report()
    assert report.signals["session_material"]["material_digest"] == before["material_digest"]
    assert any("material" in str(item.findings).lower() for item in report.blocked)


def test_capability_creation_retries_only_missing_lock_lookup(tmp_path, monkeypatch):
    import uuid

    import harness.planning_session_identity as identity

    caller = identity.resolve_caller("claude", {}, str(uuid.uuid4()))
    original = identity.os.open
    calls = []

    def opening(path, *args, **kwargs):
        if path == ".prepare.lock":
            calls.append(path)
            if len(calls) == 1:
                raise FileNotFoundError(2, "transient fixture lookup")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(identity.os, "open", opening)
    cap = identity.prepare_capability_artifact(
        {"project": "a" * 64, "request_id": "lock-creation-retry"},
        caller,
        tmp_path / "capabilities",
    )
    assert len(calls) == 2 and identity.read_capability(cap["path"], caller) == cap


@pytest.mark.parametrize("alter", ["base", "epoch", "capability"])
def test_ordinary_prepare_rejects_wrong_authority_atomically(controller, tmp_path, alter):
    from harness.planning_session_identity import prepare_capability_artifact
    from tests.planning_session_fixture import ordinary_owner

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    document = request(bridge, receipt, "guarded-ordinary-preparation")
    if alter == "base":
        document["expected"]["input_revision"] += 1
    elif alter == "epoch":
        document["epoch"] += 1
    else:
        cap = prepare_capability_artifact(
            {"project": bridge.project, "request_id": "different-capability"},
            caller,
            tmp_path / "other-capability",
        )
    before = bridge.conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0]
    with pytest.raises(ValueError, match="SESSION_"):
        bridge.prepare_interview(document, caller, cap)
    assert bridge.controller.get_task(bridge.task_id).phase == "investigation"
    assert bridge.conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == before


def test_ordinary_preparation_analyzer_failure_and_takeover_publish_nothing(
    controller, tmp_path, monkeypatch
):
    from tests.planning_session_fixture import new_cap, ordinary_owner

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)

    def fail(*args):
        raise ValueError("synthetic analyzer failure")

    monkeypatch.setattr("harness.analyzer.analyze", fail)
    with pytest.raises(ValueError, match="analyzer failure"):
        bridge.prepare_interview(request(bridge, receipt, "failed-ordinary-prepare"), caller, cap)
    assert bridge.controller.workspace(bridge.task_id).latest_checkpoint("investigation") is None
    from harness.investigation import InvestigationReport

    # The analyzer boundary is outside SQL, so a real owner replacement can commit here.
    def takeover(root, task_request):
        replacement = new_cap(bridge, caller, "takeover-during-prepare", tmp_path / "replacement")
        bridge.attach(
            {
                "schema_version": 1,
                "project": bridge.project,
                "task": bridge.task_id,
                "request_id": "takeover-during-prepare",
                "expected": bridge.base(),
                "previous_epoch": receipt["epoch"],
            },
            caller,
            replacement,
            takeover=True,
        )
        return InvestigationReport(str(root), None, task_request, (), (), (), ())

    monkeypatch.setattr("harness.analyzer.analyze", takeover)
    with pytest.raises(ValueError, match="SESSION_OWNER_LOST"):
        bridge.prepare_interview(request(bridge, receipt, "raced-ordinary-prepare"), caller, cap)
    assert bridge.controller.workspace(bridge.task_id).latest_checkpoint("investigation") is None


def test_observe_repository_exports_real_empty_sources_without_publication(controller, tmp_path):
    from tests.planning_session_fixture import ordinary_owner

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    (bridge.root / "__init__.py").write_bytes(b"")
    document = request(bridge, receipt, "export-actual-repository-observations")
    result = bridge.observe_repository(document, caller, cap)
    assert bridge.observe_repository(document, caller, cap) == result
    assert any(source["content"] == "" for source in result["repository"]["sources"])
    from harness.interview_coverage import validate_sources

    validate_sources(result["repository"]["sources"])
    assert bridge.conn.execute("SELECT count(*) FROM investigation_bundles").fetchone()[0] == 0
