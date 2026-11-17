"""Information-flow contracts; synthetic fixtures do not establish native business acceptance."""

import json
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.controller import HarnessController
from harness.knowledge.models import ContextRequest, RefreshRequest, digest
from harness.knowledge.policy import current_operator_scope, derive_scope, operator_scope
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from harness.knowledge.review_projection import resolve
from tests.test_execution_operations import operations as operations
from tests.test_knowledge_snapshots import graph as graph


def packet_fixture(tmp_path, conn, scope, *, stage="fresh"):
    root = tmp_path / "session"
    artifacts = root / "review-session" / "artifacts"
    artifacts.mkdir(parents=True)

    def artifact(value, kind):
        raw = json.dumps(value, sort_keys=True).encode()
        sha = digest(raw)
        path = artifacts / sha
        path.write_bytes(raw)
        return {
            "id": sha,
            "sha256": sha,
            "kind": kind,
            "stored_path": str(path),
            "source": "fixture",
            "captured_at": time.time() - 10,
        }

    candidate = {
        "fingerprint": "candidate",
        "files": [
            {
                "path": "service.py",
                "worktree": {
                    "type": "file",
                    "sha256": digest((scope.repository / "service.py").read_bytes()),
                },
            }
        ],
    }
    task_package = {"original_request": "safe task"}
    attempt = {"stage": stage, "role": "al-architect", "attempt_id": "attempt"}
    value = {
        "schema_version": 1,
        "stage": stage,
        "review_id": "R1",
        "candidate_hash": "candidate",
        "task_hash": "task",
        "candidate": candidate,
        "task_package": task_package,
        "attempt": attempt,
    }
    state = {
        "phase": "REVIEW",
        "run_id": "run",
        "task_package": artifact(task_package, "task-package"),
        "integration": {"agents": {}},
        "review": {
            "id": "R1",
            "stage": "fresh" if stage == "fresh" else "comparison",
            "candidate": candidate,
            "task_hash": "task",
            "packets": {stage: artifact(value, "review-packet-" + stage)},
            "attempts": {stage: artifact(attempt, "review-attempt")},
        },
    }
    execution = {
        "id": "execution",
        "al_session_id": "review-session",
        "task_id": "task",
        "epoch_id": "legacy-0",
        "authority_generation": 0,
        "handoff_path": str(tmp_path / "handoff.json"),
    }
    operation = SimpleNamespace(envelope={})
    return operation, execution, state, root


def test_packet_projection_starts_only_from_explicit_candidate_hashes(graph, tmp_path, monkeypatch):
    conn, scope = graph
    refresh(conn, scope)
    operation, execution, state, root = packet_fixture(tmp_path, conn, scope)
    monkeypatch.setattr(
        "harness.knowledge.review_projection.al_source", lambda *_: root / "state.json"
    )
    packet_hash, _, snapshot, permitted, _ = resolve(
        operation, HarnessController(conn), execution, state, scope, "fresh"
    )
    assert permitted
    locators = {
        conn.execute(
            "SELECT s.locator FROM knowledge_source_versions v "
            "JOIN knowledge_sources s USING(source_id) WHERE v.version_id=?",
            (version,),
        ).fetchone()[0]
        for version in permitted
    }
    assert locators == {"service.py"}
    restricted = replace(
        scope, purpose="fresh-review", packet_hash=packet_hash, permitted_source_versions=permitted
    )
    assert query(conn, restricted, ContextRequest("consumer")).payload["assertions"] == []
    ref = state["review"]["packets"]["fresh"]
    Path(ref["stored_path"]).write_text("tampered")
    with pytest.raises(ValueError):
        resolve(operation, HarnessController(conn), execution, state, scope, "fresh")


def test_planning_roles_use_task_bound_scope_and_claude_review_stays_closed(graph):
    conn, scope = graph
    refresh(conn, scope)
    controller = HarnessController(conn)
    execution = {"id": "e", "task_id": "t", "epoch_id": "legacy-0", "authority_generation": 0}
    state = {"phase": "PLANNING", "integration": {"agents": {}}}
    for role in ("al-architect", "al-designer", "al-scout"):
        principal = {"thread_id": role, "role": role, "writable": False}
        issued = derive_scope(controller, execution, principal, state, scope.repository)
        assert issued.purpose == "planning" and issued.task_id == "t"
    with pytest.raises(PermissionError):
        derive_scope(
            controller,
            execution,
            {"thread_id": "worker", "role": "al-engineer", "writable": False},
            state,
            scope.repository,
        )


