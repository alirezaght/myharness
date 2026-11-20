"""Typed public exchanges preserve authority, immutable references and claim ordering."""

import copy
import json
from pathlib import Path

import pytest

from harness import agent_contracts as C
from harness.agent_exchange import context, specification
from harness.execution_store import get_execution
from tests.test_execution_operations import generated_handoff_fixture, operations  # noqa: F401


def test_read_only_published_store_never_creates_missing_coordination(tmp_path):
    from harness.execution_artifacts import ArtifactStore

    directory = tmp_path / "missing" / "drafts"
    with pytest.raises(ValueError, match="published artifact store unavailable"):
        ArtifactStore(directory, create=False)
    assert not directory.parent.exists()
    directory.mkdir(parents=True)
    store = ArtifactStore(directory, create=False)
    with pytest.raises(FileNotFoundError):
        with store.locked(read_only=True):
            pytest.fail("missing lock must not be replaced")
    assert not list(directory.iterdir())


def test_preexecution_actual_interview_and_design_consumers(controller):
    from harness.agent_preexecution import consume
    from harness.classification import Complexity
    from harness.design import ArchitectureDesigner
    from harness.design_models import DesignSection
    from harness.interview import InterviewEngine
    from harness.phases import Phase

    task = controller.create_task("Build a reliable report", complexity=Complexity.SIMPLE)
    engine = InterviewEngine(controller, task.id)
    from harness.investigation import InvestigationReport

    controller.record_investigation(
        task.id,
        InvestigationReport(
            repo_root=str(Path.cwd()),
            revision="fixture",
            request=task.request,
            decisions=(),
            facts=(),
            assumptions=(),
            questions=(),
        ),
    )
    # Do not invent an execution identity.
    controller.conn.execute("UPDATE tasks SET phase=? WHERE id=?", (str(Phase.INTERVIEW), task.id))
    engine.next()
    checkpoint = engine.ws.latest_checkpoint("interview")
    contract = checkpoint.payload["contract"]
    assert contract["kind"] == "requirement-analysis"
    assert contract["context"]["execution"] is None
    assert contract["context"]["applicability"]["execution"] == "pre-execution"
    engine.summary()
    forged = copy.deepcopy(contract)
    forged["status"] = "approved"
    with pytest.raises(C.ContractError):
        consume(controller, task.id, "requirement-analysis", {}, forged)
    controller.conn.execute("UPDATE tasks SET phase=? WHERE id=?", (str(Phase.DESIGN), task.id))
    designer = ArchitectureDesigner(controller, task.id)
    designer.propose(next(iter(DesignSection)), "Small module", "Keep existing storage")
    assert (
        designer.ws.latest_checkpoint("design").payload["contract"]["kind"]
        == "architecture-proposal"
    )
    designer.document()


def output_contract(operation, child, execution, state, assignment, content):
    declared = assignment["contract"]
    return C.make(
        "implementation-result",
        context(execution, state, child, "handoff-task"),
        specification(state),
        "implementation-result",
        content,
        status="authored",
        inputs=declared["required_inputs"],
        criteria=declared["acceptance_criteria"],
        constraints=declared["constraints"],
    )


def test_public_assignment_pages_typed_result_and_immutable_consumer(operations):  # noqa: F811
    operation, _, controller, initialization, _, execution, _ = operations
    child, expected = generated_handoff_fixture(operations)
    assigned = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]
    assert assigned["contract"]["kind"] == "task-assignment"
    assert assigned["contract"]["expected_outputs"] == [
        {"name": "implementation-result", "kind": "implementation-result"}
    ]
    pages, offset = [], 0
    while True:
        page = operation.dispatch(
            child, "read_file", {"path": assigned["resource"], "offset": offset, "limit": 4096}
        )
        pages.append(page["text"])
        offset = page["next_offset"]
        if not page["truncated"]:
            break
    assert "".join(pages).encode() == expected
    state = json.loads(Path(initialization["source"]).read_bytes())
    execution = get_execution(controller.conn, execution["id"])
    content = "Implemented the exact scoped contribution; verification remains pending."
    contract = output_contract(operation, child, execution, state, assigned, content)
    result = operation.dispatch(
        child,
        "submit_output",
        {
            "name": "result.md",
            "content": content,
            "expected_graph_revision": 1,
            "contract": contract,
        },
    )
    assert operation.artifacts.outputs()[child["thread_id"]]["result.md"]["contract"] == contract
    assert operation.artifacts.read(result["artifact_id"], result["sha256"])["text"] == content
    assert state["integration"]["tasks"]["handoff-task"]["status"] == "Running"


