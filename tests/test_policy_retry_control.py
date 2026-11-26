# ruff: noqa: F811
"""Real sealed retry qualification and distinct helper/grant/save/closure facts."""

import hashlib
import json
import subprocess
import traceback
from pathlib import Path

import pytest

from harness.policy.control_channel import ControlChannel
from harness.policy.control_git import argv
from tests.test_policy_admission import configure, document
from tests.test_policy_dispatch import (  # noqa: F401
    operations,
    protected_policy_runtime,
    seed_protected_failure,
)


def setup_retry(r):
    seed_protected_failure(r)
    doc = document(r, maximum=20)
    doc["limits"].append(
        {
            "pool_id": "retry-control",
            "dimension": "retry_attempts",
            "domain": "execution_tree",
            "owner_ref": r.operation.execution_id,
            "maximum": 5,
        }
    )
    configure(r, doc)


def retry(r, **extra):
    return r.operation.dispatch(
        r.principal,
        "al_command",
        {
            "arguments": ["retry", "al-engineer", "--reason", "Inspected the genuine failure."],
            **extra,
        },
    )


@pytest.mark.parametrize("operations", ["codex", "claude"], indirect=True)
def test_real_helpers_cover_bootstrap_blobs_and_normal_completion(
    protected_policy_runtime, monkeypatch
):
    from harness.policy import control_qualification

    qualification_errors = []
    prepare = control_qualification._prepare

    def observed_prepare(*args, **kwargs):
        try:
            return prepare(*args, **kwargs)
        except ValueError:
            qualification_errors.append(traceback.format_exc())
            raise

    monkeypatch.setattr(control_qualification, "_prepare", observed_prepare)
    r = protected_policy_runtime
    repo = Path(r.operation.envelope["repository"])
    canary = "opaque-helper-content-73119"
    (repo / "tracked.txt").write_text(canary)
    git = "/Library/Developer/CommandLineTools/usr/bin/git"
    for args in (
        ["add", "tracked.txt"],
        [
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "tracked fixture",
        ],
    ):
        subprocess.run([git, *args], cwd=repo, check=True, capture_output=True)
    setup_retry(r)
    try:
        result = retry(r)
    except ValueError:
        pytest.fail("Real public retry refused qualification:\n" + "\n".join(qualification_errors))
    assert result["returncode"] == 0, result
    helpers = [
        dict(row) for row in r.controller.conn.execute("SELECT * FROM policy_control_helpers")
    ]
    assert {"repository_identity", "top", "scope_identity", "index", "tree", "read_blobs"} <= {
        row["method"] for row in helpers
    }
    assert all(row["state"] == "closed" and row["pid"] > 0 for row in helpers)
    receipt = r.controller.conn.execute("SELECT * FROM policy_control_receipts").fetchone()
    assert receipt["state"] == "closed" and receipt["helper_count"] == len(helpers)
    binding = json.loads(receipt["qualification"])
    assert binding["main_and_helper_profiles_tested"] is True
    assert binding["helper_boundary"] == "darwin-deny-process-fork-v1"
    attempt = r.controller.conn.execute("SELECT * FROM policy_attempts").fetchone()
    assert attempt["state"] == "settled" and attempt["outcome"] == "returned"
    assert (
        r.controller.conn.execute("SELECT boundary_closed FROM policy_processes").fetchone()[0] == 1
    )
    assert all(
        row[0] == "closed"
        for row in r.controller.conn.execute("SELECT state FROM policy_resource_claims")
    )
    facts = json.dumps(helpers)
    assert canary not in facts and hashlib.sha256(canary.encode()).hexdigest() not in facts


@pytest.mark.parametrize(
    "configuration",
    [
        "[include]\npath = /private/no-executable-config\n",
        "[core]\nfsmonitor = /private/no-fsmonitor\n",
        '[filter "private"]\nsmudge = /private/no-filter\n',
        '[remote "origin"]\npromisor = true\n',
    ],
)
def test_unsafe_config_refuses_before_grant_or_launch(protected_policy_runtime, configuration):
    r = protected_policy_runtime
    setup_retry(r)
    config = Path(r.operation.envelope["repository"]) / ".git/config"
    # Each fixture uses a disposable private repository, never the user's Git config.
    config.write_text(config.read_text() + configuration)
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        retry(r)
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_control_helpers").fetchone()[0] == 0
    )
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_retry_claims").fetchone()[0] == 0
    assert not r.operation.processes.records


