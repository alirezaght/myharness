# ruff: noqa: F811
"""Closed qualification metadata, real dispatch correlation and local audit failure semantics."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from harness.policy import control_qualification
from harness.policy.contracts import PolicyError, qualification_diagnostic, qualification_require
from harness.reliability import store
from harness.reliability.process_context import CURRENT
from tests.test_reliability_store import observed, rows  # noqa: F401

REF = "a" * 32
CANARY = "private-command-env-path-payload-canary"


def diagnostic(**overrides):
    return {
        "schema_version": 1,
        "cause": "launcher_bytes_mismatch",
        "qualification_ref": REF,
        **overrides,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"cause": "unknown"},
        {"cause": CANARY * 1000},
        {"qualification_ref": "x" * 32},
        {"qualification_ref": "a" * 33},
        {"observation_id": None},
        {"observation_id": CANARY},
        {"path": CANARY},
    ],
)
def test_invalid_diagnostics_never_render_sensitive_fields(change):
    with pytest.raises(ValueError) as failure:
        PolicyError("UNSUPPORTED_ENFORCEMENT_CAPABILITY", diagnostic=diagnostic(**change))
    assert str(failure.value) == "invalid qualification diagnostic"
    assert CANARY not in str(failure.value)


def test_legacy_errors_stay_code_only_and_metadata_is_copied():
    assert str(PolicyError("POLICY_DENIED")) == "POLICY_DENIED"
    value = diagnostic()
    assert qualification_diagnostic(value) == value
    error = PolicyError("UNSUPPORTED_ENFORCEMENT_CAPABILITY", diagnostic=value)
    value["cause"] = CANARY
    assert error.diagnostic == diagnostic() and len(str(error)) < 512
    with pytest.raises(ValueError):
        PolicyError("POLICY_DENIED", diagnostic=diagnostic())


def test_missing_observation_and_unexpected_exception_are_safe(monkeypatch):
    def fail(*args):
        raise RuntimeError(CANARY)

    monkeypatch.setattr(control_qualification, "_prepare", fail)
    errors = []
    for _ in range(2):
        with pytest.raises(PolicyError) as failure:
            control_qualification.prepare(None, [], {})
        errors.append(failure.value)
        assert failure.value.diagnostic["cause"] == "preparation_failed"
        assert "observation_id" not in failure.value.diagnostic
        assert CANARY not in str(failure.value)
    assert errors[0].diagnostic["qualification_ref"] != errors[1].diagnostic["qualification_ref"]


def test_actual_payload_bytes_mismatch_is_classified(tmp_path):
    path = tmp_path / "helper.py"
    path.write_text(CANARY)
    with pytest.raises(PolicyError) as failure:
        control_qualification._closure(tmp_path, {"helper.py": {"sha256": "0" * 64}}, REF)
    assert failure.value.diagnostic == diagnostic(cause="source_bytes_mismatch")
    assert CANARY not in str(failure.value) and str(path) not in str(failure.value)


def install_failure(monkeypatch, *, nested=None):
    import harness.policy.resources as resources

    def prepare(operation, controller, principal, name, arguments, state):
        if nested is not None and arguments.get("path") == "outer":
            nested(operation, principal)
        return control_qualification.prepare(operation, [], {})

    def fail(operation, args, state, qualification_ref):
        qualification_require(False, "source_inventory_mismatch", qualification_ref)

    monkeypatch.setattr(resources, "prepare", prepare)
    monkeypatch.setattr(control_qualification, "_prepare", fail)


def test_nested_and_consecutive_dispatch_correlation_is_call_local(observed, monkeypatch):
    operation, principal, _ = observed
    captured = []

    def denied(op, who, path):
        with pytest.raises(ValueError) as failure:
            op.dispatch(who, "read_file", {"path": path})
        captured.append(json.loads(str(failure.value).split(" ", 1)[1]))

    install_failure(monkeypatch, nested=lambda op, who: denied(op, who, "inner"))
    effects = []
    monkeypatch.setattr(operation, "_dispatch", lambda *args: effects.append(True))
    denied(operation, principal, "outer")
    denied(operation, principal, "later")
    assert len(captured) == 3
    assert len({d["qualification_ref"] for d in captured}) == 3
    assert len({d["observation_id"] for d in captured}) == 3
    assert CURRENT.get() is None and not effects
    for d in captured:
        observation = next(r for r in rows(observed) if r["observation_id"] == d["observation_id"])
        audit = next(
            r for r in rows(observed, "operation_audit") if r["id"] == observation["source_id"]
        )
        assert json.loads(audit["outcome"])["diagnostic"] == d
    assert not rows(observed, "policy_attempts")


def test_dispatch_without_observation_omits_it(observed, monkeypatch):
    operation, principal, _ = observed
    install_failure(monkeypatch)
    monkeypatch.setattr(store, "begin", lambda *args: None)
    with pytest.raises(ValueError) as failure:
        operation.dispatch(principal, "read_file", {"path": "."})
    assert "observation_id" not in json.loads(str(failure.value).split(" ", 1)[1])
    assert not rows(observed) and not rows(observed, "policy_attempts")


def test_terminal_diagnostic_consistency_and_correlation(observed):
    operation, principal, _ = observed
    oid = store.begin(operation, principal, "al_command")
    value = diagnostic(observation_id=oid)
    store.finish(operation, oid, "DISPATCH_REJECTED", diagnostic=value)
    store.finish(operation, oid, "DISPATCH_REJECTED", diagnostic=value)
    with pytest.raises(ValueError, match="conflicting terminal"):
        store.finish(
            operation, oid, "DISPATCH_REJECTED", diagnostic={**value, "qualification_ref": "b" * 32}
        )
    with pytest.raises(ValueError, match="correlation"):
        store.finish(operation, oid, "DISPATCH_REJECTED", diagnostic=diagnostic())
    assert json.loads(rows(observed, "operation_audit")[0]["outcome"])["diagnostic"] == value


def test_audit_failure_cannot_return_diagnostic_success(observed, monkeypatch):
    operation, principal, controller = observed
    install_failure(monkeypatch)
    connect = store.connect

    class FailedPublication:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE operation_audit SET outcome="):
                raise sqlite3.IntegrityError("private-storage-canary")
            return self.conn.execute(sql, parameters)

    monkeypatch.setattr(store, "connect", lambda path: FailedPublication(connect(path)))
    with pytest.raises(ValueError, match="Reliability recording unavailable") as failure:
        operation.dispatch(principal, "read_file", {"path": "."})
    assert "private-storage-canary" not in str(failure.value)
    assert rows(observed)[0]["state"] == "pending"
    assert rows(observed, "operation_audit")[0]["outcome"] is None
    assert not rows(observed, "policy_attempts")


def test_boundary_platform_failure_keeps_safe_cause(monkeypatch):
    from harness.policy import control_boundary

    monkeypatch.setattr(control_boundary.sys, "platform", "unsupported-fixture")
    with pytest.raises(PolicyError) as failure:
        control_boundary.prepare(None, CANARY, qualification_ref=REF)
    assert failure.value.diagnostic == diagnostic(cause="platform_unsupported")
    assert CANARY not in str(failure.value)


def test_boundary_probe_failure_never_exposes_probe_output(tmp_path, monkeypatch):
    from harness.policy import control_boundary

    monkeypatch.setattr(control_boundary.sys, "platform", "darwin")
    policy = tmp_path / "private-policy"
    policy.write_text("(version 1)")
    operation = SimpleNamespace(
        profile=lambda *args, **kwargs: SimpleNamespace(policy=policy),
        envelope={"repository": str(tmp_path)},
    )
    monkeypatch.setattr(control_boundary, "operation_popen", lambda *args, **kwargs: object())
    monkeypatch.setattr(control_boundary, "capture", lambda *args, **kwargs: (CANARY.encode(), 1))
    with pytest.raises(PolicyError) as failure:
        control_boundary.prepare(operation, CANARY, qualification_ref=REF)
    assert failure.value.diagnostic == diagnostic(cause="boundary_probe_failed")
    assert CANARY not in str(failure.value) and str(policy) not in str(failure.value)


def test_dispatch_replaces_producer_observation_with_actual_one(observed, monkeypatch):
    import harness.policy.resources as resources

    operation, principal, _ = observed

    def fail(*args):
        raise PolicyError(
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY", diagnostic=diagnostic(observation_id="f" * 32)
        )

    monkeypatch.setattr(resources, "prepare", fail)
    with pytest.raises(ValueError) as failure:
        operation.dispatch(principal, "read_file", {"path": "."})
    value = json.loads(str(failure.value).split(" ", 1)[1])
    assert value["observation_id"] == rows(observed)[0]["observation_id"]
    assert value["observation_id"] != "f" * 32


def test_store_refuses_invalid_metadata_without_terminal_change(observed):
    operation, principal, _ = observed
    oid = store.begin(operation, principal, "al_command")
    with pytest.raises(ValueError, match="invalid qualification diagnostic"):
        store.finish(
            operation,
            oid,
            "DISPATCH_REJECTED",
            diagnostic=diagnostic(observation_id=oid, cause=CANARY),
        )
    assert rows(observed)[0]["state"] == "pending"
    assert rows(observed, "operation_audit")[0]["outcome"] is None


def test_unexpected_exception_is_safe_through_dispatch_and_audit(observed, monkeypatch):
    operation, principal, _ = observed
    install_failure(monkeypatch)

    def fail(*args):
        raise RuntimeError(CANARY)

    monkeypatch.setattr(control_qualification, "_prepare", fail)
    with pytest.raises(ValueError) as failure:
        operation.dispatch(principal, "read_file", {"path": "."})
    value = json.loads(str(failure.value).split(" ", 1)[1])
    assert value["cause"] == "preparation_failed"
    assert CANARY not in str(failure.value)
    outcome = rows(observed, "operation_audit")[0]["outcome"]
    assert json.loads(outcome)["diagnostic"] == value and CANARY not in outcome
    assert not rows(observed, "policy_attempts")


def test_revalidation_read_failure_cannot_expose_private_path(tmp_path):
    qualification = control_qualification.Qualification.__new__(control_qualification.Qualification)
    qualification.probe_ref = REF
    qualification.states = ({"path": str(tmp_path / CANARY)},)
    qualification.verified = False
    with pytest.raises(PolicyError) as failure:
        qualification.validate()
    assert failure.value.diagnostic == diagnostic(cause="preparation_failed")
    assert CANARY not in str(failure.value) and str(tmp_path) not in str(failure.value)
    assert qualification.verified is False
