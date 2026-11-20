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
    principal = observed_principal(operations, registry.root, "orchestrator")
    yield operations, principal, controller, initialization, package, execution, envelope
    operations.close()


def observed_principal(operation, thread_id, role):
    """Synthetic native observation exercises the real registry, not caller-selected metadata."""
    registry = operation.registry
    try:
        thread_id = str(uuid.UUID(thread_id))
    except ValueError:
        thread_id = str(uuid.uuid5(uuid.UUID(registry.root), thread_id))
    records = registry.native_root / "sessions/2026/10/29"
    records.mkdir(parents=True, exist_ok=True)
    source = (
        "exec"
        if role == "orchestrator"
        else {"subagent": {"thread_spawn": {"parent_thread_id": registry.root, "agent_role": role}}}
    )
    (records / ("rollout-" + thread_id + ".jsonl")).write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": thread_id,
                    "session_id": thread_id,
                    "cli_version": "0.164.0",
                    "source": source,
                },
            }
        )
        + "\n"
    )
    return registry.resolve({"threadId": thread_id, "sessionId": thread_id})


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
        (
            "al_command",
            {
                "arguments": [
                    "writer",
                    "handoff",
                    "--id",
                    "absent",
                    "--b",
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
    scout = observed_principal(operation, "scout-output", "al-scout")
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
    assert result["knowledge_contribution"]["status"] == "ready"
    assert context["registered_assignment"]["status"] == "not_registered"
    assert not context["control_plane"]["al"]
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
    other = observed_principal(operation, "different-principal", "al-scout")
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
    from tests.test_agent_contracts_setup import registered_setup

    _, _, _, state = registered_setup(operations, ["setup-app"], "setup-task")

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
    inspection = operation.dispatch(principal, "environment_operation", {"operation": "inspect"})
    task = next(t for t in inspection["tasks"] if t["task_id"] == "setup-task")
    request = next(r for r in task["prepare_requests"] if r["cwd"] == "setup-app")
    assert request == {
        "operation": "prepare",
        "cwd": "setup-app",
        "task_id": "setup-task",
        "expected_revision": context_revision(state, "setup-task"),
    }
    with pytest.raises(ValueError, match="repository-relative"):
        operation.dispatch(principal, "environment_operation", request | {"cwd": str(unit)})
    result = operation.dispatch(principal, "environment_operation", request)
    with pytest.raises(ValueError, match="owned"):
        operation.dispatch(
            observed_principal(operation, "other", "al-scout"),
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
    assert observed["result"]["status"] == "Ready", json.dumps(observed, indent=2, sort_keys=True)
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
    child = observed_principal(operation, "child-A", "al-engineer")
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
            "id": "w1",
            "task": "A",
            "agent": child["thread_id"],
            "role": "al-engineer",
            "state": "ACTIVE",
            "files": ["source"],
        }
    }
    path.write_text(json.dumps(state))
    revision = digest({"task": "A", "revision": 1, "graph_revision": 1, "amendments": []})
    operation.assignments[child["thread_id"]] = {"task": "A", "revision": revision}
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


def generated_handoff_fixture(operations, child_id="handoff-child", body=None):
    """Synthetic admission fixture; handoff generation uses the actual protected AL command."""
    operation, principal, controller, initialization, _, execution, _ = operations
    child_id = observed_principal(operation, child_id, "al-engineer")["thread_id"]
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    task_id = "handoff-task"
    task = {
        "id": task_id,
        "title": "bounded fixture",
        "revision": 1,
        "status": "Running",
        "dependencies": [],
        "files": ["source"],
        "criteria": ["AC-01"],
        "progress": None,
        "registration": "fixture",
        "admission": {"revision": 1, "graph_revision": 1},
    }
    state["status"] = "RUNNING"
    state["integration"].update(tasks={task_id: task}, graph_revision=1)
    state["integration"]["agents"][child_id] = {
        "id": child_id,
        "name": "Fixture",
        "task": task_id,
        "role": "al-engineer",
        "status": "Running",
        "started_at": time.time() - 20,
        "registration": "fixture",
    }
    state["writers"] = {
        "handoff-writer": {
            "id": "handoff-writer",
            "role": "al-engineer",
            "task": task_id,
            "agent": child_id,
            "state": "ACTIVE",
            "phase": state["phase"],
            "files": ["source"],
            "commands": [],
            "started_at": time.time() - 10,
        }
    }
    state["evidence"]["plan"]["handoff-proof"] = {
        "area": "handoff-proof",
        "tier": "focused",
        "kind": "check",
        "argv": [sys.executable, "-c", "pass"],
        "scope": ["source"],
        "cwd": ".",
        "env": [],
        "sources": [],
        "preflight": None,
        "needs": [],
    }
    path.write_text(json.dumps(state))
    revision = digest({"task": task_id, "revision": 1, "graph_revision": 1, "amendments": []})
    operation.assignments[child_id] = {"task": task_id, "revision": revision}
    child = observed_principal(operation, child_id, "al-engineer")
    body = body or "ORIGINAL SPEC\n" + "bounded α🧭 text\n" * 1900 + "TAIL_SENTINEL\n"
    draft = operation.artifacts.write_artifact(principal, "handoff-body.md", body, 1, 1)
    resource = "artifact:" + draft["artifact_id"] + ":" + draft["sha256"]
    generated = operation.dispatch(
        principal,
        "al_command",
        {
            "arguments": [
                "writer",
                "handoff",
                "--id",
                "handoff-writer",
                "--body-file",
                resource,
                "--area",
                "handoff-proof",
            ]
        },
    )
    assert generated["returncode"] == 0, generated
    current = operation.state(get_execution(controller.conn, execution["id"]))
    artifact = current["writers"]["handoff-writer"]["handoffs"][-1]["artifact"]
    from harness.assignment_handoff import descriptor

    descriptor(operation, controller, child)
    return child, Path(artifact["stored_path"]).read_bytes()


def test_authenticated_generated_handoff_pages_and_current_binding(operations):
    operation, _, _, initialization, _, _, _ = operations
    child, expected = generated_handoff_fixture(operations)
    assigned = operation.dispatch(child, "await_assignment", {"timeout": 1})
    descriptor = assigned["handoff"]
    assert descriptor["status"] == "available" and descriptor["size"] == len(expected)
    resource, offset, pieces = descriptor["resource"], 0, []
    while True:
        page = operation.dispatch(
            child, "read_file", {"path": resource, "offset": offset, "limit": 10001}
        )
        assert page["sha256"] == descriptor["sha256"]
        pieces.append(page["text"].encode())
        offset = page["next_offset"]
        if not page["truncated"]:
            break
    assert b"".join(pieces) == expected
    assert b"AL MANAGED WRITER EVIDENCE END" in b"".join(pieces)
    for bad in (child | {"thread_id": "foreign"}, child | {"writable": False}):
        with pytest.raises(ValueError):
            operation.dispatch(bad, "read_file", {"path": resource})
    state_path = Path(initialization["source"])
    state = json.loads(state_path.read_bytes())
    state["evidence"]["plan"]["handoff-proof"]["argv"] = [sys.executable, "-c", "print('changed')"]
    state_path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        operation.dispatch(child, "read_file", {"path": resource, "offset": 10001})
    assert (
        operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]["status"]
        == "unavailable"
    )


def test_handoff_redacts_before_paging_and_uses_presentation_cursors(operations):
    operation, _, _, _, _, _, _ = operations
    secret = "cross_boundary_private_value"
    body = "SAFE\n" + "x" * 16290 + "\npassword=" + secret + "\nEND\n"
    child, _ = generated_handoff_fixture(operations, body=body)
    resource = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]["resource"]
    offset, chunks = 0, []
    while True:
        page = operation.dispatch(
            child, "read_file", {"path": resource, "offset": offset, "limit": 16300}
        )
        chunks.append(page["text"])
        assert page["presentation_redacted"]
        offset = page["next_offset"]
        if not page["truncated"]:
            break
    assert secret not in "".join(chunks) and "[redacted]" in "".join(chunks)


