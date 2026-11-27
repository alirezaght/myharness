"""Real authenticated dispatch and exact return-boundary regressions; no provider-use claim."""

import json
import subprocess
from importlib import import_module
from pathlib import Path

import pytest

from harness.knowledge.models import canonical, digest
from harness.knowledge.policy import current_operator_scope
from harness.knowledge.refresh import refresh
from tests.test_execution_operations import generated_handoff_fixture, observed_principal
from tests.test_execution_operations import operations as operations
from tests.test_knowledge_workflow_context import seeded


def seed_task(operations):
    operation = operations[0]
    root = Path(operation.envelope["repository"])
    (root / "source").mkdir(exist_ok=True)
    (root / "source/bounded.py").write_text("def bounded_fixture():\n    return 3\n")
    subprocess.run(["git", "-C", str(root), "add", "source/bounded.py"], check=True)
    return seeded(operations)


def register(operations, role="al-architect"):
    operation, _, _, initialization, _, _, _ = operations
    principal = observed_principal(operation, "context-" + role, role)
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["agents"][principal["thread_id"]] = {
        "id": principal["thread_id"],
        "name": "Context reader",
        "role": role,
        "task": "handoff-task",
        "status": "Running",
        "registration": "fixture",
    }
    path.write_text(json.dumps(state))
    return principal


def delivered(operation, principal):
    value = operation.dispatch(principal, "await_assignment", {"timeout": 1})["knowledge_context"]
    assert value["status"] == "available", value
    raw = canonical(value["context"]).encode()
    assert len(raw) <= 32768
    assert len(canonical(value).encode()) <= 32768
    assert value["presentation"]["sha256"] == digest(raw)
    assert value["presentation"]["size"] == len(raw)
    assert value["presentation"]["observation"] == "server-return-prepared"
    assert value["presentation"]["model_use"] == "unobserved"
    return value


def test_assignment_delivers_stage_specific_cited_bytes_without_rewriting_handoff(operations):
    operation, supervisor, controller, initialization, _, execution, _ = operations
    state_path = Path(initialization["source"])
    state = json.loads(state_path.read_bytes())
    state["phase"] = "IMPLEMENTATION"
    state_path.write_text(json.dumps(state))
    child, handoff = generated_handoff_fixture(operations)
    task_package = Path(execution["handoff_path"]).parent / "task-package.json"
    original_package = task_package.read_bytes()
    seed_task(operations)
    architect = register(operations)
    planning = delivered(operation, architect)
    engineer = delivered(operation, child)
    control = delivered(operation, supervisor)
    assert planning["context"]["purpose"] == "planning"
    assert engineer["context"]["purpose"] == control["context"]["purpose"] == "implementation"
    assert planning["bundle_hash"] != engineer["bundle_hash"]
    request = engineer["context"]["context"]["request"]
    assert request["objective"] == "bounded fixture"
    assert request["stage"] == "implementation"
    assert request["affected_entities"] == ["file:source/bounded.py"]
    assert planning["context"]["context"]["request"]["stage"] == "planning"
    assert engineer["context"]["assertions"] and engineer["context"]["citations"]
    checked = 0
    for citation in engineer["context"]["citations"]:
        row = controller.conn.execute(
            "SELECT s.locator FROM knowledge_source_versions v JOIN knowledge_sources s "
            "USING(source_id) WHERE v.version_id=?",
            (citation["version_id"],),
        ).fetchone()
        if row["locator"] == "source/bounded.py":
            raw = (Path(operation.envelope["repository"]) / row["locator"]).read_bytes()
            assert digest(raw) == citation["content_sha256"]
            assert (
                digest(raw[citation["start_byte"] : citation["end_byte"]])
                == citation["span_sha256"]
            )
            checked += 1
    assert checked > 0
    stored = controller.conn.execute(
        "SELECT payload FROM knowledge_bundles WHERE bundle_hash=?", (engineer["bundle_hash"],)
    ).fetchone()[0]
    assert digest(stored.encode()) == engineer["bundle_hash"]
    assert json.loads(stored)["context"]["request"]["estimated_tokens"] == 8192
    estimate = engineer["presentation"]["size_estimate"]
    assert type(estimate["value"]) is int and estimate["value"] > 0
    assert estimate["value"] == json.loads(stored)["budget"]["estimated_tokens"]
    assert estimate["unit"] == "estimated tokens"
    assert "not-tokenizer-exact" in estimate["method"]
    assert "not an upper bound" in estimate["uncertainty"]
    assert engineer["context"]["context"]["request"]["estimated_tokens"] == "[redacted]"
    response = operation.dispatch(child, "await_assignment", {"timeout": 1})
    descriptor = response["handoff"]
    offset, chunks = 0, []
    while True:
        page = operation.dispatch(
            child, "read_file", {"path": descriptor["resource"], "offset": offset, "limit": 65536}
        )
        raw = page["text"].encode()
        assert page["resource"] == descriptor["resource"]
        assert page["sha256"] == descriptor["sha256"]
        assert page["presentation_sha256"] == descriptor["presentation_sha256"]
        assert page["offset_unit"] == "presentation-utf8-byte"
        assert page["offset"] == offset and page["next_offset"] == offset + len(raw)
        assert not page["presentation_redacted"]
        chunks.append(raw)
        offset = page["next_offset"]
        if not page["truncated"]:
            break
        assert raw and offset < descriptor["presentation_size"]
    complete = b"".join(chunks)
    assert offset == descriptor["presentation_size"]
    assert complete == handoff
    assert digest(complete) == descriptor["sha256"]
    assert task_package.read_bytes() == original_package


