"""Stable CLI snapshots remain independent from the source checkout."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from al.tests.conftest import copy_al_source
from al.tests.test_manifest import make_source


def _environment():
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("AL_RUNTIME_DIR", None)
    environment.pop("AL_DISTRIBUTION_STATE", None)
    return environment


def test_launcher_runs_from_snapshot_after_source_is_removed(tmp_path):
    source = copy_al_source(tmp_path / "checkout/al")
    release_source = make_source(tmp_path)
    state = tmp_path / "state"
    launcher = tmp_path / "bin/alctl"
    boot = subprocess.run(
        [sys.executable, str(source / "alctl.py"), "bootstrap", "--source-root", str(source),
         "--destination", str(launcher), "--state-root", str(state)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=_environment(),
    )
    assert boot.returncode == 0, boot.stdout + boot.stderr
    receipt = json.loads(boot.stdout)["receipt"]
    assert receipt["kind"] == "development"
    assert receipt["dirty"] is True
    assert receipt["content_digest"]
    assert receipt["source_commit_if_available"] is None
    snapshot = Path(json.loads(boot.stdout)["snapshot"])
    assert (snapshot / "installer/cli.py").is_file()

    import shutil

    shutil.rmtree(source.parent)
    installed_home = tmp_path / "installed-home"
    installed_home.mkdir()
    installed = subprocess.run(
        [str(launcher), "install", "--global", "--home", str(installed_home),
         "--state-root", str(state), "--source", str(release_source),
         "--version", "1.0.0", "--platform", "codex"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=_environment(),
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert json.loads(installed.stdout)["status"] == "updated"
    assert (installed_home / ".codex/skills/al/SKILL.md").is_file()


def test_bootstrap_revalidates_snapshot_and_refuses_launcher_collision(tmp_path):
    source = copy_al_source(tmp_path / "checkout/al")
    state = tmp_path / "state"
    launcher = tmp_path / "bin/alctl"
    source_cli = source / "alctl.py"

    def bootstrap():
        return subprocess.run(
            [sys.executable, str(source_cli), "bootstrap", "--source-root", str(source),
             "--destination", str(launcher), "--state-root", str(state)],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            env=_environment(),
        )

    first = bootstrap()
    assert first.returncode == 0, first.stdout + first.stderr
    snapshot = Path(json.loads(first.stdout)["snapshot"])
    (snapshot / "installer/cli.py").write_text("corrupted\n")
    corrupt = bootstrap()
    assert corrupt.returncode == 1
    assert "snapshot corrupt" in json.loads(corrupt.stdout)["error"]

    # A second source/destination pair cannot take over unrelated launcher bytes.
    other_state = tmp_path / "other-state"
    other_launcher = tmp_path / "other-bin/alctl"
    other_launcher.parent.mkdir()
    other_launcher.write_text("#!/bin/sh\necho unrelated\n")
    collision = subprocess.run(
        [sys.executable, str(source_cli), "bootstrap", "--source-root", str(source),
         "--destination", str(other_launcher), "--state-root", str(other_state)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=_environment(),
    )
    assert collision.returncode == 1
    assert "launcher collision" in json.loads(collision.stdout)["error"]
    assert other_launcher.read_text() == "#!/bin/sh\necho unrelated\n"


def test_existing_snapshot_preserves_receipt_with_fresh_git_observation(tmp_path):
    source = copy_al_source(tmp_path / "checkout/al")
    repo = source.parent
    env = _environment()
    def git(*args):
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    git('init');git('config', 'user.email', 'fixture@example.test');git('config', 'user.name', 'Fixture')
    git('add', '.');git('commit', '-qm', 'source')
    state, destination = tmp_path/'state', tmp_path/'bin/alctl'
    def boot():
        return subprocess.run([sys.executable, str(source/'alctl.py'), 'bootstrap',
            '--source-root', str(source), '--destination', str(destination), '--state-root', str(state)],
            cwd=tmp_path, capture_output=True, text=True, env=env)
    def good():
        result = boot()
        assert result.returncode == 0, result.stdout[-2000:]+result.stderr[-2000:]
        return json.loads(result.stdout)
    first = good()
    receipt_path = Path(first['snapshot'])/'receipt.json'
    original = receipt_path.read_bytes()
    outside = repo/'outside.txt'
    outside.write_text('unselected provenance change\n')
    dirty = good()
    assert dirty['snapshot'] == first['snapshot'] and dirty['receipt'] == first['receipt']
    assert dirty['bootstrap_observation']['dirty'] is True
    assert receipt_path.read_bytes() == original
    git('add', 'outside.txt');git('commit', '-qm', 'outside closure')
    committed = good()
    assert committed['bootstrap_observation']['source_commit_if_available'] == git('rev-parse', 'HEAD')
    assert committed['bootstrap_observation']['source_commit_if_available'] != first['receipt']['source_commit_if_available']
    assert committed['receipt'] == first['receipt'] and receipt_path.read_bytes() == original
    owner = next((state/'installer/cli').glob('launcher-*.json'))
    owner_raw = owner.read_bytes()
    owner.unlink()
    refused = boot()
    assert refused.returncode != 0 and 'launcher' in refused.stdout
    owner.write_bytes(owner_raw);owner.chmod(0o600)
    forged = dict(first['receipt'], **committed['bootstrap_observation'])
    receipt_path.write_text(json.dumps(forged))
    refused = boot()
    assert refused.returncode != 0 and 'snapshot corrupt' in refused.stdout
    receipt_path.write_bytes(original)
    destination.write_bytes(destination.read_bytes()+b'\n')
    refused = boot()
    assert refused.returncode != 0 and 'launcher' in refused.stdout
