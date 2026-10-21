"""A genuinely admitted/approved pinned run must work at the installed validator seam."""

import json
import hashlib
import stat
import os
from pathlib import Path

import pytest

from al.tests.editorial_receipt_helpers import state, state_path, succeed
from al.tests.review_behavior_helpers import (
    ready_for_review,
    report_for,
    save_review,
    comparison_packet,
)
from al.tests.runtime_run_helpers import launched, public_run, invoke, run_record


def private_inventory(root):
    return {
        path.relative_to(root).as_posix(): (
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for path in [root, *root.rglob("*")]
    }


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_installed_validator_reads_actual_approved_pinned_run_without_runtime_writes(
    launched, platform
):
    issued = ready_for_review(launched, platform)
    succeed(
        save_review(
            launched,
            platform,
            report_for(launched, platform, issued, "fresh", "independent-terminal-fresh"),
        )
    )
    issued = comparison_packet(launched, platform)
    succeed(
        save_review(
            launched,
            platform,
            report_for(launched, platform, issued, "compare", "independent-terminal-comparison"),
        )
    )
    succeed(public_run(launched, platform, "phase", "FINAL_VERIFICATION"))
    succeed(public_run(launched, platform, "approved"))
    terminal = state(launched, platform)
    assert terminal["status"] == "APPROVED" and terminal["phase"] == "DONE"
    assert terminal["proof_admission"]["status"] == "admitted"
    repository = launched[2]
    before = state_path(launched, platform).read_bytes()
    index = repository / ".git/index"
    original_index = index.read_bytes()
    pin_store = launched[1] / "installer/al-run-pins"
    unchanged_private = private_inventory(pin_store)
    result = invoke(
        [
            launched[3],
            "source-proof",
            "--repository",
            repository,
            "--session",
            terminal["session_id"],
            "--run",
            terminal["run_id"],
            "--platform",
            platform,
        ],
        repository,
        launched[4],
    )
    (repository.parent / "installed-terminal-proof.stdout").write_text(result.stdout)
    (repository.parent / "installed-terminal-proof.stderr").write_text(result.stderr)
    assert state_path(launched, platform).read_bytes() == before
    assert index.read_bytes() == original_index
    assert private_inventory(pin_store) == unchanged_private
    assert result.returncode == 0, (result.returncode, result.stdout[-3000:], result.stderr[-3000:])
    proof = json.loads(result.stdout)
    assert proof["kind"] == "terminal-workspace-source-proof"
    assert proof["approval"]["task_hash"] == terminal["approval"]["task_hash"]
    assert proof["run_id"] == terminal["run_id"]
    assert len(proof["validator"]["caller_environment_digest"]) == 64
    assert len(proof["validator"]["run_pin_context_digest"]) == 64
    # Python startup remains isolated; an actual changed caller input still invalidates evidence.
    hostile = repository.parent / "hostile-python"
    hostile.mkdir()
    marker = repository.parent / "target-startup-executed"
    (hostile / "sitecustomize.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n"
    )
    changed = dict(launched[4], PYTHONPATH=str(hostile))
    refused = invoke(
        [
            launched[3],
            "source-proof",
            "--repository",
            repository,
            "--session",
            terminal["session_id"],
            "--run",
            terminal["run_id"],
            "--platform",
            platform,
        ],
        repository,
        changed,
    )
    assert refused.returncode != 0
    assert not marker.exists()
    assert state_path(launched, platform).read_bytes() == before
    assert index.read_bytes() == original_index
    transaction_path, transaction = run_record(launched)
    manifest = pin_store / "pins" / transaction["pin"]["pin_identity"] / "manifest.json"
    payload = manifest.parent / "payload"
    resource = payload / platform / "skills/al/scripts/al_architecture_check.py"
    initial = Path(transaction["initialization"]["stored_path"])
    arguments = [
        launched[3],
        "source-proof",
        "--repository",
        repository,
        "--session",
        terminal["session_id"],
        "--run",
        terminal["run_id"],
        "--platform",
        platform,
    ]
    for kind, target in (
        ("transaction-missing", transaction_path),
        ("transaction-foreign", transaction_path),
        ("pin-manifest-mode", manifest),
        ("pin-resource", resource),
        ("initialization", initial),
        ("state-downgrade", state_path(launched, platform)),
        ("new-member", payload / "unreviewed-module.py"),
    ):
        raw = target.read_bytes() if target.exists() else None
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
        try:
            if kind == "transaction-missing":
                target.unlink()
            elif kind == "transaction-foreign":
                value = json.loads(raw)
                value["run_id"] = "00000000"
                target.write_text(json.dumps(value))
            elif kind == "pin-manifest-mode":
                target.chmod(0o644)
            elif kind == "state-downgrade":
                value = json.loads(raw)
                value.pop("proof_admission")
                value.pop("runtime_origin")
                target.write_text(json.dumps(value))
            else:
                target.write_bytes((raw or b"") + b"\n")
            current_state = state_path(launched, platform).read_bytes()
            current_private = private_inventory(pin_store)
            refused = invoke(arguments, repository, launched[4])
            assert refused.returncode != 0, (kind, refused.stdout[-1000:], refused.stderr[-1000:])
            assert state_path(launched, platform).read_bytes() == current_state
            assert index.read_bytes() == original_index
            assert private_inventory(pin_store) == current_private
        finally:
            if raw is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(raw)
                target.chmod(mode)
    owner_path = (
        launched[1]
        / "installer/installer/cli"
        / ("launcher-" + hashlib.sha256(str(launched[3]).encode()).hexdigest() + ".json")
    )
    owner = json.loads(owner_path.read_bytes())
    from al.tests.pinned_terminal_probe import post_child_query

    for mutation in ("environment", payload / "post-child-member.py"):
        original_directory = payload.stat()
        try:
            refused, child_artifact = post_child_query(
                launched, platform, terminal, owner, mutation
            )
            assert child_artifact.is_file(), (
                refused.returncode,
                refused.stdout[-2000:],
                refused.stderr[-3000:],
            )
            assert refused.returncode != 0, (refused.stdout[-1500:], refused.stderr[-1500:])
            completed = json.loads(child_artifact.read_bytes())
            assert completed["kind"] == "terminal-workspace-source-proof"
            assert completed["run_id"] == terminal["run_id"]
            if mutation == "environment":
                assert "caller environment changed during query" in refused.stdout
            assert state_path(launched, platform).read_bytes() == before
            assert index.read_bytes() == original_index
        finally:
            if mutation != "environment":
                mutation.unlink(missing_ok=True)
                os.utime(
                    payload, ns=(original_directory.st_atime_ns, original_directory.st_mtime_ns)
                )
    succeed(invoke(arguments, repository, launched[4]))
    # A newer validator cannot silently treat an older resource/policy as current.
    models = launched[0] / platform / "skills/al/scripts/al_models.py"
    saved = models.read_bytes()
    try:
        models.write_bytes(saved + b"\n")
        upgraded = launched[1] / "bin/new-validator"
        succeed(
            invoke(
                [
                    launched[3],
                    "bootstrap",
                    "--source-root",
                    launched[0],
                    "--destination",
                    upgraded,
                    "--state-root",
                    launched[1] / "installer",
                ],
                repository,
                launched[4],
            )
        )
        refused = invoke([upgraded, *arguments[1:]], repository, launched[4])
        assert refused.returncode != 0
        assert (
            "current policy/resource differs from original pin" in refused.stdout + refused.stderr
        )
        assert state_path(launched, platform).read_bytes() == before
        assert index.read_bytes() == original_index
    finally:
        models.write_bytes(saved)
    succeed(invoke(arguments, repository, launched[4]))
