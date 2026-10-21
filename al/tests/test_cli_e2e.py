"""Public CLI coverage using disposable tagged sources and installations."""
from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

from al.installer.engine import D
from al.installer.manifest import build_manifest
from al.installer.source import resolve_release
from al.installer.targets import package_path, resolve_target, roles_path
from al.tests.conftest import run_alctl
from al.tests.test_policy import session as make_session
from al.tests.test_manifest import git_test, make_source, tag_second


def _install(workspace, *extra):
    return run_alctl(
        "install",
        "--global",
        "--home",
        workspace["home"],
        "--state-root",
        workspace["state"],
        "--source",
        workspace["source"],
        "--version",
        "1.0.0",
        "--platform",
        "codex,claude",
        *extra,
        cwd=workspace["root"],
    )


def test_global_cli_installs_updates_and_rolls_back_both_platforms(installer_workspace):
    workspace = installer_workspace
    installed = _install(workspace)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert installed.data["status"] == "updated"
    assert (workspace["home"] / ".codex/skills/al/SKILL.md").is_file()
    assert (workspace["home"] / ".claude/skills/al/SKILL.md").is_file()

    listed = run_alctl("list", "--state-root", workspace["state"], cwd=workspace["root"])
    assert listed.returncode == 0 and listed.data["installations"][0]["version"] == "1.0.0"

    tag_second(workspace["source"], extra_role=True)
    updated = run_alctl(
        "update",
        "--global",
        "--home",
        workspace["home"],
        "--state-root",
        workspace["state"],
        "--version",
        "1.1.0",
        "--policy",
        "follow",
        cwd=workspace["root"],
    )
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert updated.data["status"] == "updated"
    assert updated.data["version"] == "1.1.0"
    assert (workspace["home"] / ".codex/agents/al-extra.toml").is_file()

    rolled_back = run_alctl(
        "rollback",
        "--previous",
        "--global",
        "--home",
        workspace["home"],
        "--state-root",
        workspace["state"],
        cwd=workspace["root"],
    )
    assert rolled_back.returncode == 0, rolled_back.stdout + rolled_back.stderr
    assert rolled_back.data["status"] == "updated"
    assert rolled_back.data["version"] == "1.0.0"
    assert not (workspace["home"] / ".codex/agents/al-extra.toml").exists()


