"""Actual old bootstrap shape upgrades through owned staging, not source-only calls."""

import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from al.installer import journal as J
from al.installer import workspace_cli as C
from al.installer import workspace_resources as H
from al.installer.engine import D, T
from al.installer.registry import canonical, digest, namespace, write_json
from al.installer.targets import resolve_target
from al.installer.validator_origin import capture, closure_paths
from al.tests.conftest import copy_al_source


def legacy_snapshot(source, home, state):
    # The real old bootstrap format, with data-only receipt and exact managed launcher.
    files = {
        path.relative_to(source).as_posix(): capture(path)[1] for path in closure_paths(source)
    }
    key = digest(canonical(files))
    snapshot = namespace(state) / "cli/snapshots" / key
    for name, row in files.items():
        D.atomic(snapshot / name, (source / name).read_bytes(), row["mode"])
    receipt = {
        "schema_version": 1,
        "kind": "development",
        "content_digest": key,
        "files": files,
        "source_root": str(source),
        "source_commit_if_available": None,
        "dirty": True,
    }
    write_json(snapshot / "receipt.json", receipt)
    destination = home / ".local/bin/alctl"
    D.atomic(destination, C.legacy_launcher(snapshot), 0o755)
    owner = {
        "schema_version": 1,
        "destination": str(destination),
        "state": T.state(destination),
        "snapshot": str(snapshot),
        "receipt": receipt,
    }
    write_json(C.owner_path(destination, state), owner)
    return destination, owner


@pytest.fixture
def old_cli(tmp_path):
    source = copy_al_source(tmp_path / "source/al")
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    destination, owner = legacy_snapshot(source, home, state)
    return source, home, state, destination, owner


def invoke(destination, home, state, cwd, command):
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    env.pop("AL_RUNTIME_DIR", None)
    env.pop("AL_DISTRIBUTION_STATE", None)
    argv = [str(destination), *command]
    if command[0] != "--help":
        argv += ["--state-root", str(state), "--json"]
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    return result


def test_actual_old_launcher_upgrade_has_backups_and_neutral_public_compatibility(
    old_cli, tmp_path
):
    source, home, state, destination, owner = old_cli
    before = destination.read_bytes()
    before_owner = C.owner_path(destination, state).read_bytes()
    assert C.existing_owner(destination, state)["profile"] == "legacy-managed-bootstrap-v1"
    invoke(destination, home, state, tmp_path, ["--help"])
    prepared = C.prepare_cli(source, home, state, tmp_path / "preparation")
    assert (
        destination.read_bytes() == before
        and C.owner_path(destination, state).read_bytes() == before_owner
    )
    directory, path, journal = J.allocate(state, "a" * 64, "workspace-component-test", None)
    actions = [
        J.action(directory, destination, Path(prepared["launcher"]["source"]).read_bytes(), 0o755),
        J.action(directory, prepared["owner_path"], prepared["owner_bytes"], 0o600),
    ]
    journal["ancillary_actions"] = actions
    for action in actions:
        J.execute_ancillary(action, journal, path)
    J.validate_backups(journal)
    J.checkpoint(journal, path, "COMPLETE")
    assert Path(actions[0]["backup"]).read_bytes() == before
    assert Path(actions[1]["backup"]).read_bytes() == before_owner
    assert C.existing_owner(destination, state)["profile"] == "protected-validator-snapshot-v1"
    source.rename(source.with_name("source-no-longer-installed"))
    for command in (["--help"], ["list"], ["doctor"]):
        result = invoke(destination, home, state, tmp_path, command)
        if command[0] == "list":
            assert json.loads(result.stdout)["installations"] == []
        if command[0] == "doctor":
            assert json.loads(result.stdout)["ok"] is True


@pytest.mark.parametrize(
    "mutation", ["launcher", "owner-mode", "snapshot-member", "receipt", "unknown-owner"]
)
def test_legacy_drift_never_becomes_upgrade_or_adoption_authority(old_cli, mutation):
    _, _, state, destination, owner = old_cli
    if mutation == "launcher":
        destination.write_bytes(destination.read_bytes() + b"\n")
    elif mutation == "owner-mode":
        C.owner_path(destination, state).chmod(0o644)
    elif mutation == "snapshot-member":
        (Path(owner["snapshot"]) / "unreviewed.py").write_text("pass\n")
    elif mutation == "receipt":
        value = copy.deepcopy(owner["receipt"])
        value["dirty"] = False
        write_json(Path(owner["snapshot"]) / "receipt.json", value)
    else:
        owner["ignore_drift"] = True
        write_json(C.owner_path(destination, state), owner)
    with pytest.raises(ValueError):
        C.existing_owner(destination, state)


def test_global_harness_guidance_preserves_approval_text_and_requires_collision_adoption(tmp_path):
    root = Path(__file__).resolve().parents[2]
    original = (root / ".claude/commands/harness.md").read_bytes()
    home = tmp_path / "home"
    home.mkdir()
    resources = H.global_resources(
        {".claude/commands/harness.md": original}, home, tmp_path / "environment"
    )
    converted = resources[str(home / ".claude/commands/harness.md")][0]
    assert H.host_text(original) == converted
    assert b"uv run" not in converted and b"harness-redesign" in converted
    assert b"The developer alone may press Approve & Implement" in converted
    assert b"Never run it on your own initiative" in converted
    target = resolve_target(home=home)
    assert len(H.prepare_resources(resources, target, {}, {})) == 4
    collision = home / ".codex/skills/harness/SKILL.md"
    collision.parent.mkdir(parents=True)
    collision.write_bytes(resources[str(collision)][0])
    before = collision.read_bytes()
    with pytest.raises(ValueError, match="adoption required"):
        H.prepare_resources(resources, target, {}, {})
    adoption = {
        "resources": {
            str(collision): {
                "reviewed": True,
                "reason": "Exact disposable fixture adoption",
                "state": T.state(collision),
            }
        }
    }
    planned = H.prepare_resources(resources, target, {}, adoption)
    assert planned[str(collision)]["before"] == T.state(collision)
    assert collision.read_bytes() == before
