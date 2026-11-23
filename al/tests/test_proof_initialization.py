"""Mandatory pending proof is an independently anchored new-run default."""

import json
from pathlib import Path
import sys

import pytest

from al.tests.runtime_run_helpers import invoke, launched, public_run, run_record


def begin(launched, platform):
    result = public_run(
        launched, platform, "start", "--spec", "Establish observable proof before implementation."
    )
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    return launched[2] / ("." + platform) / "al-runtime/pin-flow.json"


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_every_public_new_start_has_independent_pending_initialization(launched, platform):
    state_path = begin(launched, platform)
    state = json.loads(state_path.read_bytes())
    admission = state["proof_admission"]
    path, record = run_record(launched)
    reference = record["initialization"]
    assert admission["status"] == "pending"
    assert admission["initialization"] == state["runtime_origin"]["initialization"] == reference
    assert Path(reference["stored_path"]).is_relative_to(path.parent.parent / "initializations")
    initial = json.loads(Path(reference["stored_path"]).read_bytes())
    assert initial["original_task_package"] == state["task_package"]
    assert initial["runtime_pin"] == record["pin"]
    assert initial["run_id"] == state["run_id"]
    assert initial["planning_occurrence"] == admission["planning_occurrence"] == 0
    assert initial["verifier_policy"] == admission["verifier_policy"]
    assert admission["invalidation_generation"] == 0


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_pending_proof_allows_scoped_implementation_but_never_verified_approval(launched, platform):
    state_path = begin(launched, platform)
    initial = json.loads(state_path.read_bytes())

    def succeeds(*args):
        result = public_run(launched, platform, *args)
        assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])

    def refuses(*args, diagnostic):
        before = state_path.read_bytes()
        result = public_run(launched, platform, *args)
        assert result.returncode != 0
        assert diagnostic in result.stdout + result.stderr
        assert state_path.read_bytes() == before

    writer = ("writer", "start", "--id", "scoped", "--role", "al-engineer", "--files", "app.py")
    degraded = (*writer, "--degraded", "--reason", "Explicit pending-proof tracking.")
    phase_guard = "writer admission requires a writing phase or explicit degraded planning tracking"
    # Before SPEC_READY/classification ordinary planning writers fail the phase guard,
    # which names the next supported command. The explicit degraded route separately
    # proves SPEC_READY and classification still apply.
    refuses(*writer, diagnostic=phase_guard)
    refuses(*writer, diagnostic="`spec-gate SPEC_READY`")
    refuses(*degraded, diagnostic="SPEC_READY and classification")
    succeeds("spec-gate", "SPEC_READY")
    refuses(*writer, diagnostic=phase_guard)
    refuses(*degraded, diagnostic="SPEC_READY and classification")
    succeeds("classify", "SMALL", "--designer", "no", "--reason", "Bounded observable repair.")
    # After classification the first ordinary writer records PLANNING -> IMPLEMENTATION.
    succeeds(*writer)
    admitted = json.loads(state_path.read_bytes())
    assert admitted["phase"] == "IMPLEMENTATION"
    scoped = admitted["writers"]["scoped"]
    assert scoped["state"] == "ACTIVE" and scoped["files"] == ["app.py"]
    assert scoped["role"] == "al-engineer" and "degraded" not in scoped
    assert admitted["proof_admission"]["status"] == "pending"
    assert (
        "Architect proof admission pending" in admitted["implementation_readiness"]["obligations"]
    )
    assert not admitted.get("gate_manifests") and not admitted["evidence"]["entries"]
    assert "repository gate manifest missing" in admitted["implementation_readiness"]["obligations"]

    refuses(
        "writer",
        "start",
        "--id",
        "overlap",
        "--role",
        "al-builder",
        "--files",
        "app.py",
        diagnostic="SMALL permits one writer",
    )
    refuses(
        "writer",
        "start",
        "--id",
        "escape",
        "--role",
        "al-engineer",
        "--files",
        "../outside.py",
        diagnostic="writer path must be a specific repository-relative path",
    )
    refuses(
        "writer",
        "start",
        "--id",
        "invalid-role",
        "--role",
        "al-architect",
        "--files",
        "app.py",
        diagnostic="invalid writer role",
    )
    (launched[2] / "app.py").write_text('def observable():\n    return "scoped implementation"\n')
    succeeds("writer", "finish", "--id", "scoped", "--outcome", "success")
    succeeds("phase", "VERIFICATION")
    refuses("review-start", diagnostic="proof admission pending")
    refuses(
        "phase",
        "FINAL_VERIFICATION",
        diagnostic="illegal phase edge VERIFICATION → FINAL_VERIFICATION",
    )
    refuses("approved", diagnostic="approved is not allowed from VERIFICATION")

    remaining = json.loads(state_path.read_bytes())
    assert remaining["status"] == "RUNNING" and remaining["phase"] == "VERIFICATION"
    assert remaining["writers"]["scoped"]["state"] == "FINISHED"
    assert remaining["proof_admission"]["status"] == "pending"
    assert (
        remaining["proof_admission"]["initialization"]
        == initial["proof_admission"]["initialization"]
    )
    assert remaining["runtime_origin"] == initial["runtime_origin"]
    assert not remaining.get("approval") and not remaining["evidence"]["entries"]
    assert set(admitted["implementation_readiness"]["obligations"]) <= set(
        remaining["implementation_readiness"]["obligations"]
    )