def test_project_pin_and_update_all_do_not_scan_or_override_pins(installer_workspace):
    workspace = installer_workspace
    repo = workspace["root"] / "project"
    repo.mkdir()
    git_test(repo, "init", "-q")
    git_test(repo, "commit", "--allow-empty", "-qm", "project")
    result = run_alctl(
        "install",
        "--repo",
        repo,
        "--state-root",
        workspace["state"],
        "--source",
        workspace["source"],
        "--version",
        "1.0.0",
        "--platform",
        "codex,claude",
        cwd=workspace["root"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((repo / "al.lock.json").read_text())["policy"] == "pinned"

    tag_second(workspace["source"])
    batch = run_alctl("update", "--all", "--state-root", workspace["state"], cwd=workspace["root"])
    assert batch.returncode == 0, batch.stdout + batch.stderr
    assert [row["status"] for row in batch.data] == ["pinned"]
    assert json.loads((repo / "al.lock.json").read_text())["version"] == "1.0.0"

    # A selected target may explicitly move its pin; this changes lock metadata too.
    selected = run_alctl(
        "update",
        "--repo",
        repo,
        "--state-root",
        workspace["state"],
        "--version",
        "1.1.0",
        cwd=workspace["root"],
    )
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert selected.data["version"] == "1.1.0"
    assert json.loads((repo / "al.lock.json").read_text())["version"] == "1.1.0"


def test_cli_reports_drift_without_overwriting_and_active_run_as_deferred(installer_workspace):
    workspace = installer_workspace
    installed = _install(workspace, "--policy", "follow")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    package = workspace["home"] / ".codex/skills/al/SKILL.md"
    package.write_text("user edit\n")
    tag_second(workspace["source"])
    drift = run_alctl(
        "update",
        "--global",
        "--home",
        workspace["home"],
        "--state-root",
        workspace["state"],
        "--version",
        "1.1.0",
        cwd=workspace["root"],
    )
    assert drift.returncode == 1
    assert drift.data["status"] == "failed" and "drift" in drift.data["error"]
    assert package.read_text() == "user edit\n"

    # Activity state is checked before drift planning; use a separate clean target.
    other_home = workspace["root"] / "other-home"
    other_home.mkdir()
    clean = run_alctl(
        "install",
        "--global",
        "--home",
        other_home,
        "--state-root",
        workspace["root"] / "other-state",
        "--source",
        workspace["source"],
        "--version",
        "1.0.0",
        "--platform",
        "codex",
        "--policy",
        "follow",
        cwd=workspace["root"],
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    session = other_home / ".codex/al-runtime/blocked.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    make_session(session, status="WAITING_FOR_USER", phase="PLANNING")
    deferred = run_alctl(
        "update",
        "--global",
        "--home",
        other_home,
        "--state-root",
        workspace["root"] / "other-state",
        "--version",
        "1.1.0",
        "--runtime-root",
        session.parent,
        cwd=workspace["root"],
    )
    assert deferred.returncode == 3
    assert deferred.data["status"] == "deferred"
    assert "blocked.json" in deferred.data["error"]
    assert "WAITING_FOR_USER/PLANNING" in deferred.data["error"]


def test_update_all_aggregates_failure_and_keeps_other_result_visible(installer_workspace):
    workspace = installer_workspace
    first_home = workspace["root"] / "first"
    second_home = workspace["root"] / "second"
    first_home.mkdir()
    second_home.mkdir()
    for home in (first_home, second_home):
        result = run_alctl(
            "install",
            "--global",
            "--home",
            home,
            "--state-root",
            workspace["state"],
            "--source",
            workspace["source"],
            "--version",
            "1.0.0",
            "--platform",
            "codex",
            "--policy",
            "follow",
            cwd=workspace["root"],
        )
        assert result.returncode == 0, result.stdout + result.stderr
    tag_second(workspace["source"])
    edited = first_home / ".codex/skills/al/SKILL.md"
    edited.write_text("drift\n")

    batch = run_alctl("update", "--all", "--state-root", workspace["state"], cwd=workspace["root"])
    assert batch.returncode == 1
    assert sorted(row["status"] for row in batch.data) == ["failed", "updated"]
    assert edited.read_text() == "drift\n"


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _legacy_review_and_adoption(workspace, home, source):
    package = home / ".codex/skills/al"
    shutil.copytree(source / "al/codex/skills/al", package)
    shutil.rmtree(package / "tests", ignore_errors=True)
    (package / "AGENTS.md").write_text("Review AL package changes before updating.\n")
    (package / "user-data.txt").write_text("preserve this unmanaged file\n")
    config = home / ".codex/config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('[custom]\nkeep = "user setting"\n')

    target = resolve_target(home=home, platforms=("codex",))
    instruction_paths = (package_path(target, "codex"), roles_path(target, "codex"), home / ".codex")
    instructions = {}
    for path in instruction_paths:
        for item in D.instructions(path, target["identity"]):
            instructions[item["path"]] = item
    review = {"instructions": {
        "fixture-review": {
            "reviewed": True,
            "reason": "Reviewed the exact package instruction set for this fixture.",
            "instructions": sorted(instructions.values(), key=lambda item: item["path"]),
        }
    }}
    review_path = workspace["root"] / "reviews.json"
    review_path.write_text(json.dumps(review, sort_keys=True))

    release = resolve_release(str(source), version="1.0.0")
    manifest = build_manifest(release)
    baseline = {
        name.removeprefix("codex/skills/al/"): item["sha256"]
        for name, item in manifest["files"].items()
        if name.startswith("codex/skills/al/") and item["kind"] == "package"
    }
    existing = D.managed(package)
    assert set(baseline) <= existing.keys()
    source_manifest = _write_json(workspace["root"] / "source-manifest.json", baseline)
    inventory = _write_json(workspace["root"] / "inventory.json", [{
        "target": str(package), "platform": "codex",
        "hashes": {name: item["sha256"] for name, item in existing.items()},
    }])
    descriptor = {
        "target": str(package),
        "platform": "codex",
        "reviewed": True,
        "reason": "Reviewed existing AL resources against the fixture inventory.",
        "identity": D.repo_identity(package),
        "source_manifest": source_manifest,
        "inventory": inventory,
        "files": {
            name: {**existing[name], "ownership_source": "reviewed baseline inventory"}
            for name in baseline
        },
    }
    adoption = {"schema_version": 1, "targets": [descriptor]}
    adoption_path = workspace["root"] / "adoption.json"
    adoption_path.write_text(json.dumps(adoption, sort_keys=True))
    return target, package, config, review_path, adoption_path, inventory["path"]


def test_cli_requires_real_review_and_adoption_then_preserves_unmanaged_data(installer_workspace):
    workspace = installer_workspace
    source_root = workspace["root"] / "legacy-source"
    source_root.mkdir()
    source = make_source(source_root)
    home = workspace["root"] / "legacy-home"
    home.mkdir()
    target, package, config, review_path, adoption_path, inventory_path = _legacy_review_and_adoption(
        workspace, home, source
    )
    common = (
        "install", "--global", "--home", home, "--state-root", workspace["state"],
        "--source", source, "--version", "1.0.0", "--platform", "codex", "--policy", "follow",
    )

    no_review = run_alctl(*common, cwd=workspace["root"])
    assert no_review.returncode == 1
    assert "instruction review required" in no_review.data["error"]

    reviewed_only = run_alctl(*common, "--reviews", review_path, cwd=workspace["root"])
    assert reviewed_only.returncode == 1
    assert "unmanaged resource collision" in reviewed_only.data["error"]

    adopted = run_alctl(
        *common, "--reviews", review_path, "--adopt", adoption_path, cwd=workspace["root"]
    )
    assert adopted.returncode == 0, adopted.stdout + adopted.stderr
    assert adopted.data["status"] == "updated"
    assert (package / "user-data.txt").read_text() == "preserve this unmanaged file\n"
    assert '[custom]\nkeep = "user setting"' in config.read_text()

    (package / "AGENTS.md").write_text("Changed instruction; old review is stale.\n")
    stale_review = run_alctl(
        "update", "--global", "--home", home, "--state-root", workspace["state"],
        "--reviews", review_path, "--adopt", adoption_path, cwd=workspace["root"],
    )
    assert stale_review.returncode == 1
    assert "instruction review required" in stale_review.data["error"]

    # Refresh the review, then corrupt the referenced inventory: stale adoption
    # provenance must be rejected even for an already enrolled package.
    refreshed = json.loads(review_path.read_text())
    refreshed["instructions"]["fixture-review"]["instructions"] = D.instructions(package, target["identity"])
    review_path.write_text(json.dumps(refreshed, sort_keys=True))
    inventory = json.loads(Path(inventory_path).read_text())
    inventory[0]["hashes"]["SKILL.md"] = "0" * 64
    Path(inventory_path).write_text(json.dumps(inventory, sort_keys=True))
    stale_adoption = run_alctl(
        "update", "--global", "--home", home, "--state-root", workspace["state"],
        "--reviews", review_path, "--adopt", adoption_path, cwd=workspace["root"],
    )
    assert stale_adoption.returncode == 1
    assert "provenance artifact changed" in stale_adoption.data["error"]
