"""Attached ownership excludes competing standalone work, including expired leases."""

import pytest

from tests.planning_session_fixture import attached_payload
from tests.planning_session_fixture import session as session


def test_attached_task_refuses_standalone_queue(session):
    import pytest

    from harness.authoring import AuthoringService
    from harness.authoring_jobs import queue_job
    from harness.db import transaction

    bridge, receipt, _, _ = session
    service = AuthoringService(bridge.controller, bridge.root, receipt["task"])
    with pytest.raises(ValueError, match="SESSION_OPERATION_INCOMPATIBLE"):
        with transaction(service.conn):
            queue_job(service, "generate", "codex")


def test_attached_bundle_is_terminal_truthful_and_working_draft_is_preserved(session):
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import claim_next

    bridge, receipt, caller, cap = session
    before = AuthoringService(bridge.controller, bridge.root, bridge.task_id).snapshot()["working"]
    payload = attached_payload(bridge, receipt, "attached-source-publish")
    result = bridge.publish(payload, caller, cap)
    assert bridge.publish(payload, caller, cap) == result
    jobs = [dict(r) for r in bridge.conn.execute("SELECT * FROM authoring_jobs")]
    assert len(jobs) == 1 and jobs[0]["kind"] == "attached_publication"
    assert (
        jobs[0]["host"] == "attached" and jobs[0]["state"] == "succeeded" and jobs[0]["claim"] == 0
    )
    assert (
        result["result"]["provider_invoked"] is False
        and result["result"]["process_started"] is False
    )
    assert (
        bridge.conn.execute(
            "SELECT count(*) FROM planning_session_publications WHERE epoch=?", (receipt["epoch"],)
        ).fetchone()[0]
        == 1
    )
    assert claim_next(bridge.conn, "must-not-run", bridge.project) is None
    snapshot = AuthoringService(bridge.controller, bridge.root, bridge.task_id).snapshot()
    assert snapshot["working"] == before
    assert snapshot["jobs"][0]["label"] == "Attached agent publication"
    assert (
        snapshot["investigation"]["origin"] == "attached"
        and snapshot["investigation"]["stage"] == "ready"
    )
    assert any(
        s["kind"] == "repository" and s["content"] == ""
        for s in snapshot["investigation"]["sources"]
    )
    assert not snapshot["investigation"]["coverage"]["accepted"]


def test_attached_bundle_rollback_removes_terminal_record_and_ack(session, monkeypatch):
    import pytest

    from harness.investigation_store import InvestigationStore

    bridge, receipt, caller, cap = session
    payload = attached_payload(bridge, receipt, "attached-source-rollback")

    def fail(*args, **kwargs):
        raise ValueError("injected after terminal receipt insertion")

    monkeypatch.setattr(InvestigationStore, "insert_bundle", fail)
    with pytest.raises(ValueError, match="injected"):
        bridge.publish(payload, caller, cap)
    for table in (
        "authoring_jobs",
        "authoring_attempts",
        "planning_session_publications",
        "investigation_bindings",
        "investigation_bundles",
        "investigation_sources",
        "planning_session_event_results",
    ):
        assert bridge.conn.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert bridge.status()["pendingCount"] == 1


def test_attached_publication_cannot_be_retried_cancelled_or_requeued(session):
    import sqlite3

    import pytest

    from harness.authoring import AuthoringService
    from harness.db import transaction
    from harness.planning_session_events import mutation_scope

    bridge, receipt, caller, cap = session
    publication = bridge.publish(
        attached_payload(bridge, receipt, "terminal-source-publish"), caller, cap
    )
    job = publication["result"]["job"]
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    for method, extra in (
        (service.cancel_job, {}),
        (service.retry_job, {"acknowledge_unknown": True}),
    ):
        with pytest.raises(ValueError, match="SESSION_OPERATION_INCOMPATIBLE"):
            method(
                expected=service.store.base(),
                request_id="forbidden-" + method.__name__.replace("_", "-"),
                job=job,
                **extra,
            )
    with pytest.raises(sqlite3.IntegrityError, match="SESSION_OPERATION_INCOMPATIBLE"):
        with transaction(bridge.conn), mutation_scope(bridge.conn, bridge.task_id):
            bridge.conn.execute("UPDATE authoring_jobs SET state='queued' WHERE id=?", (job,))


def test_unsettled_cancelled_native_attempt_prevents_attachment(session, tmp_path, monkeypatch):
    import pytest

    from harness.authoring import AuthoringService
    from harness.authoring_jobs import claim_next
    from tests.planning_session_fixture import new_cap, request

    bridge, receipt, caller, cap = session
    bridge.detach(
        request(bridge, receipt, "detach-for-native-job"), caller, cap, destination="standalone"
    )
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"available": True, "host": host}
    )
    created = service.request_regeneration(
        expected=service.store.base(), request_id="explicit-native-generate", host="codex"
    )
    claim = claim_next(bridge.conn, "fixture-native-owner", bridge.project)
    assert claim and claim["id"] == created["job"]
    service.cancel_job(
        expected=service.store.base(), request_id="cancel-unsettled-native", job=claim["id"]
    )
    replacement = new_cap(bridge, caller, "attach-after-cancel", tmp_path / "capabilities")
    with pytest.raises(ValueError, match="SESSION_STANDALONE_UNSETTLED"):
        bridge.attach(
            {
                "schema_version": 1,
                "project": bridge.project,
                "task": bridge.task_id,
                "request_id": "attach-after-cancel",
                "expected": bridge.base(),
                "previous_epoch": bridge.status()["epoch"],
            },
            caller,
            replacement,
        )
    assert bridge.status()["mode"] == "standalone"