@pytest.mark.parametrize(
    "defect",
    [
        "kind",
        "version",
        "unknown-key",
        "producer",
        "spec",
        "graph",
        "input",
        "payload",
        "criteria",
        "task-revision",
    ],
)
def test_rejection_is_sanitized_and_does_not_publish(operations, defect):  # noqa: F811
    operation, _, controller, initialization, _, execution, _ = operations
    child, _ = generated_handoff_fixture(operations, body="bounded scoped work\n")
    assigned = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]
    state = json.loads(Path(initialization["source"]).read_bytes())
    content = "A scoped authored result."
    value = output_contract(
        operation, child, get_execution(controller.conn, execution["id"]), state, assigned, content
    )
    secret = "PRIVATE_CONTRACT_SENTINEL"
    if defect == "kind":
        value["kind"] = secret
    if defect == "version":
        value["schema_version"] = secret
    if defect == "unknown-key":
        value[secret] = secret
    if defect == "producer":
        value["context"]["producer"] = secret
    if defect == "spec":
        value["source_specification"]["reference"]["sha256"] = "a" * 64
    if defect == "graph":
        value["context"]["graph_revision"] += 1
    if defect == "task-revision":
        value["context"]["task_revision"] += 1
    if defect == "input":
        value["required_inputs"][0]["reference"]["id"] = secret
    if defect == "payload":
        value["payload"]["record_digest"] = "b" * 64
    if defect == "criteria":
        value["acceptance_criteria"] = [secret]
    before = operation.artifacts.outputs()
    with pytest.raises(ValueError) as caught:
        operation.dispatch(
            child,
            "submit_output",
            {
                "name": "rejected.md",
                "content": content,
                "expected_graph_revision": 1,
                "contract": value,
            },
        )
    assert secret not in str(caught.value)
    assert operation.artifacts.outputs() == before


def test_claims_are_reserved_before_contract_bytes_and_metadata_reads_none(operations, monkeypatch):  # noqa: F811
    from harness import agent_exchange
    from harness.policy.context import CURRENT

    operation, _, controller, initialization, _, execution, _ = operations
    child, _ = generated_handoff_fixture(operations, body="bounded scoped work\n")
    original = agent_exchange.resolver
    observed = []

    def resolver(*args):
        actual = original(*args)

        def read(reference):
            attempt = CURRENT.get()
            assert attempt is not None
            rows = controller.conn.execute(
                "SELECT state FROM policy_resource_claims WHERE attempt_id=?", (attempt.intent.id,)
            ).fetchall()
            assert rows and all(r["state"] in {"reserved", "active"} for r in rows)
            observed.append(reference)
            return actual(reference)

        return read

    monkeypatch.setattr(agent_exchange, "resolver", resolver)
    assigned = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]
    assert assigned["status"] == "available" and observed
    observed.clear()
    operation.admitted(
        controller,
        child,
        "handoff-task",
        operation.assignments[child["thread_id"]]["revision"],
        verify_outputs=False,
    )
    assert not observed


def test_actual_decision_registry_adapter_keeps_version_and_verdict_authority(controller, tmp_path):
    from harness.decision_workflow import DecisionWorkflow
    from harness.registry import DecisionRegistry

    task = controller.create_task("Use an existing storage boundary")
    workflow = DecisionWorkflow(controller, task.id, DecisionRegistry(tmp_path / "decisions"))
    record = workflow.record("Storage", "Keep SQLite", "Retain transactional semantics")
    value = workflow.contract(record.id)
    assert value["kind"] == "design-decision" and value["status"] == "recorded"
    # The retained companion binds actual decision content, not merely its approved label.
    original = record.path.read_text()
    record.path.write_text(original.replace("Keep SQLite", "Use a changed storage backend"))
    before = list(workflow.ws.decision_changes())
    with pytest.raises(C.ContractError):
        workflow.change(record.id, "New change", "Keep original safeguards", "Bounded impact")
    assert workflow.ws.decision_changes() == before
    record.path.write_text(original)
    # A schema-valid observed decision is not a substitute for an approved registry record.
    text = record.path.read_text().replace("**Status:** approved", "**Status:** superseded")
    record.path.write_text(text)
    with pytest.raises(ValueError):
        workflow.contract(record.id)