def test_private_untracked_sources_do_not_leak_across_tasks_or_counts(graph):
    from harness.cli_knowledge import inspect_knowledge

    conn, scope = graph
    (scope.repository / "private.md").write_text("PRIVATE_TASK_CANARY")
    private = replace(scope, task_id="task-A")
    refresh(conn, private, RefreshRequest(included_paths=("private.md",)))
    first = query(conn, private, ContextRequest("PRIVATE_TASK_CANARY"))
    assert first.payload["assertions"]
    other = replace(scope, task_id="task-B")
    assert query(conn, other, ContextRequest("PRIVATE_TASK_CANARY")).payload["assertions"] == []
    assert inspect_knowledge(HarnessController(conn), scope.repository)["source_count"] == 2
    with pytest.raises(PermissionError):
        explain(conn, other, first.bundle_hash, first.payload["assertions"][0]["assertion_id"])


def test_current_operator_scope_survives_policy_generation_bump(graph):
    conn, scope = graph
    refresh(conn, scope)
    conn.execute("UPDATE knowledge_projects SET policy_generation=1")
    current = current_operator_scope(conn, scope.repository)
    assert current.policy_generation == 1
    assert query(conn, current, ContextRequest("service")).payload["assertions"]
    with pytest.raises(PermissionError):
        query(conn, scope, ContextRequest("service"))


@pytest.mark.parametrize("stage", ["fresh", "compare"])
def test_clean_review_requires_supported_creation_provenance_and_separate_invocation(
    graph, tmp_path, monkeypatch, stage
):
    conn, scope = graph
    refresh(conn, scope)
    operation, execution, state, root = packet_fixture(tmp_path, conn, scope, stage=stage)
    monkeypatch.setattr(
        "harness.knowledge.review_projection.al_source", lambda *_: root / "state.json"
    )
    controller = HarnessController(conn)
    tables = ("knowledge_review_projections", "knowledge_scope_issues", "knowledge_bundles")
    before = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    for host in ("claude", "codex"):
        principal = {
            "thread_id": "reviewer",
            "role": "al-architect",
            "writable": False,
            "native_host": host,
            "creation_provenance": datetime.now(UTC).isoformat(),
            "clean": True,
            "no_inheritance": True,
            "fork_turns": "none",
        }
        with pytest.raises(PermissionError, match="clean-review-provenance-unavailable"):
            derive_scope(
                controller, execution, principal, state, scope.repository, operation=operation
            )
    assert before == {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    conn.execute(
        "INSERT INTO knowledge_scope_issues VALUES (?,?,?,?,?)",
        (execution["id"], "reviewer", "planning", "", "fixture"),
    )
    with pytest.raises(PermissionError, match="clean-review-provenance-unavailable"):
        derive_scope(controller, execution, principal, state, scope.repository, operation=operation)
    assert (
        conn.execute("SELECT count(*) FROM knowledge_scope_issues").fetchone()[0]
        == before["knowledge_scope_issues"] + 1
    )


def test_previously_issued_execution_review_scope_cannot_query_or_explain(operations):
    operation, _, controller, _, _, execution, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "review-fixture.py").write_text("import service\n")
    subprocess.run(["git", "-C", str(root), "add", "review-fixture.py"], check=True)
    ordinary = current_operator_scope(controller.conn, root, task_id=execution["task_id"])
    refresh(controller.conn, ordinary)
    bundle = query(controller.conn, ordinary, ContextRequest("service"))
    for purpose in ("fresh-review", "comparison-review"):
        review = replace(
            ordinary,
            purpose=purpose,
            execution_id=execution["id"],
            epoch_id=execution["epoch_id"],
            authority_generation=execution["authority_generation"],
            packet_hash="prior-issued",
            permitted_source_versions=frozenset(
                c["version_id"] for c in bundle.payload["citations"]
            ),
        )
        from harness.db import utcnow
        from harness.knowledge.models import canonical
        from harness.knowledge.policy import policy_digest

        packet_hash = "prior-issued-" + purpose
        permitted_digest = digest(canonical(sorted(review.permitted_source_versions)).encode())
        review = replace(
            review,
            packet_hash=packet_hash,
            snapshot_id=bundle.payload["snapshot_id"],
            permitted_digest=permitted_digest,
            review_id="R-prior",
        )
        controller.conn.execute(
            "INSERT INTO knowledge_review_projections VALUES (?,?,?,?,?,?,?,?,?)",
            (
                review.project_id,
                packet_hash,
                execution["id"],
                "R-prior",
                "fresh" if purpose == "fresh-review" else "compare",
                review.snapshot_id,
                review.policy_generation,
                permitted_digest,
                utcnow(),
            ),
        )
        payload = dict(bundle.payload, purpose=purpose, policy_digest=policy_digest(review))
        legacy_hash = digest(canonical(payload).encode())
        controller.conn.execute(
            "INSERT INTO knowledge_bundles VALUES (?,?,?,?,?,?,?,NULL)",
            (
                legacy_hash,
                review.project_id,
                review.snapshot_id,
                payload["policy_digest"],
                purpose,
                canonical(payload),
                utcnow(),
            ),
        )
        with pytest.raises(PermissionError, match="clean-review-provenance-unavailable"):
            query(controller.conn, review, ContextRequest("service"))
        with pytest.raises(PermissionError, match="clean-review-provenance-unavailable"):
            explain(
                controller.conn,
                review,
                legacy_hash,
                bundle.payload["assertions"][0]["assertion_id"],
            )