@pytest.mark.parametrize("mode", ["off", "unindexed", "busy"])
def test_optional_context_failure_returns_actionable_bounded_fallback(
    operations, monkeypatch, mode
):
    operation, supervisor, controller, _, _, _, _ = operations
    if mode != "unindexed":
        seed_task(operations)
    if mode == "off":
        monkeypatch.setenv("HARNESS_KNOWLEDGE", "off")
    if mode == "busy":
        import sqlite3

        def busy(*args, **kwargs):
            raise sqlite3.OperationalError("private diagnostic")

        monkeypatch.setattr(import_module("harness.knowledge.query"), "query", busy)
    before = controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    value = operation.dispatch(supervisor, "await_assignment", {"timeout": 1})
    assert value["status"] == "Supervisor" and not value["writable"]
    context = value["knowledge_context"]
    assert context["status"] == "unavailable" and "read_file" in context["fallback"]
    assert len(canonical(context).encode()) < 1024
    assert "private diagnostic" not in canonical(context)
    assert (
        controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == before
    )


def test_assignment_interleaving_links_exact_receipt_and_reconnect_queries_again(
    operations, monkeypatch
):
    operation, _, controller, _, _, _, _ = operations
    generated_handoff_fixture(operations)
    seed_task(operations)
    reader = register(operations)
    module = import_module("harness.knowledge.query")
    actual = module.query
    issued = []

    def interleave(*args, **kwargs):
        first, second = actual(*args, **kwargs), actual(*args, **kwargs)
        assert first.bundle_hash == second.bundle_hash and first.receipt_id != second.receipt_id
        issued.append((first.receipt_id, second.receipt_id))
        return first

    monkeypatch.setattr(module, "query", interleave)
    first, second = delivered(operation, reader), delivered(operation, reader)
    assert first["receipt_id"] != second["receipt_id"]
    links = controller.conn.execute(
        "SELECT receipt_id FROM reliability_timeline_links WHERE reference_id "
        "LIKE 'await_assignment-return-prepared:%'"
    ).fetchall()
    assert {r[0] for r in links} == {pair[0] for pair in issued}
    assert first["bundle_hash"] == second["bundle_hash"]


@pytest.mark.parametrize("change", ["registration", "source", "snapshot", "policy"])
def test_mutation_after_query_withholds_assignment_and_return_claim(
    operations, monkeypatch, change
):
    operation, _, controller, initialization, _, _, _ = operations
    generated_handoff_fixture(operations)
    scope = seed_task(operations)
    reader = register(operations)
    module = import_module("harness.knowledge.integration")
    actual = module.assignment_context

    def mutate(*args):
        result = actual(*args)
        assert result["status"] == "available"
        if change == "registration":
            path = Path(initialization["source"])
            state = json.loads(path.read_bytes())
            state["integration"]["tasks"]["handoff-task"]["files"] = ["other"]
            path.write_text(json.dumps(state))
        elif change == "policy":
            controller.conn.execute(
                "UPDATE knowledge_projects SET policy_generation=policy_generation+1"
            )
        else:
            (scope.repository / "source/bounded.py").write_text(
                "def bounded_fixture():\n    return 987\n"
            )
            if change == "snapshot":
                refresh(controller.conn, current_operator_scope(controller.conn, scope.repository))
        return result

    monkeypatch.setattr(module, "assignment_context", mutate)
    with pytest.raises((ValueError, PermissionError)):
        operation.dispatch(reader, "await_assignment", {"timeout": 1})
    assert not controller.conn.execute(
        "SELECT 1 FROM reliability_timeline_links WHERE reference_id "
        "LIKE 'await_assignment-return-prepared:%'"
    ).fetchone()


def test_stale_or_sibling_delivery_receipt_cannot_be_published(operations):
    from harness.knowledge.integration import assignment_context
    from harness.reliability.timeline_store import assignment_link

    operation, _, _, _, _, _, _ = operations
    child, _ = generated_handoff_fixture(operations)
    seed_task(operations)
    reader = register(operations)
    value = assignment_context(operation, None, child)
    assert value["status"] == "available"
    with pytest.raises(ValueError, match="stale or foreign"):
        assignment_link(operation, reader, value)
    corrupted = json.loads(json.dumps(value))
    corrupted["context"]["receipt_id"] = "0" * 32
    with pytest.raises(ValueError, match="returned bytes differ"):
        assignment_link(operation, child, corrupted)