def test_source_acceptance_survives_wait_received_ack_and_progress(session):
    from harness.authoring import AuthoringService
    from harness.interview_coverage import current_coverage
    from harness.investigation_store import review_basis
    from harness.review_package import package
    from tests.planning_session_fixture import request
    from tests.test_interview_coverage import adopt

    bridge, receipt, caller, cap = session
    bridge.publish(
        attached_payload(bridge, receipt, "source-for-exact-acceptance", decisions=False),
        caller,
        cap,
    )
    authoring = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    adopt(authoring)
    reviewed = package(bridge.controller, bridge.task_id)
    assert reviewed["content"]["origin"] == "attached"
    authoring.accept_investigation(
        expected=authoring.store.base(),
        request_id="explicit-attached-source-acceptance",
        approved_sha256=reviewed["sha256"],
        provenance="User explicitly reviewed this exact synthetic attached source package",
    )
    assert current_coverage(bridge.controller, bridge.task_id).accepted
    basis = review_basis(bridge.controller, bridge.task_id)
    delivery = bridge.wait(
        request(bridge, receipt, "wait-after-source-acceptance"), caller, cap, timeout_seconds=0
    )
    bridge.ack_received(
        request(bridge, receipt, "ack-after-source-acceptance")
        | {
            "delivery_id": delivery["delivery_id"],
            "event_ids": [e["id"] for e in delivery["events"]],
        },
        caller,
        cap,
    )
    bridge.publish(
        request(bridge, receipt, "progress-after-source-acceptance")
        | {
            "consumes": [],
            "content": {"progress": {"stage": "waiting", "text": "Waiting for genuine decisions"}},
        },
        caller,
        cap,
    )
    assert current_coverage(bridge.controller, bridge.task_id).accepted
    assert review_basis(bridge.controller, bridge.task_id) == basis
    from harness.review import FinalReviewer

    authoring.followup(
        expected=authoring.store.base(),
        request_id="material-after-exact-source-review",
        body="Add a new CSV column for archived account status",
    )
    assert not current_coverage(bridge.controller, bridge.task_id).accepted
    assert bridge.status()["material"]["pending_material_ids"]
    assert any(
        "material" in str(item.findings).lower()
        for item in FinalReviewer(bridge.controller, bridge.task_id).report().blocked
    )


def test_complete_source_publication_and_application_ack_roll_back_together(session, monkeypatch):
    import pytest

    from harness.planning_session_store import PlanningSessionStore
    from tests.planning_session_fixture import request

    bridge, receipt, caller, cap = session
    delivered = bridge.wait(
        request(bridge, receipt, "delivery-before-bundle-rollback"), caller, cap, timeout_seconds=0
    )
    original = PlanningSessionStore.record_receipt

    def fail(self, *args):
        if args[0].endswith(":publish"):
            raise ValueError("injected final application receipt failure")
        return original(self, *args)

    monkeypatch.setattr(PlanningSessionStore, "record_receipt", fail)
    payload = attached_payload(bridge, receipt, "full-bundle-ack-rollback") | {
        "consumes": [e["id"] for e in delivered["events"]]
    }
    with pytest.raises(ValueError, match="injected"):
        bridge.publish(payload, caller, cap)
    for table in (
        "authoring_jobs",
        "authoring_attempts",
        "planning_session_publications",
        "investigation_bundles",
        "investigation_sources",
        "investigation_bindings",
        "investigation_decisions",
        "planning_session_event_results",
        "questions",
    ):
        assert bridge.conn.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert bridge.conn.execute("SELECT count(*) FROM authoring_revisions").fetchone()[0] == 1
    assert bridge.status()["pendingCount"] == 1 and bridge.status()["appliedCount"] == 0


def test_manual_exact_reconciliation_handles_inputs_without_agent_receipts(session):
    import pytest

    from harness.authoring import AuthoringService
    from harness.review import FinalReviewer
    from tests.planning_session_fixture import request

    bridge, receipt, caller, cap = session
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    service.save_spec(
        expected=service.store.base(),
        request_id="manual-content-before-detach",
        body="## Export\nExclude archived accounts.\n",
    )
    bridge.detach(
        request(bridge, receipt, "explicit-manual-mode"), caller, cap, destination="manual"
    )
    selected = service.store.binding()["working_id"]
    preview = service.reconcile_preview(selected)
    entries = [
        {
            "input_id": ident,
            "disposition": "incorporated",
            "rationale": "The selected specification explicitly retains the supplied requirement.",
            "references": ["revision:" + selected],
        }
        for ident in preview["material"]["pending_material_ids"]
    ]
    args = dict(
        expected=service.store.base(),
        request_id="manual-material-reconcile",
        revision=selected,
        token=preview["token"],
        confirm_graph=True,
        acknowledge=True,
        actions=[],
        handling=entries,
    )
    with pytest.raises(ValueError, match="rationale"):
        service.reconcile(**(args | {"handling": [entries[0] | {"rationale": ""}]}))
    with pytest.raises(ValueError, match="reference"):
        service.reconcile(
            **(args | {"handling": [entries[0] | {"references": ["revision:foreign-content"]}]})
        )
    before = FinalReviewer(bridge.controller, bridge.task_id).report().state_hash
    result = service.reconcile(**args)
    assert service.reconcile(**args) == result
    material = bridge.status()["material"]
    assert not material["pending_material_ids"]
    assert all(row["handling_kind"] == "developer_reconciliation" for row in material["handling"])
    assert (
        bridge.conn.execute("SELECT count(*) FROM planning_session_event_results").fetchone()[0]
        == 0
    )
    assert (
        bridge.conn.execute("SELECT count(*) FROM planning_session_acknowledgements").fetchone()[0]
        == 0
    )
    assert FinalReviewer(bridge.controller, bridge.task_id).report().state_hash != before
    old_digest = material["material_digest"]
    service.followup(
        expected=service.store.base(),
        request_id="new-after-manual-handling",
        body="No archived accounts should appear",
    )
    changed = service.reconcile_preview(service.store.binding()["working_id"])
    assert changed["material"]["material_digest"] != old_digest
    with pytest.raises(ValueError, match="preview changed"):
        service.reconcile(
            **(args | {"request_id": "stale-material-preview", "expected": service.store.base()})
        )