def test_selected_pin_source_drift_cannot_self_qualify(protected_policy_runtime):
    r = protected_policy_runtime
    setup_retry(r)
    source = (
        Path(r.operation.initialization["pin_root"]) / "payload/installer/runtime_retry_control.py"
    )
    source.write_bytes(source.read_bytes() + b"\n# temporary fixture drift\n")
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        retry(r)
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_control_helpers").fetchone()[0] == 0
    )


def test_saved_grant_without_final_receipt_remains_fenced(protected_policy_runtime, monkeypatch):
    r = protected_policy_runtime
    setup_retry(r)
    original = ControlChannel.request

    def lose_completion(channel, request):
        if request.get("method") == "complete":
            raise EOFError("private fixture final response lost")
        return original(channel, request)

    monkeypatch.setattr(ControlChannel, "request", lose_completion)
    result = retry(r)
    assert result["returncode"] != 0
    state = json.loads(r.source.read_bytes())
    assert state["retries"]["al-engineer"] == 1 and state["retry_history"][-1]["policy_grant_id"]
    attempt = r.controller.conn.execute("SELECT * FROM policy_attempts").fetchone()
    assert attempt["state"] == "unresolved" and attempt["retry_claim"]
    assert (
        r.controller.conn.execute("SELECT boundary_closed FROM policy_processes").fetchone()[0] == 0
    )
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_control_receipts").fetchone()[0] == 0
    )

    assert all(
        row[0] == "unresolved"
        for row in r.controller.conn.execute("SELECT state FROM policy_resource_claims")
    )
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        retry(r)


def test_helper_timeout_does_not_fabricate_main_boundary_closure(
    protected_policy_runtime, monkeypatch
):
    from harness.policy import control_git

    r = protected_policy_runtime
    setup_retry(r)

    capture = control_git.capture

    def timeout(process, *args, **kwargs):
        if any(str(arg).endswith("/git") for arg in process.args):
            raise subprocess.TimeoutExpired("fixed helper fixture", 0)
        return capture(process, *args, **kwargs)

    monkeypatch.setattr(control_git, "capture", timeout)
    result = retry(r)
    assert result["returncode"] != 0
    assert (
        r.controller.conn.execute("SELECT state FROM policy_control_helpers").fetchone()[0]
        == "unknown"
    )
    assert (
        r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "unresolved"
    )
    assert (
        r.controller.conn.execute("SELECT boundary_closed FROM policy_processes").fetchone()[0] == 0
    )


@pytest.mark.parametrize(
    "method,operands",
    [
        ("run", {"argv": ["git", "fetch"]}),
        ("head", {"configuration": "core.fsmonitor=bad"}),
        ("scope_identity", {"revision": "--upload-pack=bad"}),
        ("inventory_scope", {"path": "../private"}),
        ("read_blobs", {"argv": ["fetch"]}),
    ],
)
def test_broker_grammar_has_no_generic_argv_config_or_path_escape(method, operands):
    with pytest.raises(ValueError):
        argv(method, operands)


