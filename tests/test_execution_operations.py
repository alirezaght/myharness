"""Real protected initialization and operation boundaries, with synthetic native identity."""

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

from harness.execution_bootstrap import authority_snapshot
from harness.execution_contract import digest
from harness.execution_handoff import read_handoff
from harness.execution_initialization import initialize_execution
from harness.execution_operations import OperationController
from harness.execution_principals import PrincipalRegistry
from harness.execution_store import get_execution
from harness.execution_worker import replay_source
from harness.native_runtime import NativeRuntime, auth_selection, native_auth_environment
from harness.native_tool_policy import verify_native_policy
from tests.execution_helpers import approved_execution, claimed
from tests.native_model_fixture import operation_sandbox_unavailable

ORIGINAL_AUTH_LOCATIONS = {
    k: os.environ.get(k) for k in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")
}


pytestmark = pytest.mark.skipif(
    operation_sandbox_unavailable(), reason="Linux operation sandbox capability unavailable"
)


@pytest.fixture
def operations(controller, monkeypatch, launched, request):
    # Imported fixtures do not inherit this module's pytestmark.
    if operation_sandbox_unavailable():
        pytest.skip("Linux operation sandbox capability unavailable")
    source, home, repo, launcher, environment = launched
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    (home / ".codex").mkdir()
    host = getattr(request, "param", "codex")
    _, eid = approved_execution(controller, repo, monkeypatch, host=host)
    claimed(controller.conn, eid)
    execution = get_execution(controller.conn, eid)
    envelope = read_handoff(execution["handoff_path"])
    package = {
        "launcher": str(launcher),
        "package": str(source / host / "skills/al"),
        "executable": shutil.which(host),
    }
    initialization = initialize_execution(controller, execution, package, envelope)
    replay_source(controller.conn, eid, "test-owner")
    execution = get_execution(controller.conn, eid)
    registry = PrincipalRegistry(
        Path(execution["handoff_path"]).parent / "principals",
        home / ".codex",
        execution["binding"],
        "test-policy",
    )
    registry.observe_root_start(str(uuid.uuid4()))
    operations = OperationController(
        Path(envelope["database"]),
        execution,
        envelope,
        package,
        initialization,
        registry,
        Path(execution["handoff_path"]).parent / "operations",
    )
    principal = {
        "thread_id": registry.root,
        "role": "orchestrator",
        "writable": False,
        "binding": execution["binding"],
        "policy": registry.policy,
    }
    yield operations, principal, controller, initialization, package, execution, envelope
    operations.close()