def test_completed_outbox_contributions_are_unverified_idempotent_and_carrier_bound(operations):
    from harness.knowledge.contributions import ingest
    from tests.test_execution_operations import generated_handoff_fixture

    operation, supervisor, controller, initialization, _, execution, _ = operations
    root = Path(operation.envelope["repository"])
    public = operator_scope(root)
    (root / "knowledge.py").write_text("import service\n")
    subprocess.run(["git", "-C", str(root), "add", "knowledge.py"], check=True)
    refresh(controller.conn, public)
    parent = controller.conn.execute(
        "SELECT assertion_id FROM knowledge_assertions LIMIT 1"
    ).fetchone()[0]
    child, _ = generated_handoff_fixture(operations)
    report = operation.artifacts.submit_output(
        child,
        "report.json",
        json.dumps(
            {
                "assertions": [
                    {
                        "subject": "module:reported",
                        "predicate": "assumes",
                        "object": None,
                        "value": None,
                        "text": "ATTRIBUTED_REPORT_CANARY",
                        "parents": [parent],
                    }
                ]
            }
        ),
        1,
        1,
    )
    resource = "artifact:" + report["artifact_id"] + ":" + report["sha256"]
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["agents"][child["thread_id"]].update(
        status="Completed", ended_at=time.time(), latest_output=resource
    )
    path.write_text(json.dumps(state))
    with pytest.raises(PermissionError):
        ingest(operation, controller, child, "handoff-task", resource)
    ingested = ingest(operation, controller, supervisor, "handoff-task", resource)
    assert ingested["status"] == "published"
    assert (
        ingest(operation, controller, supervisor, "handoff-task", resource)["status"] == "unchanged"
    )
    scoped = current_operator_scope(controller.conn, root, task_id=execution["task_id"])
    bundle = query(controller.conn, scoped, ContextRequest("ATTRIBUTED_REPORT_CANARY"))
    claim = next(a for a in bundle.payload["assertions"] if a["predicate"] == "assumes")
    assert claim["verification"] == "unverified" and claim["origin"] == "attributed-inference"
    assert any(
        c["locator"].startswith("contribution:") and c["assertion_id"] == claim["assertion_id"]
        for c in bundle.payload["citations"]
    )
    assert explain(controller.conn, scoped, bundle.bundle_hash, claim["assertion_id"])["citations"]
    assert (
        query(controller.conn, public, ContextRequest("ATTRIBUTED_REPORT_CANARY")).payload[
            "assertions"
        ]
        == []
    )


def test_admitted_planning_tool_uses_closed_server_scope(operations):
    operation, supervisor, controller, _, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "planning.py").write_text("import service\n")
    subprocess.run(["git", "-C", str(root), "add", "planning.py"], check=True)
    refresh(controller.conn, operator_scope(root))
    planner = supervisor | {"thread_id": "planning-scout", "role": "al-scout", "writable": False}
    result = operation.dispatch(planner, "knowledge_query", {"query": "service"})
    assert result["purpose"] == "planning" and result["assertions"]
    before = controller.conn.execute("SELECT count(*) FROM knowledge_scope_issues").fetchone()[0]
    with pytest.raises(ValueError, match="closed knowledge query"):
        operation.dispatch(
            planner, "knowledge_query", {"query": "service", "purpose": "fresh-review"}
        )
    assert (
        controller.conn.execute("SELECT count(*) FROM knowledge_scope_issues").fetchone()[0]
        == before
    )
    with pytest.raises(ValueError):
        operation.dispatch(
            supervisor,
            "knowledge_explain",
            {
                "bundle_hash": result["bundle_hash"],
                "assertion_id": result["assertions"][0]["assertion_id"],
            },
        )