def test_mcp_supervisor_control_contract_without_product_assignment(operations):
    from harness.execution_mcp import MCPConnection

    operation, principal, controller, _, _, execution, _ = operations
    connection = MCPConnection(lambda _: principal, operation.dispatch)
    connection.request({"jsonrpc": "2.0", "method": "initialize"})

    def call(name, arguments):
        return connection.request(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )

    assignment = json.loads(call("await_assignment", {})["content"][0]["text"])
    assert assignment["status"] == "Supervisor"
    assert assignment["control_plane"]["al"] is True
    assert assignment["writable"] is False
    capabilities = json.loads(
        call("read_file", {"path": "harness:capabilities"})["content"][0]["text"]
    )
    capabilities = json.loads(capabilities["text"])
    assert capabilities["authority"]["status"] == "Supervisor"
    assert not operation.assignments
    for arguments in (["show", "--panel"], ["spec-gate", "SPEC_READY"]):
        result = call("al_command", {"arguments": arguments})
        assert not result["isError"]
        command = json.loads(result["content"][0]["text"])
        assert command["returncode"] == 0, command
    with pytest.raises(ValueError, match="writable task"):
        operation.dispatch(
            principal,
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Add File: ESCAPE\n+bad\n*** End Patch\n",
                "task_id": "unknown",
                "expected_revision": "old",
            },
        )
    assert not (Path(operation.envelope["repository"]) / "ESCAPE").exists()
    for launcher in ("alctl", "/private/launcher/alctl", "run"):
        refusal = call("al_command", {"arguments": [launcher, "--session", "foreign", "show"]})
        assert refusal["isError"] and "['show', '--panel']" in refusal["content"][0]["text"]
    inspection = json.loads(
        call("diagram_operation", {"operation": "inspect"})["content"][0]["text"]
    )
    request = inspection["publication_request"]
    assert request["expected_graph_revision"] == assignment["graph_revision"]
    assert not call("diagram_operation", request)["isError"]
    missing = request.copy()
    del missing["expected_graph_revision"]
    refusal = call("diagram_operation", missing)
    assert refusal["isError"] and "expected_graph_revision" in refusal["content"][0]["text"]
    assert "only orchestrator" not in refusal["content"][0]["text"]
    stale = request | {"expected_graph_revision": assignment["graph_revision"] + 1}
    assert "stale" in call("diagram_operation", stale)["content"][0]["text"]
    for role in ("al-architect", "al-designer"):
        child = observed_principal(operation, role, role)
        assert operation.dispatch(child, "await_assignment", {"timeout": 1})["status"] == "ReadOnly"
        with pytest.raises(ValueError, match="only orchestrator"):
            operation.dispatch(child, "diagram_operation", request)
        with pytest.raises(ValueError, match="native workers"):
            operation.dispatch(child, "al_command", {"arguments": ["spec-gate", "SPEC_READY"]})

    with pytest.raises(ValueError, match="stale"):
        operation.dispatch(principal | {"policy": "old"}, "diagram_operation", request)
    controller.conn.execute(
        "UPDATE executions SET authority_generation=authority_generation+1 WHERE id=?",
        (execution["id"],),
    )
    with pytest.raises(ValueError, match="stale_epoch"):
        operation.dispatch(principal, "diagram_operation", request)


