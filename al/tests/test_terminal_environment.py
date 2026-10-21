"""Caller observations are hashes, complete, closed and confined to trusted terminal context."""

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def load():
    path = (
        Path(__file__).resolve().parents[1]
        / "codex/skills/al/scripts/al_environment_observation.py"
    )
    spec = importlib.util.spec_from_file_location("_tested_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile():
    return {
        "schema_version": 1,
        "kind": "terminal-caller-environment-v1",
        "values": {
            "UNSAFE_EXECUTION_FLAG": {
                "present": True,
                "digest": hashlib.sha256(b"actual caller input").hexdigest(),
            }
        },
    }


def test_hash_observation_never_restores_executable_environment(monkeypatch):
    module = load()
    value = profile()
    monkeypatch.setenv("UNSAFE_EXECUTION_FLAG", "controlled worker value")
    monkeypatch.setenv("WORKER_ONLY_VALUE", "fixed internal location")
    monkeypatch.setitem(
        sys.modules,
        "_al_validator_context",
        SimpleNamespace(
            request={
                "caller_environment": value,
                "run_pin_context": {"verified": "fixture unit context"},
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "_al_terminal_observation", SimpleNamespace(environment=value))
    assert module.ambient_environment() == value["values"]
    assert module.environment(["UNSAFE_EXECUTION_FLAG", "ABSENT_INPUT"])["ABSENT_INPUT"] == {
        "present": False,
        "digest": None,
    }
    assert (
        module.value_digest("UNSAFE_EXECUTION_FLAG")
        == value["values"]["UNSAFE_EXECUTION_FLAG"]["digest"]
    )
    assert module.value_digest("WORKER_ONLY_VALUE") == "unset"
    import os

    assert os.environ["UNSAFE_EXECUTION_FLAG"] == "controlled worker value"
    copy = module.ambient_environment()
    copy["UNSAFE_EXECUTION_FLAG"]["digest"] = "0" * 64
    assert module.ambient_environment() == value["values"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-parent",
        "missing-pin",
        "foreign-request",
        "unknown-field",
        "bad-digest",
        "false-presence",
    ],
)
def test_unbound_or_malformed_caller_observations_refuse(monkeypatch, mutation):
    module = load()
    value = profile()
    request = {"caller_environment": value, "run_pin_context": {"verified": "fixture unit context"}}
    if mutation == "missing-parent":
        monkeypatch.delitem(sys.modules, "_al_validator_context", raising=False)
    else:
        if mutation == "missing-pin":
            request["run_pin_context"] = None
        if mutation == "foreign-request":
            request["caller_environment"] = None
        monkeypatch.setitem(sys.modules, "_al_validator_context", SimpleNamespace(request=request))
    if mutation == "unknown-field":
        value["force"] = True
    if mutation == "bad-digest":
        value["values"]["UNSAFE_EXECUTION_FLAG"]["digest"] = "not-a-hash"
    if mutation == "false-presence":
        value["values"]["UNSAFE_EXECUTION_FLAG"]["present"] = False
    monkeypatch.setitem(sys.modules, "_al_terminal_observation", SimpleNamespace(environment=value))
    with pytest.raises(ValueError):
        module.ambient_environment()