@pytest.mark.native
@pytest.mark.parametrize("operations", ["codex", "claude"], indirect=True)
def test_existing_signed_in_native_start_and_resume(operations, monkeypatch):
    """Opt-in real-provider proof; no credentials are exported or setup performed."""
    if os.environ.get("HARNESS_LIVE_NATIVE") != "1":
        pytest.skip("live signed-in smoke is opt-in; synthetic boundary checks run separately")
    import subprocess
    import threading

    from harness.execution_hosts import native_diagnostic, native_identity
    from harness.execution_logs import redact
    from tests.native_model_fixture import native_executable

    operation, _, controller, initialization, package, execution, envelope = operations
    for key, value in ORIGINAL_AUTH_LOCATIONS.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    host = execution["host"]
    package["executable"] = native_executable(host)
    shutil.rmtree(operation.registry.directory)
    runtime = NativeRuntime(
        Path(envelope["database"]),
        execution,
        envelope,
        package,
        initialization,
        str(uuid.uuid4()) if host == "claude" else None,
    )
    try:
        for resume in (False, True):
            argv = runtime.argv(envelope["repository"], resume=resume)
            if host == "codex":
                argv[2:2] = ["--model", "gpt-6.1-sol"]
            proc = subprocess.Popen(
                argv,
                cwd=envelope["repository"],
                env=native_auth_environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            errors = []

            def observe(proc=proc, errors=errors):
                for line in proc.stdout:
                    try:
                        diagnostic = native_diagnostic(host, line.encode())
                        if diagnostic:
                            errors.append(redact(diagnostic))
                        identity = native_identity(host, line.encode())
                        if identity:
                            runtime.observe(identity)
                    except ValueError as error:
                        errors.append(str(error))

            thread = threading.Thread(target=observe, daemon=True)
            thread.start()
            proc.stdin.write(
                "Use the Harness read_file tool once with path harness:task-package, "
                "then reply HARNESS_SIGNIN_OK. Do not perform any other task or operation.\n"
            )
            proc.stdin.close()
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                from harness.execution_processes import ProcessManager

                ProcessManager._kill(proc)
                proc.wait(timeout=5)
                raise
            thread.join(timeout=2)
            stderr = proc.stderr.read()
            proc.stdout.close()
            proc.stderr.close()
            assert proc.returncode == 0 and not errors, (errors, redact(stderr[-2000:]))
            count = controller.conn.execute(
                "SELECT count(*) FROM operation_audit WHERE execution_id=? "
                "AND outcome NOT LIKE '%error%'",
                (execution["id"],),
            ).fetchone()[0]
            assert runtime.registry.root and count >= 1 + int(resume)
        print(host + " existing signed-in start/resume and authenticated controller read passed")
    finally:
        runtime.close()


def test_root_fixed_documents_guarded_al_and_no_unadmitted_writes(operations):
    operation, principal, _, _, _, _, _ = operations
    handoff = operation.dispatch(principal, "read_file", {"path": "harness:handoff"})
    assert json.loads(handoff["text"])["execution_id"] == operation.execution_id
    result = operation.dispatch(
        principal,
        "al_command",
        {
            "arguments": [
                "checkpoint",
                "--reason",
                "Controller integration",
                "--resume-instructions",
                "Continue",
            ]
        },
    )
    assert result["returncode"] == 0, result
    result = operation.dispatch(principal, "al_command", {"arguments": ["spec-gate", "SPEC_READY"]})
    assert result["returncode"] == 0, result
    for name, arguments in (
        (
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Add File: ESCAPE\n+bad\n*** End Patch\n",
                "task_id": "unknown",
                "expected_revision": "old",
            },
        ),
        (
            "run_command",
            {
                "argv": [sys.executable, "-c", "print('bad')"],
                "cwd": ".",
                "task_id": "unknown",
                "expected_revision": "old",
            },
        ),
        ("al_command", {"arguments": ["start", "--spec", "replacement"]}),
        (
            "al_command",
            {
                "arguments": [
                    "review-packet",
                    "--stage",
                    "fresh",
                    "--output",
                    operation.initialization["source"],
                ]
            },
        ),
        ("diagnostic", {"request": {"kind": "source", "root": "/", "path": "etc/passwd"}}),
    ):
        with pytest.raises(ValueError):
            operation.dispatch(principal, name, arguments)
    assert not (Path(operation.envelope["repository"]) / "ESCAPE").exists()


def test_guarded_check_cannot_write_the_ledger_or_repository(operations):
    operation, principal, _, initialization, _, _, _ = operations
    state = Path(initialization["source"])
    source = Path(operation.envelope["repository"]) / "UNOWNED"
    script = (
        "from pathlib import Path\n"
        + "for name in "
        + repr([str(state), str(source)])
        + ":\n"
        + ' try: Path(name).write_text("unauthorized")\n'
        + ' except (PermissionError, OSError): print("write refused")\n'
        + ' else: raise SystemExit("check escaped containment")\n'
    )
    result = operation.dispatch(
        principal,
        "al_command",
        {
            "arguments": [
                "preflight",
                "--name",
                "contained-check",
                "--",
                sys.executable,
                "-c",
                script,
            ]
        },
    )
    assert result["returncode"] == 0, result
    assert "write refused" in result["stdout"] and not source.exists()
    assert json.loads(state.read_bytes())["status"] == "RUNNING"


def test_read_only_agent_can_submit_only_a_named_report_outbox(operations):
    operation, principal, _, _, _, _, _ = operations
    scout = principal | {"thread_id": "scout-output", "role": "al-scout", "writable": False}
    context = operation.dispatch(scout, "await_assignment", {"timeout": 1})
    assert context["status"] == "ReadOnly"
    result = operation.dispatch(
        scout,
        "submit_output",
        {
            "name": "findings.json",
            "content": '{"finding":"exact final report"}',
            "expected_graph_revision": context["graph_revision"],
        },
    )
    report = operation.dispatch(
        principal,
        "read_file",
        {"path": "artifact:" + result["artifact_id"] + ":" + result["sha256"]},
    )
    assert json.loads(report["text"])["finding"] == "exact final report"
    assert (
        "findings.json"
        in operation.dispatch(principal, "read_file", {"path": "harness:outputs"})[
            scout["thread_id"]
        ]
    )
    with pytest.raises(ValueError):
        operation.dispatch(
            scout,
            "submit_output",
            {
                "name": "../source.py",
                "content": "bad",
                "expected_graph_revision": context["graph_revision"],
            },
        )


