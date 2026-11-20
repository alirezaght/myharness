"""Prepared default selection is source-bound and does not confer check authority."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from al.installer import verification_contracts as C
from harness.execution_contract import digest
from harness.task_environment import SetupReport, context_revision, prepare
from harness.task_tooling import TaskTooling


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "repository"
    (root / "al").mkdir(parents=True)
    source = Path(__file__).parents[1] / "al/verification-harness.json"
    (root / "al/verification-harness.json").write_bytes(source.read_bytes())
    (root / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0"\n')
    (root / "uv.lock").write_text("version = 1\n")
    tooling = TaskTooling(tmp_path / "tooling", root, {"epoch": "one"})
    record = {
        "kind": "python",
        "inputs": {
            name: digest((root / name).read_bytes()) for name in ("pyproject.toml", "uv.lock")
        },
        "tools": digest(tooling.catalog()["tools"]),
        "setup_id": "exact-setup",
        "environment": str(tmp_path / "venv"),
        "sources": [str(root)],
    }
    state = {
        "amendments": [],
        "integration": {
            "graph_revision": 1,
            "tasks": {"T": {"revision": 1, "files": ["harness/storage.py"]}},
        },
    }
    request = {
        "operation": "prepare",
        "cwd": ".",
        "task_id": "T",
        "expected_revision": context_revision(state, "T"),
    }
    return tooling, record, state, request


def test_descriptor_binds_source_task_setup_and_current_tooling(prepared):
    tooling, record, state, request = prepared
    value = tooling.check_descriptor(state, request, record)
    descriptor = C.validate_task_recipe(value["descriptor"])
    assert descriptor["executor"] == "supervisor"
    assert descriptor["revision"] == context_revision(state, "T")
    assert tooling.check_descriptor(state, request, record) == value
    assert len(tooling.inspect()["check_recipes"]) == 1
    (tooling.repository / "uv.lock").write_text("changed")
    with pytest.raises(ValueError, match="stale"):
        tooling.check_descriptor(state, request, record)


@pytest.mark.parametrize(
    "files,reason",
    [(["docs/design.md"], "recipe_unrelated"), (["unknown/config"], "recipe_unknown")],
)
def test_unrelated_and_unknown_tasks_get_no_fabricated_check(prepared, files, reason):
    tooling, record, state, request = prepared
    state["integration"]["tasks"]["T"]["files"] = files
    result = tooling.check_descriptor(state, request, record)
    assert result["reason"] == reason and "descriptor" not in result


def test_inspection_does_not_create_recipe_or_environment_state(prepared):
    tooling, _, _, _ = prepared
    assert tooling.inspect()["check_recipes"] == []
    assert not (tooling.directory / "check-recipes.json").exists()
    assert not (tooling.directory / "environments.json").exists()


def test_cached_success_invokes_same_internal_ready_hook(prepared):
    tooling, record, state, request = prepared
    tooling.ready(".", record)
    calls = []
    operation = SimpleNamespace(
        tooling=tooling,
        envelope={"repository": str(tooling.repository)},
        processes=SimpleNamespace(records={}),
        setup_pending={},
        prepared_checks=lambda principal, request, record, **kw: (
            calls.append(kw) or {"status": "Pending"}
        ),
    )
    result = prepare(operation, None, {"role": "orchestrator"}, state, request, plan_defaults=False)
    assert result["status"] == "Ready" and result["cached"] is True
    assert calls == [{"plan_defaults": False}]
    assert result["checks"]["status"] == "Pending"


def test_async_setup_publishes_ready_before_check_reconciliation(prepared):
    tooling, record, _, _ = prepared
    calls = []
    report = SetupReport(
        tooling, ".", record, lambda actual: calls.append(actual) or {"status": "Pending"}
    )
    result = report.publish(0)
    assert result["status"] == "Ready" and result["test_evidence"] is False
    assert calls == [record] and result["checks"]["status"] == "Pending"
    assert json.loads((tooling.directory / "environments.json").read_bytes())["."] == record


def test_legacy_recipe_is_readable_but_never_auto_selected(prepared):
    tooling, record, state, request = prepared
    path = tooling.repository / "al/verification-harness.json"
    value = json.loads(path.read_bytes())
    value["schema_version"] = 1
    value.pop("task_applicability")
    path.write_text(json.dumps(value))
    C.validate_recipe(value)
    assert tooling.check_descriptor(state, request, record)["reason"] == "recipe_unknown"


# The existing fixture uses synthetic host identity with real approval, private
# registry, OS containment, pinned AL command and execution authority boundaries.
from tests.test_execution_operations import operations  # noqa: E402,F401


@pytest.mark.parametrize("cached", [True, False])
def test_authenticated_cached_prepare_creates_real_public_plan_once(operations, cached):  # noqa: F811
    operation, principal, _, initialization, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "al").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "harness").mkdir(exist_ok=True)
    (root / "tests/test_sample.py").write_text("def test_sample(): pass\n")
    (root / "harness/component.py").write_text("VALUE = 1\n")
    source = Path(__file__).parents[1] / "al/verification-harness.json"
    (root / "al/verification-harness.json").write_bytes(source.read_bytes())
    (root / "pyproject.toml").write_text(
        '[project]\nname="prepared-fixture"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n'
    )
    (root / "uv.lock").write_text(
        'version=1\nrevision=3\nrequires-python=">=3.11"\n[[package]]\nname="prepared-fixture"\nversion="0.1.0"\nsource={virtual="."}\n'
    )
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"].update(
        tasks={"T": {"revision": 1, "files": ["harness"]}}, graph_revision=1
    )
    path.write_text(json.dumps(state))
    tooling = operation.tooling
    record = {
        "kind": "python",
        "inputs": {
            name: digest((root / name).read_bytes()) for name in ("pyproject.toml", "uv.lock")
        },
        "tools": digest(tooling.catalog()["tools"]),
        "setup_id": "prepared-fixture",
        "environment": str(root / ".venv"),
        "sources": [str(root)],
    }
    if cached:
        tooling.ready(".", record)
    request = {
        "operation": "prepare",
        "cwd": ".",
        "task_id": "T",
        "expected_revision": context_revision(state, "T"),
    }
    result = operation.dispatch(principal, "environment_operation", request)
    if not cached:
        handle = result["handle"]
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            observed = operation.dispatch(principal, "poll_process", {"handle": handle})
            if observed["status"] != "Running":
                break
            time.sleep(0.05)
        assert observed["status"] != "Running", observed
        result = observed["result"]
    assert result["status"] == "Ready"
    assert result["checks"]["status"] == "Planned", json.dumps(result, indent=2)
    current = json.loads(path.read_bytes())
    area = result["checks"]["area"]
    assert current["evidence"]["plan"][area]["recipe_context"] == result["checks"]["descriptor"]
    assert not current["evidence"]["entries"] and not current["writers"]
    index = operation.artifacts.directory / "index.json"
    before = index.read_bytes()
    repeated = operation.dispatch(principal, "environment_operation", request)
    assert repeated["checks"]["status"] == "Planned" and repeated["checks"]["reused"] is True
    assert index.read_bytes() == before
    current["evidence"]["plan"][area]["tier"] = "focused"
    path.write_text(json.dumps(current))
    conflicted = operation.dispatch(principal, "environment_operation", request)
    assert conflicted["checks"]["status"] == "Pending"
    assert json.loads(path.read_bytes())["evidence"]["plan"][area]["tier"] == "focused"


def test_descriptor_execution_rejects_forged_or_replaced_prepared_binding(prepared):
    tooling, record, state, request = prepared
    tooling.ready(".", record)
    descriptor = tooling.check_descriptor(state, request, record)["descriptor"]
    assert tooling.validate_check_descriptor(descriptor) == descriptor
    forged = descriptor | {"prepared_binding": "0" * 64}
    with pytest.raises(ValueError, match="protected state"):
        tooling.validate_check_descriptor(forged)
    tooling.ready(".", record | {"setup_id": "replacement"})
    with pytest.raises(ValueError, match="runtime binding changed"):
        tooling.validate_check_descriptor(descriptor)
