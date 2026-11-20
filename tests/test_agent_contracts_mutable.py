# ruff: noqa: F811
"""Real strict consumers under supported conditions; no simulated process closure."""

import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from harness.execution_bootstrap import authority_snapshot
from harness.execution_contract import canonical, digest
from harness.execution_store import get_execution
from harness.policy.context import CURRENT, scope
from harness.policy.resources import prepare
from tests.test_policy_admission import observed, patch, policy_runtime, processes  # noqa: F401


def add_dependency(r):
    """Trusted authored input fixture; no process was launched or declared closed."""
    root = Path(r.operation.envelope["repository"])
    (root / "lib").mkdir()
    raw = b"answer = 42\n"
    (root / "lib/input.py").write_bytes(raw)
    aid = str(uuid.uuid4())
    directory = next(r.operation.registry.native_root.glob("sessions/*/*/*"))
    (directory / ("rollout-" + aid + ".jsonl")).write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": aid,
                    "session_id": aid,
                    "cli_version": "0.165.0",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": r.root["thread_id"],
                                "agent_role": "al-engineer",
                            }
                        }
                    },
                },
            }
        )
        + "\n"
    )
    producer = r.operation.registry.enroll(aid)
    requirement = digest("accepted fixture requirements")
    r.state["implementation_readiness"] = {"binding": {"requirement_hash": requirement}}
    r.state["evidence"] = {"entries": [], "plan": {}}
    r.state["integration"]["agents"] = {
        aid: {
            "id": producer["thread_id"],
            "task": "producer",
            "role": "al-engineer",
            "status": "Completed",
            "ended_at": time.time(),
        }
    }
    receipt = {
        "task_id": "producer",
        "task_revision": 1,
        "graph_revision": 1,
        "requirement_hash": requirement,
        "candidate_identity": {"scope_fingerprint": digest(raw), "dependency_receipts": {}},
        "output_refs_and_hashes": [
            {
                "kind": "source",
                "path": "lib/input.py",
                "sha256": digest(raw),
                "mode": (root / "lib/input.py").stat().st_mode & 0o777,
            }
        ],
        "producer_invocation_ids": [aid],
        "writer_ids": [],
    }
    receipt["receipt_hash"] = digest(receipt)
    r.state["integration"]["tasks"]["producer"] = {
        "id": "producer",
        "title": "Observed source",
        "revision": 1,
        "status": "Waiting",
        "files": ["lib"],
        "dependencies": [],
        "criteria": ["AC-01"],
        "authored_output": receipt,
    }
    r.state["integration"]["tasks"]["work"]["dependencies"] = ["producer"]
    r.save()
    execution = get_execution(r.controller.conn, r.operation.execution_id)
    authority_snapshot(Path(execution["handoff_path"]).parent)
    return r


@pytest.fixture
def strict_runtime(policy_runtime):
    r = add_dependency(policy_runtime)
    assert r.controller.conn.execute("SELECT count(*) FROM execution_attempts").fetchone()[0] == 0
    assert (
        r.controller.conn.execute("SELECT count(*) FROM policy_legacy_admissions").fetchone()[0]
        == 0
    )
    return r


def test_public_patch_claims_precede_hashes_and_survive_actual_replace(strict_runtime, monkeypatch):
    import harness.execution_files as files

    r = strict_runtime
    real_parent, real_replace = files.parent_descriptor, files.os.replace
    stages = []

    def assert_claims():
        attempt = CURRENT.get()
        assert attempt is not None and not attempt.legacy
        rows = r.controller.conn.execute(
            "SELECT claim,state FROM policy_resource_claims WHERE attempt_id=?",
            (attempt.intent.id,),
        ).fetchall()
        assert any(json.loads(row["claim"]).get("purpose") == "mutable_dependency" for row in rows)
        assert all(row["state"] in {"reserved", "active"} for row in rows)

    @contextmanager
    def parent(root, path, **kwargs):
        if path == "lib/input.py":
            assert_claims()
            stages.append("hash")
        with real_parent(root, path, **kwargs) as value:
            yield value

    def replace(*args, **kwargs):
        assert_claims()
        stages.append("replace")
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(files, "parent_descriptor", parent)
    monkeypatch.setattr(files.os, "replace", replace)
    result = r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert result["coverage"]["coordinated_source_consumption"] is True
    assert stages and stages[0] == "hash" and "replace" in stages
    assert (Path(r.operation.envelope["repository"]) / "svc/new.txt").read_text() == "safe\n"
    assert r.state["integration"]["tasks"]["producer"]["status"] == "Waiting"
    assert (
        r.controller.conn.execute(
            "SELECT count(*) FROM policy_resource_claims WHERE state!='closed'"
        ).fetchone()[0]
        == 0
    )


