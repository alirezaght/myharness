"""Synthetic-only evaluation contracts; these establish no live/provider acceptance."""

import copy
import json
import os
from contextlib import closing
from pathlib import Path

import pytest

from harness.analyzer import analyze
from harness.db import connect
from harness.design_models import DesignSection
from harness.interview_catalogue import CATALOGUE
from scripts.knowledge_evaluation.admission import admit
from scripts.knowledge_evaluation.package import BUDGETS, PROVENANCE, corpus_receipt, validate
from scripts.knowledge_native_eval import execute
from tests.test_execution_operations import operations as initialized_operations


@pytest.fixture
def operations(controller, monkeypatch, launched, request):
    """Add one meaningful tracked source BEFORE ordinary fixture approval/initialization."""
    from tests.sample_repo import git_init

    root = launched[2]
    source = root / "synthetic/source.py"
    source.parent.mkdir()
    source.write_text("def synthetic_value():\n    return 7\n")
    git_init(root)
    yield from initialized_operations.__wrapped__(controller, monkeypatch, launched, request)


def case(root, tmp_path):
    answers = {
        q.text: "Read-only sourced explanation; preserve approvals and record unavailable cases."
        for q in CATALOGUE
    }
    answers.update(
        {
            q.text: "This evaluation inspects only this frozen synthetic repository. "
            "No product or dependency changes are authorized."
            for q in analyze(root, "Explain demo/export.py architecture").questions
        }
    )
    return {
        "schema_version": 1,
        "case_id": "synthetic-public-case",
        "arm": "B",
        "participant": "single",
        "prompt": "Explain demo/export.py architecture",
        "host": "codex",
        "role": "al-architect",
        "model": "gpt-6-astra",
        "effort": "medium",
        "corpus": {"root": str(root), **corpus_receipt(root)},
        "allowed_roots": [str(root)],
        "state_directory": str(tmp_path / "evaluation-state"),
        "output_directory": str(tmp_path / "evaluation-output"),
        "provenance": {
            "accepted_mission_sha256": "1" * 64,
            "evaluation_authorization_sha256": "2" * 64,
            "statement": PROVENANCE,
        },
        "budgets": dict(BUDGETS),
        "specification": {
            "request": "Explain demo/export.py architecture using read-only corpus evidence.",
            "answers": answers,
            "design": {
                str(s): "Evaluation uses a frozen corpus and private read-only adapter; "
                "return a sourced answer and preserve native refusals."
                for s in DesignSection
            },
            "scenarios": [
                {
                    "category": "e2e",
                    "text": "An actual specialized role reads frozen "
                    "sources and submits one sourced answer without repository mutation.",
                },
                {
                    "category": "failure",
                    "text": "Deny knowledge in B at listing and "
                    "dispatch; reject alternate resources, aliases and excess budgets.",
                },
            ],
        },
    }


def test_default_validation_is_read_only(sample_repo, tmp_path):
    package = case(sample_repo, tmp_path)
    assert execute(package, "validate")["provider_started"] is False
    assert not Path(package["state_directory"]).exists()
    assert not Path(package["output_directory"]).exists()


@pytest.mark.parametrize("change", ["extra", "budget", "overlap", "provenance", "other-root"])
def test_closed_package_refusals(sample_repo, tmp_path, change):
    package = case(sample_repo, tmp_path)
    if change == "extra":
        package["gold"] = "forbidden"
    elif change == "budget":
        package["budgets"]["tool_calls"] = 31
    elif change == "overlap":
        package["output_directory"] = str(sample_repo / "outputs")
    elif change == "provenance":
        package["provenance"]["statement"] = "user clicked approval"
    else:
        package["allowed_roots"].append(str(tmp_path))
    with pytest.raises(ValueError):
        validate(package)


