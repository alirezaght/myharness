# ruff: noqa: F811
"""Actual setup dispatch ownership, separate from optional budgets and outer AL control."""

import threading
from pathlib import Path

from harness.policy.context import CURRENT
from harness.task_environment import context_revision
from tests.test_execution_continuity import child, command, prepare, report, state
from tests.test_execution_operations import operations  # noqa: F401


def locked_unit(operations):
    operation, principal, *_ = operations
    prepare(operation, principal)
    unit = Path(operation.envelope["repository"]) / "work"
    unit.mkdir()
    (unit / "pyproject.toml").write_text(
        '[project]\nname="queued-fixture"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n'
    )
    (unit / "uv.lock").write_text(
        'version=1\nrevision=3\nrequires-python=">=3.11"\n'
        '[[package]]\nname="queued-fixture"\nversion="0.1.0"\nsource={virtual="."}\n'
    )
    return operation, principal


def read_owners(monkeypatch):
    import harness.task_environment as setup

    actual, seen = setup.ordinary, []
    caller = threading.get_ident()

    def read(*args, **kwargs):
        attempt = CURRENT.get()
        if threading.get_ident() == caller:
            seen.append(None if attempt is None else attempt.intent)
        return actual(*args, **kwargs)

    monkeypatch.setattr(setup, "ordinary", read)
    return seen


def test_unowned_no_budget_queue_enters_actual_dispatch_before_bytes(operations, monkeypatch):
    operation, principal = locked_unit(operations)
    current = state(operation)
    request = dict(
        operation="prepare",
        cwd="work",
        task_id="A",
        expected_revision=context_revision(current, "A"),
    )
    # A scheduler request has no effect permission of its own.
    operation.setup_pending[("A", "work")] = principal, request
    seen = read_owners(monkeypatch)
    operation.tick()
    assert seen and all(v is not None for v in seen), "setup bytes read without a dispatch Attempt"
    assert all(v.operation == "environment_operation" and v.task_id == "A" for v in seen)
    assert operation.setup_outcomes["work"].get("handle")


def test_agent_registration_defers_setup_bytes_to_own_dispatch(operations, monkeypatch):
    operation, principal = locked_unit(operations)
    engineer = child(operation, "al-engineer")
    seen = read_owners(monkeypatch)
    report(
        operation,
        principal,
        "agent-register",
        {
            "id": engineer["thread_id"],
            "name": "Observed queued engineer",
            "role": "al-engineer",
            "task": "A",
        },
    )
    assert seen == [], "auto-setup read bytes under the unrelated outer AL Attempt"
    assert ("A", "work") in operation.setup_pending
    assert ("A", "work") not in operation.policy_setup_pending
    operation.tick()
    assert seen and all(
        v is not None and v.operation == "environment_operation" and v.task_id == "A" for v in seen
    )
    assert operation.setup_outcomes["work"].get("handle")


def registered_setup(operations, files, task_id="T"):
    """Use public registration, not an incomplete synthetic task or invented permission."""
    operation, principal, controller, *_ = operations
    command(operation, principal, "spec-gate", "SPEC_READY")
    command(
        operation,
        principal,
        "classify",
        "STANDARD",
        "--designer",
        "no",
        "--implementers",
        "al-engineer",
    )
    report(
        operation,
        principal,
        "task-register",
        {
            "id": task_id,
            "title": "Scoped dependency setup",
            "files": files,
            "criteria": ["AC-01"],
            "dependencies": [],
        },
    )
    command(operation, principal, "phase", "IMPLEMENTATION")
    return operation, principal, controller, state(operation)


def admitted_setup(operation, principal, controller, current, request):
    """Invoke the handler under real admission, retaining its original OS exception type."""
    from harness.policy.context import scope, start_effect
    from harness.policy.resources import prepare as reserve
    from harness.task_environment import prepare as handler

    attempt = reserve(operation, controller, principal, "environment_operation", request, current)
    with scope(attempt):
        try:
            start_effect()
            result = handler(operation, controller, principal, current, request)
        except BaseException:
            attempt.owner_finished(outcome="failed")
            raise
        else:
            attempt.owner_finished()
            return result