def test_metadata_requires_zero_source_reads_and_direct_consumption_has_no_fake_lease(
    strict_runtime, monkeypatch
):
    import harness.execution_files as files
    from harness.execution_authority import require_dependency_outputs

    r = strict_runtime
    monkeypatch.setattr(
        files, "parent_descriptor", lambda *a, **kw: pytest.fail("metadata read source bytes")
    )
    r.operation.admitted(r.controller, r.principal, "work", r.revision, verify_outputs=False)
    with pytest.raises(ValueError, match="owning reserved attempt"):
        require_dependency_outputs(
            r.state,
            "work",
            r.operation.envelope["repository"],
            get_execution(r.controller.conn, r.operation.execution_id),
        )


def test_strict_first_refuses_unqualified_read_read_launcher_and_keeps_independent_reads(
    strict_runtime,
):
    r = strict_runtime
    attempt = prepare(r.operation, r.controller, r.principal, "apply_patch", patch(r), r.state)
    try:
        with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
            prepare(
                r.operation,
                r.controller,
                r.principal,
                "run_command",
                {"argv": ["git", "status", "--short"], "cwd": ".", "expected_revision": r.revision},
                r.state,
            )
        root = Path(r.operation.envelope["repository"])
        (root / "independent.txt").write_text("independent")
        assert (
            r.operation.dispatch(r.principal, "read_file", {"path": "independent.txt"})["text"]
            == "independent"
        )
    finally:
        attempt.release_unstarted()


def test_actual_legacy_read_process_remains_predecessor_after_parent_exit(policy_runtime):
    r = policy_runtime
    request = {"argv": ["git", "status", "--short"], "cwd": ".", "expected_revision": r.revision}
    legacy = prepare(r.operation, r.controller, r.principal, "run_command", request, r.state)
    with scope(legacy):
        result = r.operation.processes.start(
            r.principal["thread_id"],
            r.operation.profile(()),
            ["/usr/bin/git", "status", "--short"],
            r.operation.envelope["repository"],
            task=None,
            revision=None,
            timeout=10,
        )
        record = r.operation.processes._owned(r.principal["thread_id"], result["handle"])
        record["thread"].join(15)
        assert not record["thread"].is_alive()
        legacy.owner_finished()
    rows = r.controller.conn.execute("SELECT * FROM policy_legacy_admissions").fetchall()
    assert rows and any(
        json.loads(row["resources"])[0]["access"] == "read_dependency" for row in rows
    )
    add_dependency(r)
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert not (Path(r.operation.envelope["repository"]) / "svc/new.txt").exists()
    assert r.controller.conn.execute("SELECT count(*) FROM policy_legacy_admissions").fetchone()[
        0
    ] == len(rows)


def test_changed_receipt_before_start_invalidates_reserved_hash(strict_runtime):
    r = strict_runtime
    attempt = prepare(r.operation, r.controller, r.principal, "apply_patch", patch(r), r.state)
    try:
        receipt = r.state["integration"]["tasks"]["producer"]["authored_output"]
        receipt["candidate_identity"]["scope_fingerprint"] = digest("a different observation")
        receipt["receipt_hash"] = digest({k: v for k, v in receipt.items() if k != "receipt_hash"})
        r.save()
        with pytest.raises(ValueError, match="AUTHORITY_STALE"):
            attempt.start()
        assert not attempt.started
    finally:
        attempt.release_unstarted()