def test_typed_result_required_consumer_preserves_authored_unverified_and_detects_tampering(
    operations,  # noqa: F811
):
    from harness.agent_results import require_typed_result

    operation, _, controller, initialization, _, execution, _ = operations
    child, _ = generated_handoff_fixture(operations, body="bounded source work\n")
    assigned = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]
    state = json.loads(Path(initialization["source"]).read_bytes())
    execution = get_execution(controller.conn, execution["id"])
    legacy = operation.dispatch(
        child,
        "submit_output",
        {"name": "notes.md", "content": "Supporting free-form notes", "expected_graph_revision": 1},
    )
    assert operation.artifacts.read(legacy["artifact_id"], legacy["sha256"])["text"]
    with pytest.raises(C.ContractError, match="expected_outputs"):
        require_typed_result(execution, state, "handoff-task")
    content = "Authored contribution remains unverified."
    value = output_contract(operation, child, execution, state, assigned, content)
    operation.dispatch(
        child,
        "submit_output",
        {"name": "result.md", "content": content, "expected_graph_revision": 1, "contract": value},
    )
    require_typed_result(execution, state, "handoff-task")
    assert state["integration"]["tasks"]["handoff-task"]["status"] == "Running"
    reference = state["writers"]["handoff-writer"]["handoffs"][-1]["artifact"]
    Path(reference["stored_path"]).write_text("changed immutable assignment")
    with pytest.raises(C.ContractError):
        require_typed_result(execution, state, "handoff-task")


def test_immutable_presentation_retains_unknown_legacy_presence_and_does_not_read_source(
    operations,  # noqa: F811
    monkeypatch,
):
    import harness.execution_files as files

    operation, _, controller, _, _, execution, _ = operations
    child, _ = generated_handoff_fixture(operations, body="immutable presentation\n")
    before = [
        tuple(row)
        for row in controller.conn.execute(
            "SELECT * FROM policy_legacy_admissions ORDER BY attempt_id"
        )
    ]
    assert before  # The real general AL producer is not misrepresented as whole-tree closed.

    def forbidden(*args, **kwargs):
        pytest.fail("presentation must not validate mutable dependency bytes")

    monkeypatch.setattr(files, "read_file", forbidden)
    page = operation.dispatch(child, "read_file", {"path": "harness:assignment-handoff"})
    assert page["coverage"] == {
        "immutable_snapshot_validated": True,
        "mutable_source_not_checked": True,
        "coordinated_source_consumption": False,
        "legacy_uncoordinated": None,
    }
    after = [
        tuple(row)
        for row in controller.conn.execute(
            "SELECT * FROM policy_legacy_admissions ORDER BY attempt_id"
        )
    ]
    assert after == before
    assert get_execution(controller.conn, execution["id"])["status"] != "Completed"


def test_indexed_artifact_buffer_replacement_is_rejected_and_exact_buffer_is_consumed(
    tmp_path, monkeypatch
):
    import os

    from harness.execution_artifacts import ArtifactStore

    store = ArtifactStore(tmp_path / "artifacts")
    record = store.capture_generated("immutable.md", b"exact authenticated snapshot")
    actual = store.buffer(record["artifact_id"], record["sha256"])
    assert actual == b"exact authenticated snapshot"
    # Public reads consume the checked buffer, not a subsequently reopened pathname.
    monkeypatch.setattr(
        store, "resolve", lambda *args: pytest.fail("checked paths must not be reopened")
    )
    assert store.read(record["artifact_id"], record["sha256"])["text"] == actual.decode()
    real_read = os.read
    changed = False

    def replacing(fd, size):
        nonlocal changed
        raw = real_read(fd, size)
        if raw and not changed:
            changed = True
            with store.locked(read_only=True) as index:
                path = Path(index["records"][record["artifact_id"]]["path"])
            path.write_bytes(b"replacement during verified read")
        return raw

    monkeypatch.setattr(os, "read", replacing)
    with pytest.raises(ValueError, match="bytes changed"):
        store.buffer(record["artifact_id"], record["sha256"])