def test_queued_setup_retains_exact_owner_and_polling_reads_no_dependencies(
    operations, monkeypatch
):
    import harness.task_environment as setup

    operation, principal = locked_unit(operations)
    root = Path(operation.envelope["repository"])
    source = root / "work"
    for name in ("one", "two", "three"):
        target = source / name
        target.mkdir()
        for file in ("pyproject.toml", "uv.lock"):
            (target / file).write_bytes((source / file).read_bytes())
    ready = {unit: threading.Event() for unit in ("work/one", "work/two")}
    release = threading.Event()
    original_publish = setup.SetupReport.publish

    def publish(self, code):
        if self.unit in ready:
            ready[self.unit].set()
            assert release.wait(15), "test callback barrier expired"
        return original_publish(self, code)

    monkeypatch.setattr(setup.SetupReport, "publish", publish)
    current = state(operation)
    request = dict(
        operation="prepare", task_id="A", expected_revision=context_revision(current, "A")
    )
    try:
        for unit in ready:
            result = operation.dispatch(principal, "environment_operation", request | {"cwd": unit})
            assert result["handle"] and ready[unit].wait(5)
        queued = operation.dispatch(
            principal, "environment_operation", request | {"cwd": "work/three"}
        )
        assert queued["status"] == "Queued"
        owner = operation.policy_setup_pending[("A", "work/three")]
        assert owner.intent.operation == "environment_operation" and owner.intent.task_id == "A"
        release.set()
        for record in list(operation.processes.records.values()):
            record["thread"].join(5)
        seen = read_owners(monkeypatch)
        operation.tick()
        result = operation.setup_outcomes["work/three"]
        assert result["handle"]
        record = operation.processes.records[result["handle"]]
        assert record["policy"].attempt is owner
        assert seen and all(v.id == owner.intent.id for v in seen)
        assert ("A", "work/three") not in operation.policy_setup_pending
        original_unit = setup.task_unit
        modes = []

        def unit(*args, **kwargs):
            modes.append(kwargs.get("verify_outputs", True))
            return original_unit(*args, **kwargs)

        monkeypatch.setattr(setup, "task_unit", unit)
        operation.dispatch(principal, "poll_process", {"handle": result["handle"]})
        operation.dispatch(principal, "cancel_process", {"handle": result["handle"]})
        assert modes == [False, False]
        assert record["policy"].attempt is owner
        assert owner.legacy
        assert (
            operations[2]
            .conn.execute(
                "SELECT 1 FROM policy_legacy_admissions WHERE attempt_id=?", (owner.intent.id,)
            )
            .fetchone()
        ), "poll/cancel must not clear unqualified setup uncertainty"
    finally:
        release.set()


def test_unrelated_actual_attempt_cannot_be_relabelled_as_setup(operations, monkeypatch):
    import pytest

    import harness.task_environment as setup
    from harness.policy.context import scope
    from harness.policy.resources import prepare as reserve

    operation, principal = locked_unit(operations)
    controller = operations[2]
    current = state(operation)
    owner = reserve(
        operation, controller, principal, "al_command", {"arguments": ["status"]}, current
    )
    seen = read_owners(monkeypatch)
    try:
        with scope(owner), pytest.raises(ValueError, match="owning admitted attempt"):
            setup.prepare(
                operation,
                controller,
                principal,
                current,
                {
                    "operation": "prepare",
                    "cwd": "work",
                    "task_id": "A",
                    "expected_revision": context_revision(current, "A"),
                },
            )
        assert seen == []
        assert owner.intent.operation == "al_command" and owner.intent.task_id is None
    finally:
        owner.release_unstarted()