def test_standalone_handles_only_explicit_captured_inputs(session, monkeypatch):
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import apply_result, claim_next
    from tests.planning_session_fixture import request
    from tests.test_authoring_jobs import output

    bridge, receipt, caller, cap = session
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    service.followup(
        expected=service.store.base(),
        request_id="standalone-pending-requirement",
        body="Exclude archived accounts",
    )
    bridge.detach(
        request(bridge, receipt, "explicit-standalone-mode"), caller, cap, destination="standalone"
    )
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"available": True, "host": host}
    )
    service.request_regeneration(
        expected=service.store.base(), request_id="standalone-handle-inputs", host="codex"
    )
    claim = claim_next(bridge.conn, "synthetic-provider")
    captured = __import__("json").loads(claim["input_json"])["session_material"]
    ident = captured["pending_material_ids"][0]
    result = output() | {
        "handling": [
            {
                "input_id": ident,
                "disposition": "no_change",
                "rationale": (
                    "The supplied requirement is already represented by the proposed design."
                ),
                "reference": "assistant",
            }
        ]
    }
    assert apply_result(service, claim, result)
    assert apply_result(service, claim, result)
    material = bridge.status()["material"]
    assert (
        ident not in material["pending_material_ids"] and len(material["pending_material_ids"]) == 1
    )
    assert (
        len(material["handling"]) == 1
        and material["handling"][0]["handling_kind"] == "standalone_result"
    )
    assert (
        bridge.conn.execute("SELECT count(*) FROM planning_session_event_results").fetchone()[0]
        == 0
    )


def test_ordinary_repository_journey_preserves_history_and_exact_review(controller, tmp_path):
    from harness.authoring import AuthoringService
    from harness.design_models import DesignSection
    from harness.interview_coverage import current_coverage
    from harness.requirement_registry import registry
    from harness.review_package import package
    from tests.planning_session_fixture import ordinary_owner, request

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    bridge.prepare_interview(request(bridge, receipt, "ordinary-prepare-for-review"), caller, cap)
    service = AuthoringService(controller, bridge.root, bridge.task_id)
    q = bridge.publish(
        request(bridge, receipt, "ordinary-review-question")
        | {
            "consumes": [],
            "content": {
                "question_proposal": {
                    "concern": "functional_behavior.success_path",
                    "text": "Should archived accounts be exported?",
                    "rationale": "Archive inclusion is a genuine product decision.",
                    "citations": [],
                    "supersedes": None,
                    "changed_premise_reason": None,
                }
            },
        },
        caller,
        cap,
    )["result"]["question"]
    service.answer(
        expected=service.store.base(),
        request_id="ordinary-human-answer",
        question=q,
        mode="answer",
        answer="Exclude archived accounts",
    )
    raw = controller.workspace(bridge.task_id).question(q)

    def publish_and_accept(label):
        delivery = bridge.wait(
            request(bridge, receipt, label + "-wait"), caller, cap, timeout_seconds=0
        )
        payload = attached_payload(
            bridge, receipt, label + "-sources", decisions=False, repository_only=True
        )
        payload["consumes"] = [event["id"] for event in delivery["events"]]
        bridge.publish(payload, caller, cap)
        candidate = service.snapshot()["revisions"][-1]["id"]
        preview = service.reconcile_preview(candidate)
        service.reconcile(
            expected=service.store.base(),
            request_id=label + "-adopt",
            revision=candidate,
            token=preview["token"],
            confirm_graph=True,
            acknowledge=True,
            actions=[],
        )
        from harness.investigation_repository import observation_current

        bundle = (
            __import__("harness.investigation_store", fromlist=["InvestigationStore"])
            .InvestigationStore(controller, bridge.project, bridge.task_id)
            .read_active()
        )
        drifted = [
            source["locator"]
            for source in bundle["content"]["sources"]
            if source["kind"] == "repository"
            and not observation_current(bridge.root, {"sources": [source]})
        ]
        assert not drifted, repr(drifted)
        reviewed = package(controller, bridge.task_id)
        service.accept_investigation(
            expected=service.store.base(),
            request_id=label + "-accept",
            approved_sha256=reviewed["sha256"],
            provenance="User explicitly reviewed this exact repository-only synthetic package",
        )
        assert current_coverage(controller, bridge.task_id).accepted

    publish_and_accept("ordinary-first")
    assert (
        registry(controller, bridge.task_id, service.snapshot()["working"]["body"])["ticket"][
            "present"
        ]
        is False
    )
    assert controller.workspace(bridge.task_id).question(q) == raw

    def planning(op, label):
        service.planning(expected=service.store.base(), request_id=label, operation=op)

    planning({"op": "advance"}, "ordinary-enter-design")
    for index, section in enumerate(DesignSection):
        planning(
            {
                "op": "design-none",
                "section": str(section),
                "reason": "Synthetic local export uses existing repository boundaries",
            },
            f"ordinary-design-{index}",
        )
    planning({"op": "advance"}, "ordinary-enter-test-plan")
    planning(
        {
            "op": "scenario-add",
            "category": "unit",
            "text": "Archived accounts are excluded from CSV export",
            "covers": [],
            "mock": False,
            "mock_reason": None,
        },
        "ordinary-test-scenario",
    )
    planning({"op": "test-plan-approve"}, "ordinary-approve-test-plan")
    publish_and_accept("ordinary-final")
    from harness.review import FinalReviewer

    report = FinalReviewer(controller, bridge.task_id).report()
    assert not report.blocking, report.render()