def test_private_source_requires_nonempty_approved_task_scope(graph):
    from harness.knowledge.extract import extract_source
    from harness.knowledge.models import EXTRACTOR_VERSION, PreparedSnapshot, identity
    from harness.knowledge.sources import enumerate_sources
    from harness.knowledge.store import publish_snapshot

    conn, scope = graph
    inventory = enumerate_sources(scope, RefreshRequest())
    source = inventory.sources[0]
    private = replace(
        source,
        visibility="task:",
        version_id=identity(
            source.source_id, source.content_sha256, EXTRACTOR_VERSION, source.kind, "task:"
        ),
    )
    inventory = replace(inventory, sources=(private,))
    with pytest.raises(ValueError, match="visibility exceeds task scope"):
        publish_snapshot(
            conn,
            PreparedSnapshot(scope, inventory, (extract_source(private),), 0),
            expected_active=None,
        )


@pytest.mark.parametrize("commit", [False, True])
def test_completed_report_retains_repository_memberships_and_coverage(operations, commit):
    from harness.cli_knowledge import inspect_knowledge
    from harness.db import connect
    from harness.knowledge.contributions import ingest
    from tests.test_execution_operations import generated_handoff_fixture
    from tests.test_knowledge_lifecycle import git

    operation, supervisor, controller, initialization, _, execution, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "unsupported.js").write_text("public limitation")
    (root / "private-malformed.py").write_text("{")
    git(root, "add", "unsupported.js")
    if commit:
        git(
            root,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "coverage",
        )
    scoped = current_operator_scope(controller.conn, root, task_id=execution["task_id"])
    initial = refresh(
        controller.conn,
        scoped,
        RefreshRequest(commit="HEAD")
        if commit
        else RefreshRequest(included_paths=("private-malformed.py",)),
    )
    child, _ = generated_handoff_fixture(operations)
    report = operation.artifacts.submit_output(
        child, "report.json", json.dumps({"summary": "COMPLETED_REPORT_COVERAGE"}), 1, 1
    )
    resource = "artifact:" + report["artifact_id"] + ":" + report["sha256"]
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["agents"][child["thread_id"]].update(
        status="Completed", ended_at=time.time(), latest_output=resource
    )
    path.write_text(json.dumps(state))
    ingest(operation, controller, supervisor, "handoff-task", resource)
    database = controller.conn.execute("PRAGMA database_list").fetchone()[2]
    with connect(Path(database)) as reopened:
        reader = HarnessController(reopened)
        status = inspect_knowledge(reader, root, task_id=execution["task_id"])
        old = {
            r[0]
            for r in reopened.execute(
                "SELECT source_id FROM knowledge_snapshot_sources WHERE snapshot_id=?",
                (initial.snapshot_id,),
            )
        }
        active = {
            r[0]
            for r in reopened.execute(
                "SELECT source_id FROM knowledge_snapshot_sources WHERE snapshot_id=?",
                (status["active_snapshot_id"],),
            )
        }
        assert old <= active
        assert any(c.get("locator") == "unsupported.js" for c in status["snapshot_coverage"])
        if not commit:
            assert "private-malformed.py" in json.dumps(status)
            assert "private-malformed.py" not in json.dumps(
                inspect_knowledge(reader, root, task_id="B")
            )
        bundle = query(reopened, scoped, ContextRequest("COMPLETED_REPORT_COVERAGE"))
        assert bundle.payload["assertions"]
        assert any(c.get("locator") == "unsupported.js" for c in bundle.payload["coverage"])
    # Explicit commit refresh is a repository-only projection, not a report augmentation.
    if commit:
        refresh(controller.conn, scoped, RefreshRequest(commit="HEAD"))
        assert not query(
            controller.conn, scoped, ContextRequest("COMPLETED_REPORT_COVERAGE")
        ).payload["assertions"]