def issuer(r, *arguments):
    import os

    from harness.execution_hosts import al_argv

    result = subprocess.run(
        al_argv(
            r.operation.package["launcher"],
            r.operation.envelope["host"],
            r.operation.envelope["repository"],
            r.operation.envelope["al_session_id"],
            *arguments,
            existing_run=True,
        ),
        cwd=r.operation.envelope["repository"],
        env=dict(os.environ),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result


def active_writer_fixture(r):
    import uuid

    from harness.execution_contract import digest

    issuer(r, "spec-gate", "SPEC_READY")
    issuer(r, "classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer")
    child = str(uuid.uuid4())
    directory = r.operation.registry.native_root / "sessions/2026/11/20"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ("rollout-" + child + ".jsonl")).write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": child,
                    "session_id": child,
                    "cli_version": "0.165.0",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": r.operation.registry.root,
                                "agent_role": "al-engineer",
                            }
                        }
                    },
                },
            }
        )
        + "\n"
    )
    principal = r.operation.registry.enroll(child)
    if r.operation.envelope["host"] == "claude":
        import secrets

        from harness.claude_identity_hook import handle_hook

        registry = r.operation.registry
        context = {
            "execution_id": r.operation.execution_id,
            "epoch": r.operation.epoch_id,
            "session_id": registry.root,
            "policy_digest": registry.policy,
            "signing_key": secrets.token_hex(32),
            "registry": str(registry.directory),
            "channels": {"engineer": {"role": "al-engineer", "source": "agent"}},
        }
        observed = {"session_id": registry.root, "agent_id": child, "agent_type": "al-engineer"}
        handle_hook(observed | {"hook_event_name": "SubagentStart"}, context)
        attested = handle_hook(
            observed
            | {
                "hook_event_name": "PreToolUse",
                "tool_name": "mcp__engineer__apply_patch",
                "mcp_server": {"name": "engineer", "source": "agent"},
                "tool_input": {},
            },
            context,
        )["hookSpecificOutput"]["updatedInput"]
        principal, _, attestation = registry.resolve_claude(
            "engineer", "apply_patch", attested, context
        )
        registry.claim_attestation(attestation)

    def report(action, value):
        path = r.source.parent / "control-fixture-input.json"
        path.write_text(json.dumps(value))
        issuer(r, "integration", action, "--file", str(path))

    report(
        "task-register",
        {
            "id": "finish-work",
            "title": "Owned finish fixture",
            "files": ["release-owned"],
            "criteria": ["AC-01"],
            "dependencies": [],
        },
    )
    issuer(r, "phase", "IMPLEMENTATION")
    report(
        "agent-register",
        {
            "id": child,
            "name": "Observed fixture engineer",
            "role": "al-engineer",
            "task": "finish-work",
        },
    )
    report("agent-update", {"id": child, "status": "Running"})
    issuer(r, "integration", "task-start", "--task", "finish-work")
    issuer(
        r,
        "writer",
        "start",
        "--id",
        "owned-writer",
        "--role",
        "al-engineer",
        "--files",
        "release-owned",
        "--task",
        "finish-work",
        "--agent",
        child,
    )
    state = json.loads(r.source.read_bytes())
    revision = digest(
        {
            "task": "finish-work",
            "revision": state["integration"]["tasks"]["finish-work"]["revision"],
            "graph_revision": state["integration"]["graph_revision"],
            "amendments": [],
        }
    )
    r.operation.assignments[child] = {"task": "finish-work", "revision": revision}
    return principal, revision