@pytest.mark.parametrize("failure", ["cancelled", "stale", "bad-reference", "receipt-failure"])
def test_standalone_material_handling_failures_commit_no_receipt(session, monkeypatch, failure):
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import apply_result, claim_next
    from tests.planning_session_fixture import request
    from tests.test_authoring_jobs import output

    bridge, receipt, caller, cap = session
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    bridge.detach(
        request(bridge, receipt, "standalone-failure-mode"), caller, cap, destination="standalone"
    )
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"available": True, "host": host}
    )
    service.request_regeneration(
        expected=service.store.base(), request_id="standalone-failure-job", host="codex"
    )
    claim = claim_next(bridge.conn, "synthetic-worker")
    import json

    captured = json.loads(claim["input_json"])["session_material"]
    entry = {
        "input_id": captured["pending_material_ids"][0],
        "disposition": "no_change",
        "rationale": (
            "The proposed result preserves the supplied requirement without additional changes."
        ),
        "reference": "assistant",
    }
    if failure == "cancelled":
        service.cancel_job(
            expected=service.store.base(), request_id="cancel-handling-job", job=claim["id"]
        )
    elif failure == "stale":
        service.followup(
            expected=service.store.base(),
            request_id="arrival-after-captured-input",
            body="A later requirement must remain pending",
        )
    elif failure == "bad-reference":
        entry["input_id"] = "not-a-captured-input"
    else:

        def fail(*args):
            raise ValueError("injected handling receipt failure")

        monkeypatch.setattr("harness.planning_session_material.record", fail)
    before = bridge.conn.execute("SELECT count(*) FROM authoring_revisions").fetchone()[0]
    result = output() | {"handling": [entry]}
    if failure in {"bad-reference", "receipt-failure"}:
        with pytest.raises(ValueError):
            apply_result(service, claim, result)
    else:
        assert apply_result(service, claim, result) is None
    assert bridge.conn.execute("SELECT count(*) FROM planning_session_handling").fetchone()[0] == 0
    assert bridge.conn.execute("SELECT count(*) FROM authoring_revisions").fetchone()[0] == before
    assert bridge.status()["material"]["pending_material_ids"]


def test_manual_handling_failure_rolls_back_adoption_and_no_change_keeps_new_identity(
    session, monkeypatch
):
    from harness.authoring import AuthoringService
    from harness.review import FinalReviewer
    from tests.planning_session_fixture import request

    bridge, receipt, caller, cap = session
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    service.save_spec(
        expected=service.store.base(),
        request_id="manual-atomic-body",
        body="## Export\nPreserve CSV exports\n",
    )
    bridge.detach(request(bridge, receipt, "manual-atomic-mode"), caller, cap, destination="manual")

    def choices(label):
        revision = service.store.binding()["working_id"]
        preview = service.reconcile_preview(revision)
        entries = [
            {
                "input_id": ident,
                "disposition": "no_change",
                "rationale": (
                    "I explicitly decide this identified requirement needs no further "
                    "content change."
                ),
                "references": ["revision:" + revision],
            }
            for ident in preview["material"]["pending_material_ids"]
        ]
        return dict(
            expected=service.store.base(),
            request_id=label,
            revision=revision,
            token=preview["token"],
            confirm_graph=True,
            acknowledge=True,
            actions=[],
            handling=entries,
        )

    args = choices("atomic-manual-reconciliation")
    original = __import__("harness.planning_session_material", fromlist=["record"]).record

    def fail(*args):
        raise ValueError("injected manual handling failure")

    monkeypatch.setattr("harness.planning_session_material.record", fail)
    before = service.store.base()
    with pytest.raises(ValueError, match="injected"):
        service.reconcile(**args)
    assert service.store.base() == before and service.store.binding()["adopted_id"] is None
    monkeypatch.setattr("harness.planning_session_material.record", original)
    service.reconcile(**args)
    old = FinalReviewer(bridge.controller, bridge.task_id).report().state_hash
    revision = bridge.status()["material"]["material_revision"]
    service.followup(
        expected=service.store.base(),
        request_id="material-even-if-no-change",
        body="Preserve existing CSV exports",
    )
    service.reconcile(**choices("explicit-new-no-change-decision"))
    assert not bridge.status()["material"]["pending_material_ids"]
    assert bridge.status()["material"]["material_revision"] == revision + 1
    assert FinalReviewer(bridge.controller, bridge.task_id).report().state_hash != old