@pytest.mark.parametrize(
    "mutation", ["missing", "downgraded", "forged-admitted", "lineage", "initialization-reference"]
)
def test_state_cannot_disable_mandatory_proof(launched, mutation):
    state_path = begin(launched, "codex")
    state = json.loads(state_path.read_bytes())
    if mutation == "missing":
        del state["proof_admission"]
    elif mutation == "downgraded":
        state["proof_admission"]["schema_version"] = 0
    elif mutation == "forged-admitted":
        state["proof_admission"]["status"] = "admitted"
    elif mutation == "lineage":
        state["proof_admission"]["requirement_lineage_hash"] = "0" * 64
    else:
        state["proof_admission"]["initialization"]["stored_path"] = str(
            state_path.parent / "forged"
        )
    state_path.write_text(json.dumps(state))
    before = state_path.read_bytes()
    result = public_run(launched, "codex", "show", "--json")
    assert result.returncode != 0
    assert state_path.read_bytes() == before


def test_unpinned_candidate_start_cannot_choose_legacy(launched):
    source, _, repo, _, environment = launched
    result = invoke(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            source / "codex/skills/al/scripts/al_state.py",
            "--session",
            "direct",
            "start",
            "--spec",
            "Do not manufacture legacy origin.",
        ],
        repo,
        environment,
    )
    assert result.returncode != 0
    assert "protected managed alctl run launcher" in result.stdout + result.stderr
    assert not (repo / ".codex/al-runtime/direct.json").exists()


def test_substantive_amendment_retains_origin_and_returns_current_pending(launched):
    state_path = begin(launched, "codex")
    old = json.loads(state_path.read_bytes())
    request = launched[2] / "amendment.json"
    request.write_text(
        json.dumps(
            {
                "id": "additional-requirement",
                "text": "Also preserve every existing observable behavior.",
                "source": "explicit fixture user amendment",
            }
        )
    )
    result = public_run(launched, "codex", "task", "amend", "--file", str(request))
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    shown = public_run(launched, "codex", "show", "--json")
    assert shown.returncode == 0, (shown.stdout[-2000:], shown.stderr[-2000:])
    state = json.loads(shown.stdout)
    assert state["runtime_origin"] == old["runtime_origin"]
    assert state["proof_admission"]["initialization"] == old["proof_admission"]["initialization"]
    assert state["proof_admission"]["invalidation_generation"] == 1
    assert state["proof_admission"]["status"] == "pending"
    assert (
        state["proof_admission"]["requirement_lineage_hash"]
        != old["proof_admission"]["requirement_lineage_hash"]
    )