def test_valid_immutable_old_bridge_is_not_qualified_by_module_presence(strict_runtime):
    import hashlib

    from harness.execution_bridge import bridge_root, verify_snapshot

    r = strict_runtime
    root = bridge_root(get_execution(r.controller.conn, r.operation.execution_id))
    file = root / "harness/policy/native_presence.py"
    file.write_bytes(b'"""Historical source without reciprocal strict launch support."""\n')
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["files"]["policy/native_presence.py"] = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest["fingerprint"] = hashlib.sha256(
        b"".join(
            k.encode() + (root / "harness" / k).read_bytes() for k in sorted(manifest["files"])
        )
    ).hexdigest()
    (root / "manifest.json").write_text(canonical(manifest))
    assert verify_snapshot(root)
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))


def test_source_only_owned_validation_blocks_unqualified_read_read(strict_runtime):
    """Isolate read/read sharing from the patch handler's destination write claim."""
    import secrets

    from harness.execution_authority import require_dependency_outputs
    from harness.policy.admission import Attempt
    from harness.policy.composition import select
    from harness.policy.contracts import OperationIntent
    from harness.policy.resource_bindings import derive
    from harness.policy.source_dependencies import bind_protocol, prepare_support

    r = strict_runtime
    operation, conn = r.operation, r.controller.conn
    prepare(operation, r.controller, r.principal, "read_file", {"path": "lib/input.py"}, r.state)
    execution = get_execution(conn, operation.execution_id)
    _, digest_value = select(conn, execution, r.principal)
    support = prepare_support(conn)
    intent = OperationIntent(
        secrets.token_hex(16),
        execution["id"],
        execution["epoch_id"],
        execution["authority_generation"],
        r.principal["thread_id"],
        r.principal["role"],
        "work",
        r.revision,
        "read_file",
        ("file:lib/input.py",),
        False,
        False,
        canonical(execution["binding"]),
        None,
        claims=bind_protocol(
            derive(
                operation,
                r.principal,
                r.state,
                task_id="work",
                resources=("file:lib/input.py",),
                mutating=False,
                boundary=None,
            ),
            support,
        ),
    )
    attempt = Attempt(operation, r.principal, intent, digest_value)
    attempt.source_support = support
    attempt.admit()
    try:
        with scope(attempt):
            require_dependency_outputs(
                r.state,
                "work",
                operation.envelope["repository"],
                execution,
                consumption=attempt,
                connection=conn,
            )
            attempt.start()
            assert all(c["access"] == "read_dependency" for c in attempt.intent.claims)
            with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
                prepare(
                    operation,
                    r.controller,
                    r.principal,
                    "run_command",
                    {
                        "argv": ["git", "status", "--short"],
                        "cwd": ".",
                        "expected_revision": r.revision,
                    },
                    r.state,
                )
    finally:
        attempt.owner_finished()


def test_public_reserved_consumer_preserves_original_source_drift_refusal(strict_runtime):
    """Retain the original bytes-changed assertion under genuinely supported reservation."""
    r = strict_runtime
    root = Path(r.operation.envelope["repository"])
    (root / "lib/input.py").write_text("external drift\n")
    with pytest.raises(ValueError, match="bytes changed"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert not (root / "svc/new.txt").exists()
    assert r.controller.conn.execute("SELECT 1 FROM policy_legacy_admissions").fetchone() is None


def test_unqualified_setup_refuses_before_source_or_manifest_bytes(strict_runtime, monkeypatch):
    import harness.execution_files as files
    import harness.task_environment as setup
    from harness.task_environment import context_revision, task_unit

    r = strict_runtime
    root = Path(r.operation.envelope["repository"])
    (root / "svc").mkdir(exist_ok=True)
    request = dict(
        operation="prepare",
        task_id="work",
        cwd="svc",
        expected_revision=context_revision(r.state, "work"),
    )
    outcomes = dict(r.operation.setup_outcomes)
    handles = set(r.operation.processes.records)

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported setup read target bytes")

    monkeypatch.setattr(setup, "ordinary", forbidden)
    monkeypatch.setattr(files, "parent_descriptor", forbidden)
    # Real preflight validates the current scope without hashing source outputs.
    assert (
        task_unit(r.operation, r.controller, r.state, r.principal, request, verify_outputs=False)
        == "svc"
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        r.operation.dispatch(r.principal, "environment_operation", request)
    assert r.operation.setup_outcomes == outcomes
    assert set(r.operation.processes.records) == handles