def test_no_tests_scanner_requires_dedicated_framework_layout_coverage(controller, tmp_path):
    from harness.authoring import AuthoringService
    from harness.interview import InterviewEngine
    from harness.review_package import package
    from tests.planning_session_fixture import ordinary_owner, request

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    (bridge.root / "test_export.py").unlink()
    bridge.prepare_interview(request(bridge, receipt, "prepare-no-tests-repository"), caller, cap)
    scanner = next(
        q for q in controller.workspace(bridge.task_id).questions() if q.topic == "tests"
    )
    from harness.interview_coverage import coverage_entries

    assert "scanner.tests_setup" in {e.key for e in coverage_entries(controller, bridge.task_id)}
    payload = attached_payload(
        bridge, receipt, "no-tests-technical-interpretation", decisions=False, repository_only=True
    )
    row = next(
        r
        for r in payload["content"]["source_bundle"]["attached_envelope"]["evidence"]["coverage"]
        if r["key"] == "scanner.tests_setup"
    )
    row.update(
        disposition="interpretation",
        statement="Use pytest, placing CSV export tests under tests/test_export.py",
        rationale=(
            "A proposed framework and layout for this small Python module; explicit review required"
        ),
    )
    bridge.publish(payload, caller, cap)
    service = AuthoringService(controller, bridge.root, bridge.task_id)
    from tests.test_interview_coverage import adopt

    adopt(service)
    assert InterviewEngine(controller, bridge.task_id).checklist().blocking
    reviewed = package(controller, bridge.task_id)
    service.accept_investigation(
        expected=service.store.base(),
        request_id="accept-dedicated-testing-approach",
        approved_sha256=reviewed["sha256"],
        provenance=(
            "User explicitly reviewed the framework and layout proposal in this exact package"
        ),
    )
    assert controller.workspace(bridge.task_id).question(scanner.id) == scanner
    assert not InterviewEngine(controller, bridge.task_id).checklist().blocking
    from harness.interview_coverage import current_coverage

    (bridge.root / "export.py").write_text("export_csv = False\n")
    assert not current_coverage(controller, bridge.task_id).accepted
    assert controller.workspace(bridge.task_id).question(scanner.id) == scanner


@pytest.mark.parametrize(
    "case", ["generic-only", "not-applicable", "unknown-decision", "unrelated-question"]
)
def test_scanner_testing_coverage_does_not_waive_unresolved_choices(controller, tmp_path, case):
    from harness.authoring import AuthoringService
    from harness.review_package import package
    from tests.planning_session_fixture import ordinary_owner, request
    from tests.test_interview_coverage import adopt

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    (bridge.root / "test_export.py").unlink()
    bridge.prepare_interview(request(bridge, receipt, "prepare-scanner-guards"), caller, cap)
    service = AuthoringService(controller, bridge.root, bridge.task_id)
    if case == "unrelated-question":
        from harness.db import transaction
        from harness.planning_session_events import mutation_scope
        from harness.state_models import QuestionSource

        with transaction(bridge.conn), mutation_scope(bridge.conn, bridge.task_id):
            controller.workspace(bridge.task_id).ask(
                "tests", "Which test accounts may be deleted?", QuestionSource.INVESTIGATION
            )
    payload = attached_payload(
        bridge, receipt, "scanner-testing-guard-sources", decisions=False, repository_only=True
    )
    evidence = payload["content"]["source_bundle"]["attached_envelope"]["evidence"]
    row = next(row for row in evidence["coverage"] if row["key"] == "scanner.tests_setup")
    if case == "generic-only":
        evidence["coverage"].remove(row)
    elif case == "not-applicable":
        row["disposition"] = "not_applicable"
    elif case == "unknown-decision":
        row["disposition"] = "decision"
        evidence["decisions"] = [
            {
                "concern": row["key"],
                "text": "Should we use pytest with tests/test_export.py?",
                "citations": row["citations"],
                "supersedes": None,
                "reason": "Framework and layout require an explicit user choice",
            }
        ]
    if case == "not-applicable":
        with pytest.raises(ValueError, match="non-applicability"):
            bridge.publish(payload, caller, cap)
        return
    bridge.publish(payload, caller, cap)
    if case == "unknown-decision":
        from harness.investigation_store import InvestigationStore

        question = next(
            row["question_id"]
            for row in InvestigationStore(controller, bridge.project, bridge.task_id).decisions()
            if row["concern"] == "scanner.tests_setup"
        )
        service.planning(
            expected=service.store.base(),
            request_id="unknown-testing-setup",
            operation={"op": "question-unknown", "question_id": question},
        )
    adopt(service)
    reviewed = package(controller, bridge.task_id)
    with pytest.raises(ValueError, match="decisions|genuine|operational"):
        service.accept_investigation(
            expected=service.store.base(),
            request_id="refuse-unresolved-testing-package",
            approved_sha256=reviewed["sha256"],
            provenance=(
                "User reviewed the exact proposed source package but unresolved concerns remain"
            ),
        )