def test_owned_read_process_and_revision_rejection(operations):
    operation, principal, _, _, _, _, _ = operations
    result = operation.dispatch(
        principal,
        "run_command",
        {"argv": ["pwd"], "cwd": ".", "task_id": "none", "expected_revision": "none"},
    )
    other = principal | {"thread_id": "different-principal"}
    with pytest.raises(ValueError, match="owned"):
        operation.dispatch(other, "poll_process", {"handle": result["handle"]})
    deadline, output = time.monotonic() + 10, ""
    while time.monotonic() < deadline:
        value = operation.dispatch(principal, "poll_process", {"handle": result["handle"]})
        output += value["output"]
        if value["status"] != "Running":
            break
        time.sleep(0.05)
    assert value["status"] == "Exited" and value["returncode"] == 0, value
    assert operation.envelope["repository"] in output
    with pytest.raises(ValueError, match="stale"):
        operation.dispatch(principal | {"policy": "old"}, "read_file", {"path": "README.md"})


def test_supervisor_polls_its_scoped_setup_without_product_writer_authority(operations):
    operation, principal, _, initialization, _, _, _ = operations
    from harness.task_environment import context_revision

    root = Path(operation.envelope["repository"])
    unit = root / "setup-app"
    unit.mkdir()
    (unit / "pyproject.toml").write_text(
        '[project]\nname="setup-app"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n'
    )
    (unit / "uv.lock").write_text(
        'version=1\nrevision=3\nrequires-python=">=3.11"\n'
        '[[package]]\nname="setup-app"\nversion="0.1.0"\nsource={virtual="."}\n'
    )
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"].update(
        tasks={"setup-task": {"revision": 1, "files": ["setup-app"]}}, graph_revision=1
    )
    path.write_text(json.dumps(state))
    result = operation.dispatch(
        principal,
        "environment_operation",
        {
            "operation": "prepare",
            "cwd": "setup-app",
            "task_id": "setup-task",
            "expected_revision": context_revision(state, "setup-task"),
        },
    )
    with pytest.raises(ValueError, match="owned"):
        operation.dispatch(
            principal | {"thread_id": "other"},
            "poll_process",
            {
                "handle": result["handle"],
            },
        )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        observed = operation.dispatch(principal, "poll_process", {"handle": result["handle"]})
        if observed["status"] != "Running":
            break
        time.sleep(0.05)
    assert observed["result"]["status"] == "Ready", observed
    assert observed["result"]["test_evidence"] is False
    assert not json.loads(path.read_bytes())["writers"]
    state["integration"]["tasks"]["setup-task"]["revision"] = 2
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="revision"):
        operation.dispatch(principal, "poll_process", {"handle": result["handle"]})


def test_native_auth_selection_and_profile_tamper(operations, monkeypatch):
    operation, _, _, initialization, package, execution, envelope = operations
    from tests.native_model_fixture import native_executable

    package["executable"] = native_executable("codex")
    home = Path.home()
    config = Path(os.environ["CODEX_HOME"]) / "config.toml"
    config.write_text('cli_auth_credentials_store="keyring"\nforced_login_method="chatgpt"\n')
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-native-only")
    environment = native_auth_environment()
    assert environment["HOME"] == str(home)
    assert environment["ANTHROPIC_AUTH_TOKEN"] == "fixture-native-only"
    assert auth_selection()["cli_auth_credentials_store"] == "keyring"
    shutil.rmtree(operation.registry.directory)  # Replace this test's synthetic registry.
    runtime = NativeRuntime(
        Path(envelope["database"]), execution, envelope, package, initialization, None
    )
    try:
        argv = runtime.argv(envelope["repository"])
        assert "--ignore-user-config" in argv
        assert any('cli_auth_credentials_store="keyring"' in arg for arg in argv)
        policy_file = next(iter(runtime.policy["files"]))
        Path(policy_file).write_text("tampered")
        with pytest.raises(ValueError, match="profile changed"):
            verify_native_policy(runtime.policy)
    finally:
        runtime.close()
    # No native auth data was copied into the trusted code snapshot.
    trusted = authority_snapshot(Path(execution["handoff_path"]).parent)
    assert "fixture-native-only" not in (Path(trusted) / "manifest.json").read_text()