def completed_summary_report(operations, document):
    from tests.test_execution_operations import generated_handoff_fixture

    operation, _, _, initialization, _, _, _ = operations
    child, _ = generated_handoff_fixture(operations)
    raw = json.dumps(document)
    report = operation.artifacts.submit_output(child, "report.json", raw, 1, 1)
    resource = "artifact:" + report["artifact_id"] + ":" + report["sha256"]
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["agents"][child["thread_id"]].update(
        status="Completed", ended_at=time.time(), latest_output=resource
    )
    path.write_text(json.dumps(state))
    return child, resource, raw.encode()


@pytest.mark.parametrize("form", ["facts", "answer", "summary"])
def test_completed_report_summary_aliases_keep_inference_provenance_and_task_scope(
    operations, form
):
    from harness.knowledge.contributions import ingest

    operation, supervisor, controller, _, _, execution, _ = operations
    claim = "ApprovalGate records specification content_hash and review_state_hash."
    document = (
        {
            "title": "Implementation-handoff approval provenance",
            "source_revision": "a" * 40,
            "scope": "Read-only source investigation",
            "facts": [{"claim": claim, "sources": ["harness/approval.py:56-74"]}],
            "authority_conclusion": "Historical explanation grants no implementation authority.",
            "limits": ["No current task-specific approval is asserted."],
            "source_hashes": {"harness/approval.py": "b" * 64},
        }
        if form == "facts"
        else {form: claim, "atomic_claims": [{"claim": claim}]}
    )
    child, resource, raw = completed_summary_report(operations, document)
    with pytest.raises(PermissionError):
        ingest(operation, controller, child, "handoff-task", resource)
    result = ingest(operation, controller, supervisor, "handoff-task", resource)
    assert result["status"] == "published"
    assert (
        ingest(operation, controller, supervisor, "handoff-task", resource)["status"] == "unchanged"
    )
    root = Path(operation.envelope["repository"])
    scoped = current_operator_scope(controller.conn, root, task_id=execution["task_id"])
    bundle = query(controller.conn, scoped, ContextRequest("ApprovalGate"))
    item = next(a for a in bundle.payload["assertions"] if a["predicate"] == "observes")
    assert item["text"] and claim in item["text"]
    assert item["subject"] == "report:" + digest(raw)
    assert item["origin"] == "attributed-inference" and item["verification"] == "unverified"
    assert item["applicability"]["producer_role"] == child["role"]
    citation = next(
        c for c in bundle.payload["citations"] if c["assertion_id"] == item["assertion_id"]
    )
    assert citation["content_sha256"] == citation["span_sha256"] == digest(raw)
    assert citation["start_byte"] == 0 and citation["end_byte"] == len(raw)
    observation = json.loads(citation["observation_json"])
    assert observation["producer"] == child["thread_id"]
    assert observation["execution_id"] == execution["id"]
    assert observation["artifact_sha256"] == digest(raw)
    assert explain(
        controller.conn, scoped, bundle.bundle_hash, item["assertion_id"], include_evidence=True
    )["citations"][0]["text"]
    assert not query(
        controller.conn,
        current_operator_scope(controller.conn, root, task_id="other-task"),
        ContextRequest("ApprovalGate"),
    ).payload["assertions"]


@pytest.mark.parametrize(
    "document",
    [
        {"answer": "  "},
        {"answer": {"claim": "unsupported"}},
        {"facts": []},
        {"facts": {"claim": "unsupported"}},
        {"facts": [{"claim": ""}]},
        {"facts": [{"claim": "text", "sources": 7}]},
        {"facts": [None]},
        {"other": "not a supported summary"},
        {"answer": "AKIA0000000000000000"},
        {
            "answer": "valid text",
            "assertions": [
                {"subject": "x", "predicate": "y", "text": "z", "verification": "source-backed"}
            ],
        },
    ],
)
def test_completed_report_invalid_summary_aliases_and_guard_bypasses_refuse(operations, document):
    from harness.knowledge.contributions import ingest

    operation, supervisor, controller, _, _, _, _ = operations
    _, resource, _ = completed_summary_report(operations, document)
    with pytest.raises(ValueError):
        ingest(operation, controller, supervisor, "handoff-task", resource)
    assert (
        controller.conn.execute("SELECT count(*) FROM knowledge_contributions").fetchone()[0] == 0
    )