def test_no_manifest_scanner_has_dedicated_dependency_management_concern(controller, tmp_path):
    from harness.interview_coverage import coverage_entries
    from tests.planning_session_fixture import ordinary_owner, request

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    (bridge.root / "pyproject.toml").unlink()
    bridge.prepare_interview(
        request(bridge, receipt, "prepare-no-manifest-repository"), caller, cap
    )
    assert "scanner.dependency_management" in {
        entry.key for entry in coverage_entries(controller, bridge.task_id)
    }


@pytest.mark.parametrize("custom", ["inside", "outside"])
def test_managed_draft_policy_custom_roots_support_repeat_publication(session, tmp_path, custom):
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import immutable_input
    from harness.investigation_artifacts import derive, stored
    from harness.investigation_repository import Reader
    from harness.review_package import package

    bridge, receipt, caller, cap = session
    specs = bridge.root / "custom-specs" if custom == "inside" else tmp_path / "external-specs"
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id, specs=specs)
    service.save_spec(
        expected=service.store.base(),
        request_id="set-custom-spec-store",
        body="## CSV export\nRetain existing exports\n",
    )
    for index in range(2):
        result = bridge.publish(
            attached_payload(bridge, receipt, f"custom-source-cycle-{index}", decisions=False),
            caller,
            cap,
        )
        candidate = result["result"]["revision"]
        preview = service.reconcile_preview(candidate)
        service.reconcile(
            expected=service.store.base(),
            request_id=f"custom-adopt-cycle-{index}",
            revision=candidate,
            token=preview["token"],
            confirm_graph=True,
            acknowledge=True,
            actions=[],
        )
        reviewed = package(bridge.controller, bridge.task_id)
        service.accept_investigation(
            expected=service.store.base(),
            request_id=f"custom-accept-cycle-{index}",
            approved_sha256=reviewed["sha256"],
            provenance="User explicitly reviewed the exact custom-root synthetic package",
        )
    policy = derive(service)
    assert policy == stored(bridge.conn, bridge.task_id)
    assert bool(policy["excluded_paths"]) == (custom == "inside")
    observation = Reader(bridge.root, managed_policy=policy).observe("CSV exports")
    assert not set(policy["excluded_paths"]) & {
        source["locator"] for source in observation["sources"]
    }
    assert immutable_input(service)["body"] == service.snapshot()["working"]["body"]
    assert immutable_input(service)["graph"] == service.snapshot()["working"]["graph"]


def test_managed_draft_and_staging_refuse_as_sources_without_blanket_docs_filter(session):
    from harness.authoring import AuthoringService
    from harness.execution_files import read_file
    from harness.investigation_artifacts import derive
    from harness.investigation_repository import Reader, source_record
    from tests.test_interview_coverage import adopt

    bridge, receipt, caller, cap = session
    bridge.publish(
        attached_payload(bridge, receipt, "seed-generated-draft-policy", decisions=False),
        caller,
        cap,
    )
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    adopt(service)
    policy = derive(service)
    draft = service.manager().store.draft_path
    stage = draft.with_name("." + draft.name + ".tmp")
    stage.write_text("derived staging content")
    for relative in (
        "docs/specs/user-guide.md",
        "docs/specs/another-task/draft.md",
        "docs/specs/another-task/v1.md",
    ):
        path = bridge.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("## Independent documentation\nRetain as source\n")
    reader = Reader(bridge.root, managed_policy=policy)
    sources = reader.observe("CSV export")["sources"]
    paths = {source["locator"] for source in sources}
    assert (
        "docs/specs/user-guide.md" in paths
        and "docs/specs/another-task/draft.md" in paths
        and "docs/specs/another-task/v1.md" in paths
    )
    for excluded in policy["excluded_paths"]:
        with pytest.raises(ValueError, match="managed draft"):
            reader.additional([{"kind": "read", "path": excluded, "offset": 0, "limit": 8192}])
        payload = attached_payload(
            bridge, receipt, "refuse-managed-source-" + str(len(excluded)), decisions=False
        )
        value = read_file(bridge.root, excluded)
        source = source_record("repository", excluded, value.pop("text"), value)
        payload["content"]["source_bundle"]["attached_envelope"]["sources"].append(source)
        with pytest.raises(ValueError, match="managed draft"):
            bridge.publish(payload, caller, cap)
    payload = attached_payload(bridge, receipt, "refuse-agent-exclusion-list", decisions=False)
    payload["content"]["source_bundle"]["attached_envelope"]["exclusions"] = []
    with pytest.raises(ValueError, match="invalid fields"):
        bridge.publish(payload, caller, cap)


def test_managed_policy_change_during_preparation_refuses_without_receipt(
    session, tmp_path, monkeypatch
):
    import harness.planning_session_publication as publication
    from harness.authoring import AuthoringService
    from harness.investigation_artifacts import stored

    bridge, receipt, caller, cap = session
    payload = attached_payload(bridge, receipt, "policy-change-during-prepare", decisions=False)
    original = publication.prepare
    old = stored(bridge.conn, bridge.task_id)

    def prepare(*args):
        result = original(*args)
        service = AuthoringService(
            bridge.controller, bridge.root, bridge.task_id, specs=tmp_path / "new-spec-store"
        )
        service.save_spec(
            expected=service.store.base(),
            request_id="change-configured-spec-store",
            body="## Explicit newer manual draft\n",
        )
        return result

    monkeypatch.setattr(publication, "prepare", prepare)
    with pytest.raises(ValueError, match="SESSION_BASE_STALE|configuration changed"):
        bridge.publish(payload, caller, cap)
    assert stored(bridge.conn, bridge.task_id) != old
    assert bridge.conn.execute("SELECT count(*) FROM investigation_bundles").fetchone()[0] == 0
    assert (
        bridge.conn.execute("SELECT count(*) FROM planning_session_publications").fetchone()[0] == 0
    )


