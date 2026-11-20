import pytest

from harness.approval import ApprovalGate
from harness.authoring import AuthoringService
from harness.authoring_store import approval_token
from harness.phases import Phase
from harness.review import FinalReviewer
from harness.spec_manager import SpecificationManager
from harness.spec_store import SpecStore
from tests.complete_task import settle


@pytest.fixture(autouse=True)
def available_fixture_host(monkeypatch):
    monkeypatch.setattr(
        "harness.authoring_policy.availability",
        lambda host: {"host": host, "available": True, "status": "fixture"},
    )


def test_exact_adoption_sealing_and_pending_edits_preserve_approval(controller, sample_repo):
    task = controller.create_task("fixture request", "trivial")
    settle(controller, task.id)
    service = AuthoringService(
        controller, sample_repo, task.id, specs=str(sample_repo / "docs/specs")
    )
    service.start(expected=service.store.base(), request_id="resume-1234", host="codex")
    body = "## User-owned specification\n\nExact user words and spacing.\n"
    service.save_spec(expected=service.store.base(), request_id="save-12345", body=body)
    revision = service.store.base()["working"]
    preview = service.reconcile_preview(revision)
    service.reconcile(
        expected=service.store.base(),
        request_id="adopt-12345",
        revision=revision,
        token=preview["token"],
        confirm_graph=True,
        acknowledge=True,
        actions=[],
    )
    assert controller.get_task(task.id).phase is Phase.FINAL_REVIEW
    controller.advance(task.id)
    store = SpecStore(sample_repo / "docs/specs" / task.id)
    manager = SpecificationManager(controller, task.id, store, sample_repo)
    assert manager.body() == body
    gate = ApprovalGate(controller, task.id, store, sample_repo)
    approval, record, _ = gate.approve()
    assert record.body == body
    assert gate.require() == approval
    state_hash = FinalReviewer(controller, task.id).report().state_hash
    token = approval_token(controller, task.id, state_hash)
    service.save_spec(
        expected=service.store.base(), request_id="save-67890", body="invalid but retained"
    )
    assert manager.body() == body
    assert gate.require() == approval
    assert FinalReviewer(controller, task.id).report().state_hash == state_hash
    assert approval_token(controller, task.id, state_hash) != token
    assert service.snapshot()["working"]["body"] == "invalid but retained"
    with pytest.raises(ValueError):
        p = service.reconcile_preview(service.store.base()["working"])
        service.reconcile(
            expected=service.store.base(),
            request_id="adopt-invalid",
            revision=p["revision"],
            token=p["token"],
            confirm_graph=True,
            acknowledge=True,
            actions=[],
        )


def test_approval_preview_is_fenced_by_pending_save(controller, sample_repo):
    from harness.dashboard.approve import StaleApproval, _guard
    from harness.diagrams import DiagramService
    from harness.spec_record import content_hash

    task = controller.create_task("fixture request", "trivial")
    settle(controller, task.id)
    controller.advance(task.id)
    service = AuthoringService(controller, sample_repo, task.id)
    initial = service.start(expected=service.store.base(), request_id="resume-1234", host="codex")
    service.cancel_job(
        expected=service.store.base(), request_id="cancel-initial", job=initial["job"]
    )
    store = SpecStore(sample_repo / "docs/specs" / task.id)
    gate = ApprovalGate(controller, task.id, store, sample_repo)
    diagram = DiagramService(controller, task.id).sync()
    report = FinalReviewer(controller, task.id).report()
    seen = {
        "version": diagram.version if diagram else None,
        "review_hash": approval_token(controller, task.id, report.state_hash),
        "spec_hash": content_hash(gate.manager.body()),
    }
    service.save_spec(
        expected=service.store.base(), request_id="save-before-approve", body="## Pending edit\n"
    )
    with pytest.raises(StaleApproval):
        gate.approve(guard=_guard(controller, gate, seen))
    assert not store.version_files()


def test_active_execution_refuses_adoption_but_preserves_editable_revision(
    controller, tmp_path, monkeypatch
):
    from harness.authoring_contract import AuthoringConflict
    from tests.test_authoring import initialized

    service = initialized(controller, tmp_path)
    service.save_spec(
        expected=service.store.base(),
        request_id="save-active-1234",
        body="## Changed while running\n",
    )
    monkeypatch.setattr(
        "harness.execution_store.latest_execution",
        lambda *args: {
            "id": "execution-fixture",
            "status": "Running",
            "approval_id": 1,
            "epoch_id": "epoch-1",
            "authority_generation": 1,
        },
    )
    revision = service.store.base()["working"]
    preview = service.reconcile_preview(revision)
    with pytest.raises(AuthoringConflict, match="active or unsettled"):
        service.reconcile(
            expected=service.store.base(),
            request_id="adopt-active-1234",
            revision=revision,
            token=preview["token"],
            confirm_graph=True,
            acknowledge=True,
            actions=[],
        )
    assert service.snapshot()["working"]["id"] == revision
    assert service.snapshot()["adopted"] is None


def test_terminal_status_alone_is_not_execution_settlement(controller, tmp_path, monkeypatch):
    from harness.authoring_contract import AuthoringConflict
    from tests.test_authoring import initialized

    service = initialized(controller, tmp_path)
    monkeypatch.setattr(
        "harness.execution_store.latest_execution",
        lambda *args: {"id": "fixture-terminal", "status": "Completed", "epoch_id": "legacy-0"},
    )
    with pytest.raises(AuthoringConflict, match="unverified attempt settlement"):
        service._refuse_execution()