@pytest.mark.parametrize("change", ["dirty", "untracked", "hardlink", "symlink", "head"])
def test_exact_corpus_refusals(sample_repo, tmp_path, change):
    package = case(sample_repo, tmp_path)
    path = sample_repo / "README.md"
    if change == "dirty":
        path.write_text("future candidate material")
    elif change == "untracked":
        (sample_repo / "future.py").write_text("x = 1")
    elif change == "hardlink":
        os.link(path, tmp_path / "alias")
    elif change == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "forbidden")
    else:
        package["corpus"]["head"] = "0" * 40
    with pytest.raises((ValueError, FileNotFoundError)):
        validate(package)


def test_genuine_preparation_and_immutable_output(sample_repo, tmp_path):
    package = case(sample_repo, tmp_path)
    result = execute(package, "prepare")
    assert result["status"] == "prepared" and result["provider_started"] is False
    admission = json.loads((Path(package["output_directory"]) / "admission.json").read_bytes())
    assert admission["new_user_approval_event"] is False
    assert admission["approval"]["content_hash"]
    assert PROVENANCE in Path(admission["approval"]["path"]).read_text()
    assert corpus_receipt(sample_repo)["head"] == package["corpus"]["head"]
    with pytest.raises(ValueError, match="existing state/output"):
        execute(package, "prepare")


def test_incomplete_concrete_interview_refuses(sample_repo, tmp_path):
    package = case(sample_repo, tmp_path)
    package["specification"]["answers"] = {"nonmatching question": "concrete answer"}
    Path(package["state_directory"]).mkdir()
    with pytest.raises(ValueError, match="no answer for"):
        admit(package)


def make_adapter(operations, tmp_path, monkeypatch, arm="B"):
    from scripts.knowledge_evaluation.adapter import EvaluatorAdapter

    operation, principal, *_ = operations
    root = Path(operation.envelope["repository"])
    # This existing initialized fixture includes untracked installation/spec files.
    # Mock ONLY its frozen observation for adapter unit contracts; clean corpus tests above
    # and the complete synthetic worker integration below retain real validation.
    snapshot = {
        "head": "synthetic",
        "tree": "synthetic",
        "files_sha256": "synthetic",
        "file_count": 0,
    }
    monkeypatch.setattr("scripts.knowledge_evaluation.adapter.corpus_receipt", lambda _: snapshot)
    monkeypatch.setattr(__name__ + ".corpus_receipt", lambda _: snapshot)
    package = case(root, tmp_path)
    package["arm"] = arm
    return EvaluatorAdapter(operation, package), principal


def test_actual_initialized_synthetic_authority_and_baseline(operations, tmp_path, monkeypatch):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    from tests.test_execution_continuity import child

    supervisor = principal
    with pytest.raises(ValueError, match="source prefetch"):
        adapter.dispatch(supervisor, "read_file", {"path": "evaluation:analyze"})
    adapter.dispatch(supervisor, "await_assignment", {"timeout": 1})
    principal = child(adapter.operation, "al-architect")
    delivered_before = adapter.delivered
    result = adapter.dispatch(principal, "read_file", {"path": "evaluation:analyze"})
    assert result["repo_root"]
    assert adapter.delivered - delivered_before == len(json.dumps(result).encode())
    adapter.dispatch(principal, "read_file", {"path": "evaluation:analyze"})
    assert adapter.delivered - delivered_before == 2 * len(json.dumps(result).encode())
    forged = copy.deepcopy(principal)
    forged["policy"] = "forged"
    with pytest.raises(ValueError):
        adapter.dispatch(forged, "read_file", {"path": "evaluation:analyze"})
    assert adapter.receipt()["actual_native_proof"] is False

    from harness.execution_mcp import MCPConnection
    from scripts.knowledge_evaluation.live import connection_class

    connection = connection_class(MCPConnection)(lambda _: principal, adapter.dispatch)
    connection.request({"jsonrpc": "2.0", "method": "initialize"})
    listing = connection.request({"jsonrpc": "2.0", "method": "tools/list"})
    assert {t["name"] for t in listing["tools"]} == adapter.allowed
    response = connection.request(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "knowledge_query",
                "arguments": {"query": "forbidden"},
            },
        }
    )
    assert response["isError"] is True