@pytest.mark.parametrize("case", ["generic-only", "interpretation", "unknown", "malformed"])
def test_dependency_management_review_is_exact_and_preserves_unknowns(controller, tmp_path, case):
    from harness.authoring import AuthoringService
    from harness.interview_coverage import current_coverage
    from harness.review_package import package
    from tests.planning_session_fixture import ordinary_owner, request
    from tests.test_interview_coverage import adopt

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    (bridge.root / "pyproject.toml").unlink()
    if case == "malformed":
        (bridge.root / "pyproject.toml").write_text("[project\nbroken")
    bridge.prepare_interview(request(bridge, receipt, "prepare-dependency-review"), caller, cap)
    raw = next(
        q for q in controller.workspace(bridge.task_id).questions() if q.topic == "dependencies"
    )
    service = AuthoringService(controller, bridge.root, bridge.task_id)
    service.planning(
        expected=service.store.base(),
        request_id="raw-dependency-unknown",
        operation={"op": "question-unknown", "question_id": raw.id},
    )
    raw = controller.workspace(bridge.task_id).question(raw.id)
    payload = attached_payload(
        bridge, receipt, "dependency-coverage-proposal", decisions=False, repository_only=True
    )
    evidence = payload["content"]["source_bundle"]["attached_envelope"]["evidence"]
    row = next(
        (r for r in evidence["coverage"] if r["key"] == "scanner.dependency_management"), None
    )
    if case == "malformed":
        assert row is None
    elif case == "generic-only":
        evidence["coverage"].remove(row)
    else:
        row.update(
            disposition="interpretation",
            statement=(
                "Use Python standard-library dependencies only, maintained with "
                "the module's reviewed imports"
            ),
            rationale=(
                "Concrete proposed dependency mechanism, scope and maintenance "
                "approach, subject to exact review"
            ),
        )
        if case == "unknown":
            row["disposition"] = "decision"
            evidence["decisions"] = [
                {
                    "concern": row["key"],
                    "text": (
                        "Should dependencies stay standard-library-only and be maintained "
                        "with module imports?"
                    ),
                    "citations": row["citations"],
                    "supersedes": None,
                    "reason": "A genuine dependency-management choice remains",
                }
            ]
    bridge.publish(payload, caller, cap)
    if case == "unknown":
        from harness.investigation_store import InvestigationStore

        q = next(
            row["question_id"]
            for row in InvestigationStore(controller, bridge.project, bridge.task_id).decisions()
            if row["concern"] == "scanner.dependency_management"
        )
        service.planning(
            expected=service.store.base(),
            request_id="unknown-genuine-dependency-choice",
            operation={"op": "question-unknown", "question_id": q},
        )
    adopt(service)
    reviewed = package(controller, bridge.task_id)
    args = dict(
        expected=service.store.base(),
        request_id="accept-exact-dependency-review",
        approved_sha256=reviewed["sha256"],
        provenance=(
            "User explicitly reviewed the dependency mechanism, scope and maintenance approach"
        ),
    )
    if case == "interpretation":
        service.accept_investigation(**args)
        assert current_coverage(controller, bridge.task_id).accepted
        assert controller.workspace(bridge.task_id).question(raw.id) == raw
        (bridge.root / "export.py").write_text("export_csv = False\n")
        assert not current_coverage(controller, bridge.task_id).accepted
    else:
        with pytest.raises(ValueError, match="genuine|operational|decisions"):
            service.accept_investigation(**args)
    assert controller.workspace(bridge.task_id).question(raw.id) == raw


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_managed_draft_aliases_never_become_independent_sources(session, alias):
    import os

    from harness.authoring import AuthoringService
    from harness.investigation_artifacts import derive
    from harness.investigation_repository import Reader
    from tests.test_interview_coverage import adopt

    bridge, receipt, caller, cap = session
    bridge.publish(
        attached_payload(bridge, receipt, "seed-draft-alias-test", decisions=False), caller, cap
    )
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    adopt(service)
    path = bridge.root / "alias-draft.md"
    if alias == "symlink":
        path.symlink_to(service.manager().store.draft_path)
    else:
        os.link(service.manager().store.draft_path, path)
    reader = Reader(bridge.root, managed_policy=derive(service))
    with pytest.raises((OSError, ValueError)):
        reader.read("alias-draft.md")
    observed = reader.observe("CSV export")
    assert "alias-draft.md" not in {source["locator"] for source in observed["sources"]}