def test_review_and_foreign_identity_are_hard_refusals(operations):
    operation, _, controller, initialization, _, _, _ = operations
    generated_handoff_fixture(operations)
    seed_task(operations)
    reviewer = register(operations)
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["phase"] = "REVIEW"
    state["review"] = {"stage": "fresh"}
    path.write_text(json.dumps(state))
    before = controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    with pytest.raises(
        ValueError, match="knowledge:(review-packet-not-issued|clean-review-provenance-unavailable)"
    ):
        operation.dispatch(reviewer, "await_assignment", {"timeout": 1})
    with pytest.raises(ValueError):
        operation.dispatch(reviewer | {"thread_id": "foreign"}, "await_assignment", {"timeout": 1})
    assert (
        controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == before
    )


def test_recovery_occurrence_refreshes_and_old_epoch_is_refused(operations):
    operation, _, controller, initialization, _, _, _ = operations
    generated_handoff_fixture(operations)
    seed_task(operations)
    reader = register(operations)
    before = delivered(operation, reader)
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["phase"] = "REWORK"
    path.write_text(json.dumps(state))
    after = delivered(operation, reader)
    assert after["receipt_id"] != before["receipt_id"]
    assert after["context"]["context"]["request"]["stage"] == "recovery"
    assert after["bundle_hash"] != before["bundle_hash"]
    count = controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    operation.epoch_id = "stale-epoch"
    with pytest.raises(ValueError):
        operation.dispatch(reader, "await_assignment", {"timeout": 1})
    assert controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == count


def test_redaction_of_cited_claim_withholds_context_instead_of_relabelling_citations(operations):
    operation, _, _, _, _, _, _ = operations
    child, _ = generated_handoff_fixture(operations)
    scope = seed_task(operations)
    source = scope.repository / "source/bounded.py"
    source.write_text(
        "def bounded_fixture():\n    password=CANARY_NOT_RETURNED\n    return password\n"
    )
    refresh(operations[2].conn, scope)
    value = operation.dispatch(child, "await_assignment", {"timeout": 1})["knowledge_context"]
    assert value["status"] == "unavailable"
    assert value["reason"] == "knowledge:source-presentation-redacted"
    assert "CANARY_NOT_RETURNED" not in canonical(value)


def test_changed_interface_refreshes_and_unapproved_design_change_is_refused(operations):
    from harness.design_models import DesignSection
    from harness.knowledge.integration import assignment_context
    from harness.reliability.timeline_store import assignment_link

    operation, _, controller, _, _, execution, _ = operations
    generated_handoff_fixture(operations)
    scope = seed_task(operations)
    reader = register(operations)
    ws = controller.workspace(execution["task_id"])
    first = delivered(operation, reader)
    source = scope.repository / "source/bounded.py"
    source.write_text("def bounded_fixture():\n    return 4\n")
    refresh(controller.conn, current_operator_scope(controller.conn, scope.repository))
    second = delivered(operation, reader)
    assert second["bundle_hash"] != first["bundle_hash"]
    assert second["snapshot_id"] != first["snapshot_id"]
    assert (
        second["context"]["context"]["binding_identity"]
        != first["context"]["context"]["binding_identity"]
    )
    assert any("return 4" in a["text"] for a in second["context"]["assertions"])
    with pytest.raises(ValueError, match="stale or foreign"):
        assignment_link(operation, reader, first)
    prepared = assignment_context(operation, None, reader)
    assert prepared["receipt_id"] not in {first["receipt_id"], second["receipt_id"]}

    from harness.approval import NotApproved

    count = controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    ws.state_design(DesignSection.INTERFACES, "bounded fixture returns four")
    with pytest.raises(NotApproved, match="specification content changed since approval"):
        operation.dispatch(reader, "await_assignment", {"timeout": 1})
    assert controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == count


def test_writer_change_between_descriptor_validation_and_publication_is_refused(
    operations, monkeypatch
):
    from contextlib import contextmanager

    from harness.knowledge.integration import assignment_context
    from harness.reliability import store
    from harness.reliability.timeline_store import assignment_link

    operation, _, controller, initialization, _, _, _ = operations
    child, _ = generated_handoff_fixture(operations)
    seed_task(operations)
    prepared = assignment_context(operation, None, child)
    assert prepared["status"] == "available"
    actual = store._publication

    @contextmanager
    def changed(operation):
        path = Path(initialization["source"])
        state = json.loads(path.read_bytes())
        state["writers"]["handoff-writer"]["handoffs"][-1]["artifact"]["sha256"] = "0" * 64
        path.write_text(json.dumps(state))
        with actual(operation) as publication:
            yield publication

    monkeypatch.setattr(store, "_publication", changed)
    with pytest.raises(ValueError, match="assignment changed before publication"):
        assignment_link(operation, child, prepared)
    assert not controller.conn.execute(
        "SELECT 1 FROM reliability_timeline_links WHERE reference_id "
        "LIKE 'await_assignment-return-prepared:%'"
    ).fetchone()