@pytest.mark.parametrize("operations", ["codex", "claude"], indirect=True)
@pytest.mark.parametrize("outcome", ["success", "failed", "abandoned"])
def test_qualified_finish_keeps_attribution_and_prior_liability_under_exhausted_budget(
    protected_policy_runtime, outcome, monkeypatch
):
    import sqlite3

    r = protected_policy_runtime
    principal, revision = active_writer_fixture(r)
    configure(r, document(r, maximum=1))
    from harness.policy import claims

    transition = claims.transition
    faulted = []

    def fail_settlement(conn, attempt_id, state):
        row = conn.execute(
            "SELECT operation,state FROM policy_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if row["operation"] == "apply_patch" and row["state"] == "started" and state == "closed":
            faulted.append(attempt_id)
            raise sqlite3.IntegrityError("fixture settlement fault")
        return transition(conn, attempt_id, state)

    with monkeypatch.context() as patch:
        patch.setattr(claims, "transition", fail_settlement)
        with pytest.raises(sqlite3.IntegrityError):
            r.operation.dispatch(
                principal,
                "apply_patch",
                {
                    "task_id": "finish-work",
                    "expected_revision": revision,
                    "patch": (
                        "*** Begin Patch\n*** Add File: release-owned/after.txt\n"
                        "+observed contribution\n*** End Patch"
                    ),
                },
            )

    prior = r.controller.conn.execute("SELECT id,state FROM policy_attempts").fetchone()
    assert faulted == [prior["id"]]
    assert (
        Path(r.operation.envelope["repository"]) / "release-owned/after.txt"
    ).read_text() == "observed contribution\n"
    assert prior["state"] == "started"
    result = r.operation.dispatch(
        r.principal,
        "al_command",
        {"arguments": ["writer", "finish", "--id", "owned-writer", "--outcome", outcome]},
    )
    assert result["returncode"] == 0, result
    saved = json.loads(r.source.read_bytes())
    writer = saved["writers"]["owned-writer"]
    assert writer["state"] == ("FINISHED" if outcome == "success" else "UNRESOLVED")
    assert writer["outcome"] == outcome and saved["task_attribution"]["contributions"]
    assert (
        r.controller.conn.execute(
            "SELECT state FROM policy_attempts WHERE id=?", (prior["id"],)
        ).fetchone()[0]
        == prior["state"]
    )
    assert (
        r.controller.conn.execute(
            "SELECT state FROM policy_resource_claims WHERE attempt_id=?", (prior["id"],)
        ).fetchone()[0]
        != "closed"
    )
    receipt = r.controller.conn.execute("SELECT * FROM policy_control_receipts").fetchone()
    assert receipt["state"] == "closed"
    binding = json.loads(receipt["qualification"])
    assert (
        binding["profile"] == "sealed-al-writer-finish-v1"
        and binding["writer_id"] == "owned-writer"
    )
    assert binding["outcome"] == outcome
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (1, 0)
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_retry_claims").fetchone()[0] == 0
    # Observed contribution is not stable dependency evidence while producer liability remains.
    from harness.policy.claims import claim, require_clear

    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        require_clear(
            r.controller.conn,
            [
                claim(
                    r.operation._policy_repository,
                    "release-owned/after.txt",
                    "read_dependency",
                    execution=r.operation.execution_id,
                )
            ],
        )


@pytest.mark.parametrize("position", ["startup", "service_prelude"])
def test_modified_managed_startup_cannot_run_before_the_private_guard(
    protected_policy_runtime, position
):
    r = protected_policy_runtime
    setup_retry(r)
    launcher = Path(r.operation.package["launcher"])
    canary = launcher.parent / "pre-guard-executed"
    raw = launcher.read_text()
    anchor = "' '''\n" if position == "startup" else "if len(sys.argv)>2 and sys.argv[1]=='run'"
    assert anchor in raw
    payload = "open(" + repr(str(canary)) + ",'w').write('bad')\n"
    replacement = anchor + payload if position == "startup" else payload + anchor
    launcher.write_text(raw.replace(anchor, replacement, 1))
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        retry(r)
    assert not canary.exists()


def test_sealed_helper_environment_does_not_inherit_execution_overrides(
    protected_policy_runtime, monkeypatch
):
    from harness.policy import control_channel

    r = protected_policy_runtime
    setup_retry(r)
    for name in (
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "DYLD_INSERT_LIBRARIES",
    ):
        monkeypatch.setenv(name, "untrusted-environment-canary")
    original = control_channel.operation_popen
    seen = []

    def launch(profile, command, **kwargs):
        assert all(
            name not in kwargs["env"]
            for name in (
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_KEY_0",
                "GIT_CONFIG_VALUE_0",
                "DYLD_INSERT_LIBRARIES",
            )
        )
        assert "(deny process-fork)" in Path(profile.policy).read_text()
        seen.append(profile.policy)
        return original(profile, command, **kwargs)

    monkeypatch.setattr(control_channel, "operation_popen", launch)
    assert retry(r)["returncode"] == 0
    assert seen


def test_tested_main_profile_drift_refuses_before_grant(protected_policy_runtime, monkeypatch):
    from harness.policy import control_qualification

    r = protected_policy_runtime
    setup_retry(r)
    original = control_qualification.prepare

    def drift(*args):
        qualified = original(*args)
        policy = Path(qualified.main_isolation.policy)
        policy.write_bytes(policy.read_bytes() + b"\n# fixture identity drift\n")
        return qualified

    monkeypatch.setattr(control_qualification, "prepare", drift)
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        retry(r)
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_retry_claims").fetchone()[0] == 0
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_control_helpers").fetchone()[0] == 0
    )