def test_supervisor_confined_source_proof_via_mcp(operations):
    from harness.execution_mcp import MCPConnection

    operation, principal, _, _, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    raw = b"ordinary source proof\n" * 100
    (root / "proof.py").write_bytes(raw)
    connection = MCPConnection(lambda _: principal, operation.dispatch)
    connection.request({"jsonrpc": "2.0", "method": "initialize"})
    result = connection.request(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "proof.py", "limit": 19}},
        }
    )
    assert not result["isError"]
    page = json.loads(result["content"][0]["text"])
    assert page["sha256"] == digest(raw) and page["size"] == len(raw)
    assert page["page_bytes"] == page["next_offset"] == 19 and page["truncated"]
    assert principal["writable"] is False and not operation.assignments


def test_shared_fixture_keeps_one_canonical_root_through_continuity_child(operations):
    from tests.test_execution_continuity import child

    operation, principal, *_ = operations
    native = child(operation, "al-engineer")
    assert native["parent_thread_id"] == principal["thread_id"]
    assert (
        len(
            list(
                operation.registry.native_root.glob(
                    "sessions/*/*/*/*" + principal["thread_id"] + ".jsonl"
                )
            )
        )
        == 1
    )
    assert (
        operation.registry.resolve(
            {"threadId": principal["thread_id"], "sessionId": principal["session_id"]}
        )
        == principal
    )
    assert operation.dispatch(principal, "read_file", {"path": "harness:handoff"})