@pytest.mark.parametrize(
    "name,args",
    [
        ("knowledge_query", {"query": "hidden"}),
        ("knowledge_explain", {"bundle_hash": "x", "assertion_id": "x"}),
        ("read_file", {"path": "harness:assignment"}),
        ("read_file", {"path": "harness:knowledge"}),
        ("read_file", {"path": "evaluation:refresh"}),
        ("read_file", {"path": ".env"}),
        ("read_file", {"path": "../other-arm/answer.md"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("read_file", {"path": ".git/config"}),
        ("al_command", {"arguments": ["show"]}),
        ("diagnostic", {"request": {"kind": "source", "path": ".env"}}),
        ("environment_operation", {"operation": "prepare"}),
    ],
)
def test_actual_initialized_synthetic_bypass_refusal(operations, tmp_path, monkeypatch, name, args):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        adapter.dispatch(principal, name, args)
    assert "knowledge_query" not in adapter.allowed
    assert "knowledge_explain" not in adapter.allowed


def test_visible_budgets_and_null_accounting(operations, tmp_path, monkeypatch):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    adapter.delivered = BUDGETS["delivered_bytes"]
    with pytest.raises(ValueError):
        adapter.dispatch(principal, "read_file", {"path": "README.md"})
    assert adapter.delivered == BUDGETS["delivered_bytes"]
    adapter.calls = BUDGETS["tool_calls"]
    with pytest.raises(ValueError):
        adapter.dispatch(principal, "read_file", {"path": "README.md"})
    adapter.started -= BUDGETS["wall_seconds"]
    with pytest.raises(ValueError, match="wall budget"):
        adapter.check_time()
    assert adapter.receipt()["service_read_bytes"] is None
    assert adapter.receipt()["output_tokens"] is None


@pytest.mark.parametrize("arm", ["B", "C"])
def test_complete_clean_synthetic_worker_uses_selected_bridge(
    sample_repo, tmp_path, monkeypatch, arm
):
    """Actual admission/lease/initialization/runtime/cleanup; synthetic CLI, zero provider use."""
    from tests.execution_helpers import isolated_launch_environment

    isolated_launch_environment(monkeypatch, tmp_path / "isolated-tooling")
    package = case(sample_repo, tmp_path)
    package.update(host="claude", model="fable", arm=arm)
    before = corpus_receipt(sample_repo)
    result = execute(package, "live")
    assert result["status"] == "unavailable", result
    output = Path(package["output_directory"])
    assert (output / "worker-reservation.json").exists(), result
    receipt = json.loads((output / "native-result.json").read_bytes())
    assert receipt["native_id"]
    assert receipt["execution_status"] == "Interrupted"
    assert receipt["mission_complete"] is False
    assert receipt["modules"]["harness.execution_worker"]["path"].startswith(
        receipt["selected_bridge"] + "/harness/"
    )
    admission = json.loads((output / "admission.json").read_bytes())
    with connect(admission["database"]) as conn:
        attempts = conn.execute(
            "SELECT status,worker_pid,child_pid FROM execution_attempts"
        ).fetchall()
    assert len(attempts) == 1
    assert attempts[0]["status"] == "Exited"
    assert attempts[0]["worker_pid"] and attempts[0]["child_pid"]
    assert corpus_receipt(sample_repo) == before


def test_import_origin_guard_refuses_checkout_modules(tmp_path):
    from scripts.knowledge_evaluation.live import module_receipt

    with pytest.raises(ValueError, match="conflicting Harness import"):
        module_receipt(tmp_path)


def test_c_real_controller_query_with_synthetic_authenticated_child(
    operations, tmp_path, monkeypatch
):
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.refresh import refresh
    from tests.test_execution_continuity import child

    adapter, supervisor = make_adapter(operations, tmp_path, monkeypatch, arm="C")
    operation = adapter.operation
    adapter.dispatch(supervisor, "await_assignment", {"timeout": 1})
    principal = child(operation, "al-architect")
    with closing(connect(operation.database)) as conn:
        refresh(conn, current_operator_scope(conn, adapter.root))
    result = adapter.dispatch(principal, "knowledge_query", {"query": "architecture"})
    assert isinstance(result, dict)
    assert principal["thread_id"] in adapter.registered
    with closing(connect(operation.database)) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM operation_audit WHERE request LIKE ?", ("%knowledge_query%",)
            ).fetchone()[0]
            >= 1
        )