def origin_query(launched, platform="codex", *, allow_absent=False):
    return invoke(
        [
            launched[3],
            "runtime-successor",
            "origin",
            "--repository",
            launched[2],
            "--platform",
            platform,
            "--session",
            "pin-flow",
            *(["--allow-absent"] if allow_absent else []),
        ],
        launched[2],
        launched[4],
    )


def test_origin_absence_is_explicit_and_default_contract_remains_strict(launched):
    assert origin_query(launched).returncode != 0
    observed = origin_query(launched, allow_absent=True)
    assert observed.returncode == 0, observed.stdout + observed.stderr
    expected = {
        "schema_version": 1,
        "kind": "runtime-origin-absence-v1",
        "state": "ABSENT",
        "repository": str(launched[2]),
        "platform": "codex",
        "session_id": "pin-flow",
    }
    assert json.loads(observed.stdout) == expected
    assert json.loads(origin_query(launched, allow_absent=True).stdout) == expected
    begin(launched, "codex")
    original = origin_query(launched)
    active = origin_query(launched, allow_absent=True)
    assert original.returncode == active.returncode == 0
    assert json.loads(original.stdout) == json.loads(active.stdout)
    assert set(json.loads(active.stdout)) == {"storage", "transaction", "source"}


@pytest.mark.parametrize(
    "mutation",
    [
        "permission",
        "malformed",
        "foreign",
        "reserved",
        "ambiguous",
        "missing-binding",
        "orphan-initialization",
        "corrupt-orphan-initialization",
        "symlink",
    ],
)
def test_origin_absence_never_reclassifies_refused_or_partial_runs(launched, mutation):
    source = begin(launched, "codex")
    path, record = run_record(launched)
    lock = path.with_suffix(".lock")
    original_mode = lock.stat().st_mode & 0o7777
    if mutation == "permission":
        lock.chmod(0)
    elif mutation == "malformed":
        path.write_text("{")
    elif mutation == "foreign":
        record["session_id"] = "foreign"
        path.write_text(json.dumps(record))
    elif mutation == "reserved":
        record["status"] = "RESERVED"
        path.write_text(json.dumps(record))
    elif mutation == "ambiguous":
        duplicate = path.parent / "ambiguous.json"
        duplicate.write_bytes(path.read_bytes())
        duplicate.chmod(0o600)
    elif mutation == "symlink":
        original = path.with_suffix(".retained")
        path.rename(original)
        path.symlink_to(original)
    else:
        path.unlink()
        if mutation in {"orphan-initialization", "corrupt-orphan-initialization"}:
            source.unlink()
        if mutation == "corrupt-orphan-initialization":
            initial = Path(record["initialization"]["stored_path"])
            changed = json.loads(initial.read_bytes())
            changed["session_id"] = "foreign"
            initial.write_text(json.dumps(changed))
    before = source.read_bytes() if source.exists() else None
    try:
        result = origin_query(launched, allow_absent=True)
        assert result.returncode != 0
        assert json.loads(result.stdout)["status"] == "failed"
        assert "ABSENT" not in result.stdout
        assert (source.read_bytes() if source.exists() else None) == before
    finally:
        if mutation == "permission":
            lock.chmod(original_mode)