@pytest.mark.native
def test_actual_native_controller_reads_guarded_al_and_resume(operations):
    import subprocess
    import threading

    from tests.native_model_fixture import codex_fixture_argv, native_executable, responses_fixture

    operation, _, _, initialization, package, execution, envelope = operations
    package["executable"] = native_executable("codex")
    shutil.rmtree(operation.registry.directory)
    runtime = NativeRuntime(
        Path(envelope["database"]), execution, envelope, package, initialization, None
    )
    stages, output = [], []
    sentinel = Path(envelope["repository"]) / "ESCAPE"

    def plan(request, index):
        stages.append(index)
        return [
            ("mcp__harness.read_file", {"path": "harness:task-package"}),
            (
                "mcp__harness.al_command",
                {
                    "arguments": [
                        "checkpoint",
                        "--reason",
                        "Actual native controller",
                        "--resume-instructions",
                        "Continue exact run",
                    ]
                },
            ),
            ("functions.exec_command", {"cmd": "touch " + str(sentinel)}),
            None,
        ][min(index, 3)]

    try:
        with responses_fixture(plan) as (url, observed):

            def drive(resume=None):
                argv = codex_fixture_argv(
                    runtime.policy, url, envelope["repository"], resume=resume
                )
                argv.insert(2, "--strict-config")
                proc = subprocess.Popen(
                    argv,
                    cwd=envelope["repository"],
                    env=native_auth_environment(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                errors = []

                def observe():
                    for line in proc.stdout:
                        output.append(line)
                        value = json.loads(line)
                        if value.get("type") == "thread.started":
                            try:
                                runtime.observe(value["thread_id"])
                            except ValueError as error:
                                errors.append(str(error))

                thread = threading.Thread(target=observe, daemon=True)
                thread.start()
                proc.wait(timeout=40)
                thread.join(timeout=2)
                stderr = proc.stderr.read()
                proc.stdout.close()
                proc.stderr.close()
                assert not errors and proc.returncode == 0, (errors, stderr, output[-5:])

            drive()
            root = runtime.registry.root
            assert root and stages and not sentinel.exists()
            state = json.loads(Path(initialization["source"]).read_bytes())
            checkpoint = json.loads(Path(state["checkpoints"][-1]["stored_path"]).read_bytes())
            assert checkpoint["reason"] == "Actual native controller"
            drive(root)
            assert runtime.registry.root == root and not sentinel.exists()
            assert len(observed) >= 5
    finally:
        runtime.close()


def test_child_write_requires_its_exact_current_writer(operations):
    operation, principal, controller, initialization, _, execution, _ = operations
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    child = principal | {"thread_id": "child-A", "role": "al-engineer", "writable": True}
    state["status"] = "RUNNING"
    task = {
        "revision": 1,
        "status": "Running",
        "dependencies": [],
        "files": ["source"],
        "admission": {"revision": 1, "graph_revision": 1},
    }
    state["integration"].update(tasks={"A": task}, graph_revision=1)
    state["writers"] = {
        "w1": {
            "task": "A",
            "agent": "child-A",
            "role": "al-engineer",
            "state": "ACTIVE",
            "files": ["source"],
        }
    }
    path.write_text(json.dumps(state))
    revision = digest({"task": "A", "revision": 1, "graph_revision": 1, "amendments": []})
    operation.assignments["child-A"] = {"task": "A", "revision": revision}
    operation.admitted(controller, child, "A", revision, paths=["source/good.py"])
    for wrong, rev, files in (
        (child | {"thread_id": "child-B"}, revision, ["source/good.py"]),
        (child, "old", ["source/good.py"]),
        (child, revision, ["outside.py"]),
        (child, revision, ["source/.codex/auth.json"]),
    ):
        with pytest.raises(ValueError):
            operation.admitted(controller, wrong, "A", rev, paths=files)
    controller.conn.execute(
        "UPDATE executions SET status='Completed' WHERE id=?", (execution["id"],)
    )
    with pytest.raises(ValueError):
        operation.admitted(controller, child, "A", revision, paths=["source/good.py"])