def test_material_context_omissions_cannot_be_claimed_as_handled(session):
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import immutable_input
    from harness.authoring_native import validate_output
    from tests.test_authoring_jobs import output

    bridge, receipt, caller, cap = session
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    for index in range(35):
        service.followup(
            expected=service.store.base(),
            request_id=f"bounded-context-input-{index}",
            body=f"Synthetic requirement {index}",
        )
    context = immutable_input(service)
    material = context["session_material"]
    assert len(material["inputs"]) == 32 and material["omitted_input_count"] == 4
    omitted = next(
        ident
        for ident in bridge.status()["material"]["pending_material_ids"]
        if ident not in material["pending_material_ids"]
    )
    with pytest.raises(ValueError, match="uncaptured"):
        validate_output(
            output()
            | {
                "handling": [
                    {
                        "input_id": omitted,
                        "disposition": "no_change",
                        "rationale": "This omitted input was never present in the captured context",
                        "reference": "assistant",
                    }
                ]
            },
            context,
        )
    assert bridge.conn.execute("SELECT count(*) FROM planning_session_handling").fetchone()[0] == 0


def test_historical_draft_sources_stay_stale_and_retained_until_refresh(session):
    from harness.authoring import AuthoringService
    from harness.authoring_contract import canonical, digest
    from harness.db import transaction
    from harness.execution_files import read_file
    from harness.interview_coverage import current_coverage
    from harness.investigation_artifacts import derive
    from harness.investigation_repository import source_record
    from harness.investigation_store import InvestigationStore
    from harness.planning_session_events import mutation_scope
    from harness.review_package import package
    from tests.test_interview_coverage import adopt

    bridge, receipt, caller, cap = session
    result = bridge.publish(
        attached_payload(bridge, receipt, "legacy-bundle-seed", decisions=False), caller, cap
    )
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    adopt(service)
    store = InvestigationStore(bridge.controller, bridge.project, bridge.task_id)
    ident = result["result"]["bundle"]
    content = store.read_bundle(ident)["content"]
    draft = derive(service)["excluded_paths"][0]
    value = read_file(bridge.root, draft)
    source = source_record("repository", draft, value.pop("text"), value)
    # Seed a historical pre-policy record; production never rewrites retained sources.
    content["sources"].append(source)
    content["repository"]["sources"].append(source)
    content["repository"].pop("managed_policy")
    old = canonical(content)
    with transaction(bridge.conn), mutation_scope(bridge.conn, bridge.task_id):
        bridge.conn.execute(
            "UPDATE investigation_bundles SET content_json=?,content_hash=? WHERE id=?",
            (old, digest(content), ident),
        )
    assert not current_coverage(bridge.controller, bridge.task_id).accepted
    reviewed = package(bridge.controller, bridge.task_id)
    with pytest.raises(ValueError, match="operational|incomplete|stale"):
        service.accept_investigation(
            expected=service.store.base(),
            request_id="refuse-historical-draft-source",
            approved_sha256=reviewed["sha256"],
            provenance=(
                "Explicit review cannot make a historical self-observed draft independent evidence"
            ),
        )
    assert canonical(store.read_bundle(ident)["content"]) == old
    fresh = bridge.publish(
        attached_payload(bridge, receipt, "fresh-with-managed-policy", decisions=False), caller, cap
    )
    assert fresh["result"]["bundle"] != ident
    assert canonical(store.read_bundle(ident)["content"]) == old
    assert draft not in {source["locator"] for source in store.read_active()["content"]["sources"]}


def test_manual_input_handling_does_not_answer_a_genuine_unknown(controller, tmp_path):
    from harness.authoring import AuthoringService
    from harness.interview import InterviewEngine
    from tests.planning_session_fixture import ordinary_owner, request

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    bridge.prepare_interview(request(bridge, receipt, "prepare-unknown-manual-case"), caller, cap)
    published = bridge.publish(
        request(bridge, receipt, "genuine-before-manual-mode")
        | {
            "consumes": [],
            "content": {
                "question_proposal": {
                    "concern": "functional_behavior.success_path",
                    "text": "Should archived accounts be included?",
                    "rationale": "An explicit product choice remains unknown.",
                    "citations": [],
                    "supersedes": None,
                    "changed_premise_reason": None,
                }
            },
        },
        caller,
        cap,
    )
    question = published["result"]["question"]
    service = AuthoringService(controller, bridge.root, bridge.task_id)
    service.planning(
        expected=service.store.base(),
        request_id="human-genuine-unknown",
        operation={"op": "question-unknown", "question_id": question},
    )
    before = controller.workspace(bridge.task_id).question(question)
    bridge.detach(
        request(bridge, receipt, "manual-mode-with-unknown"), caller, cap, destination="manual"
    )
    revision = service.store.binding()["working_id"]
    preview = service.reconcile_preview(revision)
    handling = [
        {
            "input_id": ident,
            "disposition": "no_change",
            "rationale": (
                "I acknowledge this input is represented in the selected content; "
                "the genuine question remains unresolved."
            ),
            "references": ["revision:" + revision],
        }
        for ident in preview["material"]["pending_material_ids"]
    ]
    service.reconcile(
        expected=service.store.base(),
        request_id="manual-inputs-still-genuine-unknown",
        revision=revision,
        token=preview["token"],
        confirm_graph=True,
        acknowledge=True,
        actions=[],
        handling=handling,
    )
    assert not bridge.status()["material"]["pending_material_ids"]
    assert controller.workspace(bridge.task_id).question(question) == before
    assert before.status == "open" and before.answer is None
    assert InterviewEngine(controller, bridge.task_id).checklist().blocking