@pytest.mark.parametrize("partial", ["initial-inputs", "durable-state"])
def test_origin_absence_rejects_prebinding_and_orphaned_durable_metadata(launched, partial):
    from al.installer.runtime_pin_transaction import key
    from al.installer.validator_origin import repository_identity

    assert origin_query(launched, allow_absent=True).returncode == 0
    root = launched[1] / "installer/al-run-pins"
    if partial == "initial-inputs":
        subject = {
            "repository_identity": repository_identity(launched[2]),
            "platform": "codex",
            "session_id": "pin-flow",
        }
        (root / "successors").mkdir(mode=0o700)
        (root / "successors" / ("initial-" + key(subject))).mkdir(mode=0o700)
    else:
        directory = root
        for part in ("harness-runtimes", "a" * 64, "exec-fixture", "legacy-0"):
            directory = directory / part
            directory.mkdir(mode=0o700)
        state = directory / "pin-flow.json"
        state.write_text("{}")
        state.chmod(0o600)
    result = origin_query(launched, allow_absent=True)
    assert result.returncode != 0 and "ABSENT" not in result.stdout


@pytest.mark.parametrize("member", ["unknown-file", "unknown-directory", "miskeyed-record"])
def test_absence_query_refuses_unknown_registry_members(launched, member):
    assert origin_query(launched, allow_absent=True).returncode == 0
    root = launched[1] / "installer/al-run-pins/runs"
    path = root / member
    if member == "unknown-directory":
        path.mkdir(mode=0o700)
    elif member == "unknown-file":
        path.write_text("unknown partial metadata")
        path.chmod(0o600)
    else:
        path = path.with_suffix(".json")
        path.write_text(
            json.dumps(
                {
                    "repository_identity": {"root": str(launched[2])},
                    "platform": "codex",
                    "session_id": "foreign",
                }
            )
        )
        path.chmod(0o600)
    result = origin_query(launched, allow_absent=True)
    assert result.returncode != 0 and "ABSENT" not in result.stdout


def durable_epoch_fixture(runtime):
    root = runtime[1] / "installer/al-run-pins/harness-runtimes"
    directory = root
    for part in ("a" * 64, "exec-old-fixture", "epoch-deadbeef"):
        directory.mkdir(mode=0o700, exist_ok=True)
        directory = directory / part
    directory.mkdir(mode=0o700)
    return directory


def test_absence_ignores_large_unrelated_epoch_artifact_payload(launched):
    assert origin_query(launched, allow_absent=True).returncode == 0
    epoch = durable_epoch_fixture(launched)
    old = epoch / "unrelated-old-session"
    old.mkdir(mode=0o700)
    artifacts = old / "artifacts"
    artifacts.mkdir(mode=0o700)
    for index in range(10001):
        (artifacts / str(index)).touch()
    result = origin_query(launched, allow_absent=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "ABSENT"


def test_absence_refuses_exact_orphan_session_directory(launched):
    assert origin_query(launched, allow_absent=True).returncode == 0
    (durable_epoch_fixture(launched) / "pin-flow").mkdir(mode=0o700)
    result = origin_query(launched, allow_absent=True)
    assert result.returncode != 0 and "ABSENT" not in result.stdout


@pytest.mark.parametrize(
    "mutation", ["authority", "execution", "epoch", "symlink", "permissions", "file"]
)
def test_absence_refuses_unknown_durable_structure(launched, mutation):
    assert origin_query(launched, allow_absent=True).returncode == 0
    epoch = durable_epoch_fixture(launched)
    execution, authority = epoch.parent, epoch.parent.parent
    original_mode = authority.stat().st_mode & 0o7777
    if mutation == "authority":
        authority.rename(authority.with_name("unknown-authority"))
    elif mutation == "execution":
        execution.rename(execution.with_name("-invalid"))
    elif mutation == "epoch":
        epoch.rename(epoch.with_name("unknown-epoch"))
    elif mutation == "symlink":
        retained = authority.parent.parent / "retained-fixture-authority"
        authority.rename(retained)
        authority.symlink_to(retained, target_is_directory=True)
    elif mutation == "permissions":
        authority.chmod(0)
    else:
        (authority / "exec-file").write_text("{}")
    try:
        result = origin_query(launched, allow_absent=True)
        assert result.returncode != 0 and "ABSENT" not in result.stdout
    finally:
        if mutation == "permissions":
            authority.chmod(original_mode)