@pytest.mark.parametrize("field", ["arm", "host", "role", "participant"])
def test_malformed_identity_fields_refuse_cleanly(sample_repo, tmp_path, field):
    package = case(sample_repo, tmp_path)
    package[field] = []
    with pytest.raises(ValueError, match="bounded text"):
        validate(package)


def test_package_special_file_refused_without_reading(tmp_path):
    from scripts.knowledge_evaluation.package import load

    fifo = tmp_path / "package-pipe"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular case package"):
        load(fifo)


@pytest.mark.parametrize("arm", ["B", "C"])
def test_engineer_actual_synthetic_task_source_query_and_output(
    operations, tmp_path, monkeypatch, arm
):
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.refresh import refresh
    from tests.test_execution_continuity import child

    adapter, supervisor = make_adapter(operations, tmp_path, monkeypatch, arm=arm)
    adapter.package["role"] = "al-engineer"
    adapter.package["specification"]["task_files"] = ["synthetic/source.py"]
    operation = adapter.operation
    adapter.dispatch(supervisor, "await_assignment", {"timeout": 1})
    principal = child(operation, "al-engineer")
    assert principal["writable"] is True
    monkeypatch.setattr(
        "harness.task_environment.prepare",
        lambda *a, **kw: pytest.fail("evaluation registration must not launch automatic setup"),
    )
    monkeypatch.setattr(
        "harness.knowledge.integration.assignment_context",
        lambda *a: pytest.fail("evaluation must not retrieve automatic assignment knowledge"),
    )
    assignment = adapter.dispatch(principal, "await_assignment", {"timeout": 1})
    assert assignment["status"] == "Assigned"
    assert assignment["handoff"]["status"] == "unavailable"
    assert assignment["knowledge_context"]["status"] == "unavailable"
    source = adapter.dispatch(principal, "read_file", {"path": "synthetic/source.py"})
    assert "synthetic_value" in source["text"]
    with closing(connect(operation.database)) as conn:
        if arm == "C":
            refresh(conn, current_operator_scope(conn, adapter.root))
        else:
            assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 0
    if arm == "C":
        assert isinstance(
            adapter.dispatch(principal, "knowledge_query", {"query": "synthetic"}), dict
        )
    else:
        with pytest.raises(ValueError):
            adapter.dispatch(principal, "knowledge_query", {"query": "synthetic"})
    result = adapter.dispatch(
        principal,
        "submit_output",
        {
            "name": "answer.md",
            "content": "synthetic/source.py defines synthetic_value.",
            "expected_graph_revision": assignment["graph_revision"],
        },
    )
    assert result["owner"] == principal["thread_id"] and result["role"] == "al-engineer"
    state = operation.state(operations[5])
    assert state["writers"] == {}
    assert state["integration"]["tasks"]["evaluation"]["status"] == "Idle"
    assert "admission" not in state["integration"]["tasks"]["evaluation"]
    assert state["integration"]["agents"][principal["thread_id"]]["task"] == "evaluation"
    assert operation.processes.records == {}


@pytest.mark.parametrize("scope", [[], [".git"], [".env"], ["missing.py"], ["../escape"]])
def test_engineer_task_scope_refusals(sample_repo, tmp_path, scope):
    package = case(sample_repo, tmp_path)
    package["role"] = "al-engineer"
    package["specification"]["task_files"] = scope
    with pytest.raises(ValueError):
        validate(package)