def test_gitlink_refuses_before_retry_grant(protected_policy_runtime):
    r = protected_policy_runtime
    setup_retry(r)
    repo = r.operation.envelope["repository"]
    git = "/Library/Developer/CommandLineTools/usr/bin/git"
    head = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=repo).strip().decode()
    subprocess.run(
        [git, "update-index", "--add", "--cacheinfo", "160000," + head + ",child"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    result = retry(r)
    assert result["returncode"] != 0
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_retry_claims").fetchone()[0] == 0
    assert (
        r.controller.conn.execute("SELECT boundary_closed FROM policy_processes").fetchone()[0] == 0
    )


def test_missing_git_blob_never_fabricates_completion(protected_policy_runtime):
    r = protected_policy_runtime
    repo = Path(r.operation.envelope["repository"])
    git = "/Library/Developer/CommandLineTools/usr/bin/git"
    (repo / "lost-blob.txt").write_text("disposable missing object fixture")
    for args in (
        ["add", "lost-blob.txt"],
        [
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture blob",
        ],
    ):
        subprocess.run([git, *args], cwd=repo, check=True, capture_output=True)
    setup_retry(r)
    oid = (
        subprocess.check_output([git, "rev-parse", "HEAD:lost-blob.txt"], cwd=repo).strip().decode()
    )
    (repo / ".git/objects" / oid[:2] / oid[2:]).unlink()
    result = retry(r)
    assert result["returncode"] != 0
    assert (
        r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "unresolved"
    )
    assert (
        r.controller.conn.execute("SELECT boundary_closed FROM policy_processes").fetchone()[0] == 0
    )
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_control_receipts").fetchone()[0] == 0
    )


@pytest.mark.parametrize("failure", ["source_inventory_mismatch", "launcher_bytes_mismatch"])
def test_qualification_diagnostic_reaches_real_mcp_and_audit(
    protected_policy_runtime, monkeypatch, failure
):
    from harness.execution_mcp import MCPConnection

    r = protected_policy_runtime
    setup_retry(r)
    effects = []
    monkeypatch.setattr(r.operation, "_dispatch", lambda *args: effects.append(True))
    manifest = Path(r.operation.initialization["pin_root"]) / "manifest.json"
    original = manifest.read_bytes()
    launcher = Path(r.operation.package["launcher"])
    launcher_original = launcher.read_bytes()
    protected_before = r.source.read_bytes()
    # Change and restore only the disposable fixture; all earlier predicates run.
    try:
        if failure == "source_inventory_mismatch":
            value = json.loads(original)
            del value["complete_file_manifest"]["installer/runtime_retry_control.py"]
            manifest.write_text(json.dumps(value))
        else:
            launcher.write_bytes(launcher_original + b"\n# controlled launcher drift\n")
        mcp = MCPConnection(lambda meta: r.principal, r.operation.dispatch)
        mcp.request({"jsonrpc": "2.0", "method": "initialize"})
        result = mcp.request(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "al_command",
                    "arguments": {
                        "arguments": ["retry", "al-engineer", "--reason", "private-reason-canary"],
                    },
                },
            }
        )
    finally:
        manifest.write_bytes(original)
        launcher.write_bytes(launcher_original)
    assert result["isError"] is True
    text = result["content"][0]["text"]
    code, encoded = text.split(" ", 1)
    diagnostic = json.loads(encoded)
    assert code == "UNSUPPORTED_ENFORCEMENT_CAPABILITY" and len(text) < 512
    assert diagnostic["cause"] == failure
    observation = r.controller.conn.execute(
        "SELECT * FROM reliability_observations WHERE observation_id=?",
        (diagnostic["observation_id"],),
    ).fetchone()
    assert observation["code"] == "DISPATCH_REJECTED"
    audit = r.controller.conn.execute(
        "SELECT outcome FROM operation_audit WHERE id=?", (observation["source_id"],)
    ).fetchone()
    assert json.loads(audit["outcome"])["diagnostic"] == diagnostic
    assert not effects and not r.operation.processes.records
    for table in (
        "policy_attempts",
        "policy_retry_claims",
        "policy_control_helpers",
        "policy_control_receipts",
        "policy_reservations",
        "policy_resource_claims",
    ):
        assert r.controller.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    assert r.source.read_bytes() == protected_before
    persisted = text + audit["outcome"]
    for private in ("private-reason-canary", str(manifest), str(r.operation.package["launcher"])):
        assert private not in persisted
