"""Caller observations are hashes, complete, closed and confined to trusted terminal context."""

import hashlib
import importlib.util
import sys
from pathlib import Path
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


def test_query_uses_system_git_even_when_caller_path_prefers_other_tooling(tmp_path, monkeypatch):
    import os
    import subprocess

    from al.installer.terminal_source import query_environment

    tools = tmp_path / "tools"
    tools.mkdir()
    marker = tmp_path / "caller-git-ran"
    executable = tools / "git"
    executable.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\nexit 99\n")
    executable.chmod(0o755)
    caller_path = str(tools) + os.pathsep + os.defpath
    monkeypatch.setenv("PATH", caller_path)
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "caller-controlled")

    environment = query_environment()
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    driver = """
import os, subprocess, sys
sys.path.insert(0, sys.argv[1])
from al.installer.validator_readonly import Readonly
reader = Readonly()
reader.start()
result = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
assert result.returncode == 0, result.stderr
print(result.stdout.strip())
assert os.environ['PATH'] == sys.argv[2]
with subprocess.Popen(['git', 'rev-parse', '--show-toplevel'], -1, None,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
    out, err = child.communicate(timeout=5)
    assert child.returncode == 0, err
    assert out.strip().decode() == result.stdout.strip()
try:
    subprocess.Popen(['git', 'status', '--porcelain'], -1, sys.argv[3])
except PermissionError:
    pass
else:
    raise AssertionError('explicit positional foreign executable permitted')
for args, kwargs in [
    (['git', 'status', '--porcelain'], {'executable': sys.argv[3]}),
    (['git', 'config', 'test.unsafe', 'true'], {}),
    (['git', 'status', '--porcelain'], {'env': dict(os.environ, GIT_EXTERNAL_DIFF='unsafe')}),
]:
    try:
        subprocess.run(args, capture_output=True, **kwargs)
    except PermissionError:
        pass
    else:
        raise AssertionError('unapproved execution permitted')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            driver,
            str(Path(__file__).resolve().parents[2]),
            caller_path,
            str(executable),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(repository.resolve())
    assert not marker.exists()
    assert environment["PATH"] == caller_path
    assert "GIT_EXTERNAL_DIFF" not in environment
    assert os.environ["PATH"] == caller_path
