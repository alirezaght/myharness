"""Managed runtime pins keep recovery capability mandatory for their own runs."""

import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from al.tests.runtime_run_helpers import launched, run_record


def private_inventory(root):
    return {
        path.relative_to(root).as_posix(): (
            stat.S_IMODE(path.stat().st_mode),
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *root.rglob("*")]
    }


@pytest.mark.parametrize("platform", ["codex", "claude"])
@pytest.mark.parametrize("removed", [
    ("rework_recovery",),
    ("rework_recovery", "runtime_origin", "proof_admission"),
])
def test_managed_run_missing_recovery_capability(launched, platform, removed):
    """A current pinned issuer refuses a missing capability without repairing it."""
    source, home, repository, launcher, environment = launched
    session = f"recovery-capability-{platform}"

    def managed(*arguments):
        return subprocess.run(
            [
                launcher,
                "run",
                "--repository",
                repository,
                "--platform",
                platform,
                "--session",
                session,
                "--",
                *arguments,
            ],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            timeout=60,
        )

    started = managed("start", "--spec", "Test pinned recovery capability enforcement.")
    assert started.returncode == 0, (started.stdout[-2000:], started.stderr[-2000:])

    runtime = repository / f".{platform}/al-runtime"
    state_path = runtime / f"{session}.json"
    state_before = json.loads(state_path.read_bytes())
    assert isinstance(state_before.get("rework_recovery"), dict)
    transaction_path, transaction = run_record((source, home, repository, launcher, environment))
    pin_root = home / "installer/al-run-pins/pins" / transaction["pin"]["pin_identity"]
    protected_before = {
        "transaction": transaction_path.read_bytes(),
        "pin": private_inventory(pin_root),
    }
    if transaction.get("initialization", {}).get("stored_path"):
        initialization = Path(transaction["initialization"]["stored_path"])
        protected_before["initialization"] = initialization.read_bytes()

    tampered = dict(state_before)
    for name in removed:
        del tampered[name]
    tampered_bytes = json.dumps(tampered, indent=2, ensure_ascii=False).encode()
    state_path.write_bytes(tampered_bytes)
    tampered_digest = hashlib.sha256(state_path.read_bytes()).hexdigest()

    shown = managed("show", "--json")
    assert shown.returncode != 0, (shown.stdout[-2000:], shown.stderr[-2000:])
    expected = (
        "independently protected runtime binding differ"
        if "runtime_origin" in removed else "recovery"
    )
    assert expected in (shown.stdout + shown.stderr).lower()
    assert hashlib.sha256(state_path.read_bytes()).hexdigest() == tampered_digest
    assert state_path.read_bytes() == tampered_bytes
    assert transaction_path.read_bytes() == protected_before["transaction"]
    assert private_inventory(pin_root) == protected_before["pin"]
    if "initialization" in protected_before:
        assert initialization.read_bytes() == protected_before["initialization"]


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_managed_run_refuses_independent_pinned_payload_tamper(launched, platform):
    source, home, repository, launcher, environment = launched
    session = f"recovery-pin-tamper-{platform}"

    def managed(session_id, *arguments):
        return subprocess.run(
            [launcher, "run", "--repository", repository, "--platform", platform,
             "--session", session_id, "--", *arguments],
            cwd=repository, env=environment, text=True, capture_output=True, timeout=60,
        )

    started = managed(session, "start", "--spec", "Test immutable pin tamper refusal.")
    assert started.returncode == 0, (started.stdout[-2000:], started.stderr[-2000:])
    state_path = repository / f".{platform}/al-runtime/{session}.json"
    state_before = state_path.read_bytes()
    transaction_path, transaction = run_record((source, home, repository, launcher, environment))
    transaction_before = transaction_path.read_bytes()
    pin_root = home / "installer/al-run-pins/pins" / transaction["pin"]["pin_identity"]
    member = pin_root / "payload" / platform / "skills/al/scripts/al_state.py"
    original_member = member.read_bytes()
    member.write_bytes(original_member + b"\n")
    tampered_member = member.read_bytes()

    refused = managed(session, "show", "--json")
    assert refused.returncode != 0, (refused.stdout[-2000:], refused.stderr[-2000:])
    assert "pin" in (refused.stdout + refused.stderr).lower()
    assert state_path.read_bytes() == state_before
    assert transaction_path.read_bytes() == transaction_before
    assert member.read_bytes() == tampered_member


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_actual_old_issuer_and_state_remain_unchanged_after_updated_install(
    launched, tmp_path, platform
):
    source, home, repository, _, environment = launched
    from al.tests.old_issuer_input import materialize, verify

    old_home = tmp_path / f"old-issuer-{platform}"
    issuer, old_files = materialize(old_home, platform)
    old_session = f"old-recovery-{platform}"
    old_environment = dict(environment)
    old_environment.pop("AL_RUNTIME_DIR", None)
    if platform == "claude":
        old_environment["CLAUDE_PROJECT_DIR"] = str(repository)
    old_state_path = repository / f".{platform}/al-runtime/{old_session}.json"

    def old_public(*arguments):
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", issuer / "scripts/al_state.py",
             "--session", old_session, *arguments],
            cwd=repository, env=old_environment, text=True, capture_output=True, timeout=60,
        )

    started = old_public("start", "--spec", "Preserve this independently retained old issuer run.")
    assert started.returncode == 0, (started.stdout[-2000:], started.stderr[-2000:])
    old_before = old_state_path.read_bytes()
    old_state = json.loads(old_before)
    assert "rework_recovery" not in old_state
    verify(old_home, platform, old_files)

    from al.installer.bootstrap import bootstrap_cli

    updated_launcher = tmp_path / f"updated-{platform}/bin/alctl"
    updated_state = tmp_path / f"updated-{platform}/installer-state"
    bootstrap_cli(source, updated_launcher, updated_state)
    verify(old_home, platform, old_files)
    unsupported = old_public("rework-recovery", "packet")
    assert unsupported.returncode != 0
    assert "rework-recovery" in (unsupported.stdout + unsupported.stderr)
    assert old_state_path.read_bytes() == old_before

    stale_current = subprocess.run(
        [updated_launcher, "run", "--repository", repository, "--platform", platform,
         "--session", old_session, "--", "show", "--json"],
        cwd=repository, env=environment, text=True, capture_output=True, timeout=60,
    )
    assert stale_current.returncode != 0
    assert old_state_path.read_bytes() == old_before
    verify(old_home, platform, old_files)

    fresh_session = f"updated-recovery-{platform}"
    fresh = subprocess.run(
        [updated_launcher, "run", "--repository", repository, "--platform", platform,
         "--session", fresh_session, "--", "start", "--spec", "Fresh updated recovery run."],
        cwd=repository, env=environment, text=True, capture_output=True, timeout=60,
    )
    assert fresh.returncode == 0, (fresh.stdout[-2000:], fresh.stderr[-2000:])
    fresh_path = repository / f".{platform}/al-runtime/{fresh_session}.json"
    assert isinstance(json.loads(fresh_path.read_bytes()).get("rework_recovery"), dict)
    packet = subprocess.run(
        [updated_launcher, "run", "--repository", repository, "--platform", platform,
         "--session", fresh_session, "--", "rework-recovery", "packet"],
        cwd=repository, env=environment, text=True, capture_output=True, timeout=60,
    )
    assert packet.returncode != 0
    assert "no pending Architect reassessment" in (packet.stdout + packet.stderr)
    assert old_state_path.read_bytes() == old_before
    verify(old_home, platform, old_files)
