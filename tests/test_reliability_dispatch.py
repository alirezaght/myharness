# ruff: noqa: F811
"""Public dispatch security, privacy and result semantics, with genuine registry identities."""

import hashlib
import json
import subprocess

import pytest

from harness.reliability import store
from tests.test_reliability_store import observed, rows  # noqa: F401

CANARIES = ["argument-private-canary-462", "result-private-canary-981", "error-private-canary-317"]


def test_real_dispatch_denial_does_not_apply_patch(observed, tmp_path):
    operation, principal, _ = observed
    target = tmp_path / "forbidden"
    with pytest.raises(ValueError):
        operation.dispatch(
            principal,
            "apply_patch",
            {
                "task_id": "foreign-task",
                "expected_revision": "foreign-revision",
                "patch": CANARIES[0],
                "target": str(target),
            },
        )
    assert not target.exists()
    assert rows(observed)[0]["code"] == "DISPATCH_REJECTED"
    assert rows(observed, "reliability_incidents")[0]["category"] == "operation_denied"


def test_real_authority_denial_is_distinct(observed):
    operation, principal, controller = observed
    controller.conn.execute(
        "UPDATE executions SET status='Completed' WHERE id=?", (operation.execution_id,)
    )
    with pytest.raises(ValueError, match="read-only"):
        operation.dispatch(principal, "apply_patch", {"patch": CANARIES[0]})
    assert rows(observed)[0]["code"] == "AUTHORITY_REJECTED"


@pytest.mark.parametrize(
    ("error", "code", "public_type"),
    [
        (OSError(CANARIES[2]), "DISPATCH_OS_ERROR", ValueError),
        (subprocess.SubprocessError(CANARIES[2]), "DISPATCH_PROCESS_ERROR", ValueError),
        (LookupError(CANARIES[2]), "DISPATCH_REJECTED", ValueError),
        (RuntimeError(CANARIES[2]), "DISPATCH_INTERNAL_ERROR", RuntimeError),
    ],
)
def test_exception_classification_preserves_public_behavior(
    observed, monkeypatch, error, code, public_type
):
    operation, principal, _ = observed

    def effect(*args):
        raise error

    monkeypatch.setattr(operation, "_dispatch", effect)
    with pytest.raises(public_type):
        operation.dispatch(principal, "read_file", {"path": CANARIES[0]})
    assert rows(observed)[0]["code"] == code
    assert_private(observed)


def assert_private(observed):
    persisted = json.dumps(
        [
            rows(observed, table)
            for table in (
                "reliability_observations",
                "reliability_incidents",
                "operation_audit",
            )
        ]
    )
    for secret in CANARIES:
        assert secret not in persisted
        assert hashlib.sha256(secret.encode()).hexdigest() not in persisted


@pytest.mark.parametrize(
    "returned",
    [
        {"error": CANARIES[1], "returncode": 1},
        {"handle": CANARIES[1], "status": "Running"},
        {"status": "SetupPending", "detail": CANARIES[1]},
    ],
)
def test_returned_application_errors_and_handles_are_not_incidents(observed, monkeypatch, returned):
    operation, principal, _ = observed
    monkeypatch.setattr(operation, "_dispatch", lambda *args: returned)
    assert operation.dispatch(principal, "read_file", {"path": CANARIES[0]}) == returned
    assert rows(observed)[0]["state"] == "succeeded"
    assert not rows(observed, "reliability_incidents")
    assert_private(observed)


@pytest.mark.parametrize(
    "change",
    [
        {"thread_id": "foreign-private-canary"},
        {"role": "al-engineer"},
        {"policy": "foreign-private-canary"},
        {"binding": {}},
    ],
)
def test_foreign_or_malformed_identity_not_recorded_as_verified(observed, change):
    operation, principal, _ = observed
    assert store.begin(operation, principal | change, "unknown") is None
    assert not rows(observed) and not rows(observed, "operation_audit")


def test_real_read_returns_content_without_persisting_it(observed):
    from pathlib import Path

    operation, principal, _ = observed
    (Path(operation.envelope["repository"]) / "public.txt").write_text(CANARIES[1])
    result = operation.dispatch(principal, "read_file", {"path": "public.txt"})
    assert CANARIES[1] in result["text"]
    assert rows(observed)[0]["state"] == "succeeded"
    assert_private(observed)


def test_assignment_wait_remains_explicitly_unobserved(observed, monkeypatch):
    operation, principal, _ = observed
    monkeypatch.setattr(operation, "await_assignment", lambda *args: {"waiting": True})
    assert operation.dispatch(principal, "await_assignment", {}) == {"waiting": True}
    assert not rows(observed) and not rows(observed, "operation_audit")


def test_unknown_tool_is_closed_and_each_dispatch_has_new_identity(observed):
    operation, principal, _ = observed
    for _ in range(2):
        with pytest.raises(ValueError, match="unknown operation"):
            operation.dispatch(principal, CANARIES[0], {})
    observations = rows(observed)
    assert len(observations) == 2 and {r["tool"] for r in observations} == {"unknown"}
    assert observations[0]["source_id"] != observations[1]["source_id"]
    assert_private(observed)
