"""Preparation reconstructs actual planned inputs and cannot bless ambient drift."""

import copy
import json
import sys

import al_store
import al_verification_prepare as P
import pytest
from batch13_helpers import start
from hook_regression_helpers import invoke


def planned(cli, repo):
    start(cli, repo)
    (repo / "verification-input.json").write_text('{"valid":true}\n')
    invoke(
        cli,
        "evidence",
        "plan",
        "--area",
        "manifest-read",
        "--kind",
        "check",
        "--tier",
        "focused",
        "--scope",
        ".",
        "--",
        sys.executable,
        "-I",
        "-m",
        "json.tool",
        "verification-input.json",
    )
    return json.loads(cli("show")[1])


def test_public_preparation_is_run_bound_and_executes_no_check(cli, repo):
    before = planned(cli, repo)
    prepared = json.loads(invoke(cli, "verification", "prepare", "--area", "manifest-read")[1])
    assert prepared["ready"] and prepared["blockers"] == []
    after = json.loads(cli("show")[1])
    ref = after["verification_preparations"][-1]
    assert before["evidence"]["entries"] == after["evidence"]["entries"]
    assert P.validate_preparation(after, ref) == []
    data = json.loads(open(ref["stored_path"]).read())
    assert data["run_id"] == after["run_id"] and data["session_id"] == after["session_id"]
    assert data["command_profiles"][0]["id"] == "isolated-json-tool-v1"
    assert data["command_profiles"][0]["stage_independent"]
    assert not data["blockers"] and data["setup_recipe"]["kind"] == "isolated-stdlib-reader-v1"
    assert data["environment_policy"]["inputs"]
    assert all(
        set(v) == {"present", "digest"} for v in data["environment_policy"]["inputs"].values()
    )


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "mode",
        "membership",
        "environment",
        "empty-environment",
        "plan",
        "run",
        "task",
        "cache-policy",
    ],
)
def test_preparation_currency_refuses_each_actual_dependency(cli, repo, monkeypatch, change):
    st = planned(cli, repo)
    result = json.loads(invoke(cli, "verification", "prepare", "--area", "manifest-read")[1])
    ref = result["artifact"]
    st = json.loads(cli("show")[1])
    if change == "source":
        (repo / "verification-input.json").write_text('{"valid":false}\n')
    elif change == "mode":
        (repo / "verification-input.json").chmod(0o600)
    elif change == "membership":
        (repo / "verification-input.json").rename(repo / "replacement-input.json")
    elif change == "environment":
        monkeypatch.setenv("AL_PREPARATION_NEW_INPUT", "secret-not-recorded")
    elif change == "empty-environment":
        monkeypatch.setenv("AL_PREPARATION_NEW_INPUT", "")
    elif change == "plan":
        st["evidence"]["plan"]["manifest-read"]["argv"][-1] = "other.json"
    elif change == "run":
        st["run_id"] = "foreign-run"
    elif change == "task":
        import al_artifacts as A

        file = repo.parent / "preparation-amendment.json"
        file.write_text(
            json.dumps(
                {
                    "id": "preparation-change",
                    "text": "Require changed preparation inputs and retained evidence provenance.",
                    "source": "Explicit disposable user amendment.",
                    "affected_criteria": A.read_json(st["task_package"])["criteria"],
                }
            )
        )
        invoke(cli, "task", "amend", "--file", str(file))
        st = json.loads(cli("show")[1])
    else:
        from al_artifacts import snapshot_bytes

        data = json.loads(open(ref["stored_path"]).read())
        data["cache_policy"]["enabled"] = "yes"
        ref = snapshot_bytes(
            st["session_id"], json.dumps(data).encode(), ref["kind"], "forged policy"
        )
    assert P.validate_preparation(st, ref), change


def test_unknown_identifier_and_shell_text_cannot_be_preparation_authority(cli, repo):
    planned(cli, repo)
    path = al_store.state_path(al_store.session_id(None))
    before = path.read_bytes()
    invoke(cli, "verification", "prepare", "--area", "unknown", ok=False)
    assert path.read_bytes() == before
    marker = repo / "must-not-execute"
    invoke(
        cli,
        "evidence",
        "plan",
        "--area",
        "opaque",
        "--kind",
        "check",
        "--tier",
        "focused",
        "--scope",
        ".",
        "--",
        sys.executable,
        "-c",
        "from pathlib import Path;Path(" + repr(str(marker)) + ").touch()",
    )
    result = json.loads(invoke(cli, "verification", "prepare", "--area", "opaque", ok=False)[1])
    assert not result["ready"] and any("unsupported" in gap for gap in result["blockers"])
    assert not marker.exists()


def test_preparation_prerequisites_are_execution_proof_not_tool_availability(cli, repo):
    st = planned(cli, repo)
    spec = st["evidence"]["plan"]["manifest-read"]
    spec["needs"] = ["unexecuted-setup"]
    st["evidence"]["plan"]["unexecuted-setup"] = copy.deepcopy(spec)
    result = P.prepare(st, area="manifest-read")
    assert any("VALID PASS" in gap for gap in result["blockers"])
    assert result["prerequisite_receipts"][0]["evidence"] is None


def test_environment_absence_empty_and_value_have_distinct_nonsecret_bindings(monkeypatch):
    from al_verification_inputs import environment

    name = "AL_EXPLICIT_PREPARATION_TEST_INPUT"
    monkeypatch.delenv(name, raising=False)
    absent = environment([name])
    monkeypatch.setenv(name, "")
    empty = environment([name])
    monkeypatch.setenv(name, "sensitive")
    value = environment([name])
    assert absent != empty != value and not absent[name]["present"] and empty[name]["present"]
    assert "sensitive" not in json.dumps(value)


def test_gate_preparation_binds_actual_runner_manifest_stage_and_resources(
    cli, repo, tmp_path, monkeypatch
):
    import al_gates as G
    import al_runtime_resources as R
    from batch13_helpers import manifest

    start(cli, repo)
    manifest(
        cli, tmp_path, [sys.executable, "-I", "-m", "json.tool", "svc/tests/fixtures/rows.json"]
    )
    st = json.loads(cli("show")[1])
    m = G.manifest(st)
    gate = next(g for g in m["gates"] if g["category"] == "lint-imports" and g["argv"])
    result = json.loads(
        invoke(cli, "verification", "prepare", "--gate", gate["id"], "--stage", "base")[1]
    )
    assert result["ready"]
    data = P.prepare(st, gate=gate["id"], stage="base")
    assert data["destination"]["manifest_ref"] == st["gate_manifests"][-1]
    assert data["command_profiles"][0]["id"] == "bundled-module-ban-v1"
    assert data["destination"]["execution_signature"] == gate["execution_signature"]
    target = dict(data["destination"], stage="candidate")
    assert P.validate_preparation(st, result["artifact"], target)
    original = R.resource_bytes
    monkeypatch.setattr(
        R,
        "resource_bytes",
        lambda name: (
            original(name) + b"\n" if name == "al_architecture_check.py" else original(name)
        ),
    )
    assert P.validate_preparation(st, result["artifact"])


def test_plain_available_tool_does_not_prove_locked_setup_or_correct_configuration(cli, repo):
    st = planned(cli, repo)
    st["evidence"]["plan"]["manifest-read"]["argv"] = ["uv", "run", "pytest", "-q"]
    result = P.prepare(st, area="manifest-read")
    assert any("setup chain" in gap or "unavailable" in gap for gap in result["blockers"])
    assert not result["setup_recipe"]
