"""Real isolated public run dispatch through a disposable managed launcher."""

import json
import sys

import pytest

from al.tests.runtime_run_helpers import invoke, launched, public_run, run_record, start


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_public_new_run_isolated_start_survives_installation_change(launched, platform, tmp_path):
    source, home, repo, _, environment = launched
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    marker = tmp_path / "startup-executed"
    code = f'from pathlib import Path\nPath({str(marker)!r}).write_text("escaped")\n'
    (hostile / "sitecustomize.py").write_text(code)
    (hostile / "al_state.py").write_text(code + 'raise RuntimeError("foreign")\n')
    (repo / "al_state.py").write_text(code)
    hostile_env = dict(environment, PYTHONPATH=str(hostile))
    started = public_run(
        launched, platform, "start", "--spec", "Observe the pinned runtime.", env=hostile_env
    )
    assert started.returncode == 0, (
        started.returncode,
        started.stdout[-3000:],
        started.stderr[-3000:],
    )
    assert not marker.exists()
    state_path = repo / ("." + platform) / "al-runtime/pin-flow.json"
    state = json.loads(state_path.read_bytes())
    records = list((home / "installer/al-run-pins/runs").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_bytes())
    assert record["status"] == "ACTIVE"
    assert state["run_id"] == record["run_id"]
    assert state["runtime_origin"]["pin"] == record["pin"]
    before = state_path.read_bytes()
    # The retained CLI and runtime remain independent from the installation that
    # supplied them. Neither this replacement nor target import paths are used.
    (source / platform / "skills/al/scripts/al_state.py").write_text(
        code + 'raise RuntimeError("changed")\n'
    )
    shown = public_run(launched, platform, "show", "--json", env=hostile_env)
    assert shown.returncode == 0, (shown.returncode, shown.stdout[-3000:], shown.stderr[-3000:])
    assert json.loads(shown.stdout)["run_id"] == state["run_id"]
    assert state_path.read_bytes() == before
    assert not marker.exists()


def test_missing_binding_never_selects_candidate_for_old_state(launched):
    _, _, repo, _, _ = launched
    runtime = repo / ".codex/al-runtime"
    runtime.mkdir(parents=True)
    state = runtime / "pin-flow.json"
    state.write_text('{"run_id":"old"}')
    before = state.read_bytes()
    result = public_run(launched, "codex", "show", "--json")
    assert result.returncode != 0
    assert "original issuer" in result.stdout
    assert state.read_bytes() == before


def test_unisolated_source_entry_cannot_start_pinned_runtime(launched):
    source, _, repo, _, env = launched
    result = invoke(
        [
            sys.executable,
            "-B",
            source / "alctl.py",
            "run",
            "--repository",
            repo,
            "--platform",
            "codex",
            "--session",
            "unsafe",
            "--",
            "start",
            "--spec",
            "test",
        ],
        repo,
        env,
    )
    assert result.returncode != 0
    assert "isolated managed launcher" in result.stdout
    assert not (repo / ".codex/al-runtime/unsafe.json").exists()


@pytest.mark.parametrize(
    "mutation", ["origin-removed", "origin-downgrade", "foreign-run", "binding-missing"]
)
def test_new_origin_cannot_downgrade_into_legacy_dispatch(launched, mutation):
    state_path = start(launched)
    path, record = run_record(launched)
    state = json.loads(state_path.read_bytes())
    if mutation == "origin-removed":
        del state["runtime_origin"]
    elif mutation == "origin-downgrade":
        state["runtime_origin"]["schema_version"] = 0
    elif mutation == "foreign-run":
        state["run_id"] = "00000000"
    else:
        path.unlink()
    if mutation != "binding-missing":
        state_path.write_text(json.dumps(state))
    before = state_path.read_bytes()
    result = public_run(launched, "codex", "show", "--json")
    assert result.returncode != 0
    assert state_path.read_bytes() == before


def test_hostile_home_does_not_select_a_different_pin_registry(launched, tmp_path):
    state_path = start(launched)
    _, _, _, _, env = launched
    hostile_home = tmp_path / "target-home"
    hostile_home.mkdir()
    before = state_path.read_bytes()
    record_path, record = run_record(launched)
    binding_before = record_path.read_bytes()
    result = public_run(launched, "codex", "show", "--json", env=dict(env, HOME=str(hostile_home)))
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    assert json.loads(result.stdout)["run_id"] == record["run_id"]
    assert state_path.read_bytes() == before
    assert record_path.read_bytes() == binding_before
    assert not list(hostile_home.iterdir())


def test_tampered_pinned_module_never_executes(launched, tmp_path):
    state_path = start(launched)
    _, home, _, _, _ = launched
    _, record = run_record(launched)
    pin = home / "installer/al-run-pins/pins" / record["pin"]["pin_identity"]
    module = pin / "payload/codex/skills/al/scripts/al_state.py"
    marker = tmp_path / "tampered-module-executed"
    module.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    before = state_path.read_bytes()
    result = public_run(launched, "codex", "show", "--json")
    assert result.returncode != 0
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_failed_start_retries_the_same_reserved_identity(launched):
    _, _, repo, _, _ = launched
    failed = public_run(launched, "codex", "start", "--review-mode", "unknown", "--spec", "test")
    assert failed.returncode != 0
    assert not (repo / ".codex/al-runtime/pin-flow.json").exists()
    path, reserved = run_record(launched)
    assert reserved["status"] == "RESERVED"
    state_path = start(launched)
    active = json.loads(path.read_bytes())
    assert reserved["initialization"] is None
    assert active["initialization"] is not None
    assert active == dict(reserved, status="ACTIVE", initialization=active["initialization"])
    assert json.loads(state_path.read_bytes())["run_id"] == reserved["run_id"]


def test_exact_state_publication_recovers_binding_without_reexecuting_start(launched):
    state_path = start(launched)
    path, active = run_record(launched)
    path.write_text(json.dumps(dict(active, status="RESERVED")))
    before = state_path.read_bytes()
    result = public_run(
        launched, "codex", "start", "--spec", "A different task must not overwrite."
    )
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    assert "recovered exact published run" in result.stdout
    assert json.loads(path.read_bytes()) == active
    assert state_path.read_bytes() == before


def test_incomplete_pointer_cannot_be_reported_as_published_start(launched):
    state_path = start(launched)
    path, active = run_record(launched)
    path.write_text(json.dumps(dict(active, status="RESERVED")))
    (state_path.parent / "current").unlink()
    before = state_path.read_bytes()
    binding_before = path.read_bytes()
    result = public_run(launched, "codex", "start", "--spec", "test")
    assert result.returncode != 0
    assert state_path.read_bytes() == before
    assert path.read_bytes() == binding_before
    assert not (state_path.parent / "current").exists()


def test_foreign_native_project_cannot_redirect_new_state(launched, tmp_path):
    _, _, repo, _, env = launched
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    result = public_run(
        launched,
        "claude",
        "start",
        "--spec",
        "test",
        env=dict(env, CLAUDE_PROJECT_DIR=str(foreign)),
    )
    assert result.returncode != 0
    assert "project directory differs" in result.stdout
    assert not (foreign / ".claude").exists()
    assert not (repo / ".claude/al-runtime/pin-flow.json").exists()
