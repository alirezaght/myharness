"""Disposable Phase 3 scenario through approval, detached worker and AL replay.

Only the external model-provider executable is simulated. The candidate AL
package, detached harness worker, approval, guarded AL CLI, Decision Registry,
decision adapter, outbox and SQLite projection all run for real.
"""

import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from harness.approval import ApprovalGate
from harness.controller import HarnessController
from harness.dashboard.server import DashboardServer
from harness.db import connect
from harness.execution import start_execution
from harness.execution_decisions import record_execution_decision
from harness.execution_projection import execution_view
from harness.execution_store import get_execution
from harness.spec_store import SpecStore
from scripts import smoke_phase3_native
from scripts.smoke_phase3_native import _stop_process_group
from tests.complete_task import settle
from tests.sample_repo import make_sample_repo

ROOT = Path(__file__).resolve().parents[1]
ROLE_NAMES = (
    "al-architect",
    "al-architect-fallback",
    "al-designer",
    "al-designer-fallback",
    "al-engineer",
    "al-builder",
    "al-scout",
)


def test_native_smoke_cleanup_denial_is_reported_without_losing_the_timeout(monkeypatch):
    identity = {"pid": 4321, "pgid": 4321, "started": "fixture", "command": "host"}

    def denied(pid, sig):
        assert pid == 4321
        assert sig == signal.SIGTERM
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(os, "killpg", denied)
    monkeypatch.setattr(smoke_phase3_native, "_owned_process_identity", lambda _pid: identity)

    assert _stop_process_group(4321, identity) == (
        "could not signal owned process group 4321: PermissionError: Operation not permitted"
    )


def _al(env, repo, session, *args):
    """Every AL command goes through the protected managed launcher, as a real host's would."""
    return [
        env["PHASE3_PROVIDER_ALCTL"],
        "run",
        "--repository",
        str(repo),
        "--platform",
        "claude",
        "--session",
        session,
        *(["--session-directory"] if args and args[0] == "start" else ["--existing-run"]),
        "--",
        *args,
    ]


def _bootstrap_alctl(tmp_path: Path, destination: Path, env: dict) -> Path:
    from tests.al_launcher import bootstrap_alctl

    return bootstrap_alctl(tmp_path, destination, env)


def _admit_planning(repo, env, session, directory):
    from al.tests.planning_reports import report_for

    packet = json.loads(_run(repo, env, session, "planning", "packet"))
    report = directory / "architect-planning-report.json"
    report.write_text(json.dumps(report_for(packet)), encoding="utf-8")
    saved = json.loads(_run(repo, env, session, "planning", "report", "--file", str(report)))
    _run(
        repo,
        env,
        session,
        "planning",
        "admit",
        "--file",
        saved["materialized_obligations"]["stored_path"],
    )


def _run(repo, env, session, *args, expected=0):
    result = subprocess.run(
        _al(env, repo, session, *args),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"AL {args!r} returned {result.returncode}: {result.stdout}{result.stderr}"
        )
    return result.stdout


def _install_candidate_claude(repo: Path) -> None:
    skill = repo / ".claude/skills/al"
    skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT / "al/claude/skills/al",
        skill,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    roles = repo / ".claude/agents/al"
    roles.mkdir(parents=True, exist_ok=True)
    for name in ROLE_NAMES:
        shutil.copy2(ROOT / "al/claude/agents" / f"{name}.md", roles / f"{name}.md")

    # The installed CLI is private fixture tooling, not part of the sample
    # project's implementation inventory. Keep its runtime-generated files
    # out of the candidate repo's selected/anticipated source set.
    exclude = repo / ".git/info/exclude"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    patterns = ("/.claude/skills/al/", "/.claude/agents/al/")
    missing = [pattern for pattern in patterns if pattern not in existing.splitlines()]
    if missing:
        with exclude.open("a", encoding="utf-8") as stream:
            if existing and not existing.endswith("\n"):
                stream.write("\n")
            stream.write("\n".join(missing) + "\n")


def _wait_for(predicate, timeout, message):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(message)


def _plan_scenario_gates(repo, tmp_path, env, session):
    paths = [
        "demo/export.py",
        "tests/test_export.py",
        "README.md",
        "tests/test_phase3_independent.py",
    ]
    paths_file = tmp_path / "phase3-gate-paths.json"
    paths_file.write_text(json.dumps(paths), encoding="utf-8")
    manifest = json.loads(
        _run(repo, env, session, "gates", "discover", "--paths-file", str(paths_file))
    )
    architecture = [gate for gate in manifest["gates"] if gate["category"] == "lint-imports"]
    if len(architecture) != 1 or architecture[0]["applicability"] != "APPLICABLE":
        raise AssertionError("public candidate discovery omitted the real architecture check")
    if not architecture[0].get("obligation_ids") or not architecture[0].get("execution_signature"):
        raise AssertionError("public candidate discovery omitted architecture binding provenance")
    # Pinned runs resolve the runner from the launcher's immutable pin, never the target repo.
    runner = next(
        (Path(p) for p in architecture[0]["argv"] if p.endswith("al_architecture_check.py")), None
    )
    if (
        runner is None
        or "al-run-pins" not in runner.parts
        or runner.parts[-5:-1] != ("claude", "skills", "al", "scripts")
        or repo.resolve() in runner.resolve().parents
    ):
        raise AssertionError("architecture runner was not resolved from the pinned Claude runtime")

    # This fixture has no LOC checker, hooks, contract-generation scripts or CI jobs.
    # Resolve only those independently absent obligations; the discovered architecture
    # obligation and any future real CI/check obligation remain untouched.
    absent = {
        "loc": not (repo / "scripts/check-loc.py").is_file(),
        "pre-commit": not (repo / ".husky/pre-commit").exists()
        and not (repo / ".githooks/pre-commit").exists(),
        "contracts": not (repo / "package.json").exists(),
        "ci": not (repo / ".github/workflows").exists(),
    }
    source = manifest["rules"][0] if manifest["rules"] else None
    for gate in manifest["gates"]:
        category = gate["category"]
        if category not in absent or not absent[category]:
            continue
        if category == "contracts" and (repo / "package.json").exists():
            continue
        if category == "ci" and any(
            item["category"] == "ci" and item["applicability"] == "APPLICABLE"
            for item in manifest["gates"]
        ):
            continue
        gate.update(
            applicability="NOT_APPLICABLE",
            required=False,
            argv=[],
            reason=f"Disposable fixture contains no {category} obligation.",
        )
        if source:
            gate["source"] = source
            gate["scope"] = sorted(set(gate["scope"] + [source["path"]]))

    manifest_file = tmp_path / "phase3-gates.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    _run(repo, env, session, "gates", "plan", "--file", str(manifest_file))
    _run(repo, env, session, "gates", "run", "--stage", "base")


def run_phase3_scenario(tmp_path: Path) -> dict:
    # Protected AL paths refuse symlinked ancestors (e.g. macOS /var -> /private/var).
    tmp_path = tmp_path.resolve()
    repo = make_sample_repo(tmp_path / "target")
    # The sample project must declare a real architecture check. Coverage
    # admission no longer treats a Python service without any discoverable
    # verification command as complete.
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + '\n[tool.setuptools.package-dir]\n"" = "demo"\n'
        + '\n[tool.al.architecture]\nforbidden = ["os"]\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True, capture_output=True)
    # The shared sample's jobless placeholder workflow is not a CI definition; AL
    # correctly refuses to resolve it, so this scenario declares only real checks.
    subprocess.run(
        ["git", "rm", "-q", ".github/workflows/ci.yml"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "declare architecture check",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    _install_candidate_claude(repo)
    previous_cwd = Path.cwd()
    previous_environment = dict(os.environ)
    try:
        try:
            return _run_phase3_scenario_in_repo(tmp_path, repo)
        except Exception as error:
            diagnostics = []
            for path in tmp_path.rglob("provider-error"):
                from harness.execution_logs import redact

                diagnostics.append(
                    f"{path.name}: {redact(path.read_text(encoding='utf-8')[-4096:])}"
                )
            for path in tmp_path.rglob("worker-*.log"):
                from harness.execution_logs import redact

                diagnostics.append(
                    f"{path.name}: {redact(path.read_bytes()[-4096:].decode(errors='replace'))}"
                )
            if diagnostics:
                raise AssertionError(
                    f"{error}\nProvider/worker diagnostics:\n" + "\n".join(diagnostics)
                ) from error
            raise
    finally:
        os.chdir(previous_cwd)
        os.environ.clear()
        os.environ.update(previous_environment)


def _run_phase3_scenario_in_repo(tmp_path: Path, repo: Path) -> dict:
    provider_bin = tmp_path / "provider-bin"
    provider_bin.mkdir()
    invocation_file = tmp_path / "provider-invocations"
    ready_file = tmp_path / "provider-ready"
    finished_file = tmp_path / "provider-finished"
    error_file = tmp_path / "provider-error"
    database = tmp_path / "harness.db"
    env = dict(os.environ)
    env.update(
        HARNESS_DB=str(database),
        HARNESS_HOME=str(tmp_path / "harness-home"),
        HARNESS_AUTHORITY_ROOT=str(ROOT),
        PYTHONPATH=str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
        PHASE3_PROVIDER_REPO=str(repo),
        PHASE3_PROVIDER_INVOCATIONS=str(invocation_file),
        PHASE3_PROVIDER_READY=str(ready_file),
        PHASE3_PROVIDER_FINISHED=str(finished_file),
        PHASE3_PROVIDER_ERROR=str(error_file),
    )
    env["PATH"] = str(provider_bin) + os.pathsep + env.get("PATH", "")
    env.pop("AL_RUNTIME_DIR", None)
    env["PHASE3_PROVIDER_ALCTL"] = str(_bootstrap_alctl(tmp_path, provider_bin / "alctl", env))
    launcher = provider_bin / "claude"
    launcher.write_text(
        f'#!/bin/sh\nexec {sys.executable} -m tests.phase3_fake_provider "$@"\n', encoding="utf-8"
    )
    launcher.chmod(0o755)
    if shutil.which("claude", path=env["PATH"]) != str(launcher):
        raise AssertionError(
            "the disposable fake provider is not first in the native executable path"
        )
    os.environ.update(env)
    os.chdir(repo)

    conn = connect(database)
    controller = HarnessController(conn)
    task = controller.create_task("Implement the Phase 3 execution demonstration", "trivial")
    settle(controller, task.id)
    controller.advance(task.id)
    ApprovalGate(controller, task.id, SpecStore(repo / "docs/specs" / task.id), repo).approve()
    reserved = start_execution(controller, task.id, launch=False)
    execution_id = reserved["execution"]["id"]
    saved = get_execution(conn, execution_id)
    handoff = Path(saved["handoff_path"])
    package_file = handoff.parent / "task-package.json"
    os.environ.pop("AL_RUNTIME_DIR", None)
    session = saved["al_session_id"]
    os.environ["AL_SESSION_ID"] = session
    env["AL_SESSION_ID"] = session
    _run(
        repo, env, session, "start", "--package-file", str(package_file), "--handoff", str(handoff)
    )

    _run(repo, env, session, "spec-gate", "SPEC_READY")
    _run(
        repo,
        env,
        session,
        "classify",
        "STANDARD",
        "--designer",
        "no",
        "--implementers",
        "al-engineer",
    )
    _plan_scenario_gates(repo, tmp_path, env, session)
    _admit_planning(repo, env, session, tmp_path)
    _run(repo, env, session, "phase", "IMPLEMENTATION")

    launched = start_execution(controller, task.id, host="claude")
    if launched["launch"].get("status") != "WorkerOwned":
        raise AssertionError(f"detached worker was not admitted: {launched['launch']}")

    def provider_ready():
        if error_file.exists():
            raise AssertionError(error_file.read_text(encoding="utf-8"))
        return ready_file.exists()

    try:
        _wait_for(provider_ready, 45, "fake provider did not reach its independent-task checkpoint")
    except AssertionError as error:
        current = get_execution(conn, execution_id)
        attempt = conn.execute(
            "SELECT worker_pid,child_pid,status FROM execution_attempts "
            "WHERE execution_id=? ORDER BY rowid DESC LIMIT 1",
            (execution_id,),
        ).fetchone()
        provider_count = (
            invocation_file.read_text(encoding="utf-8")
            if invocation_file.exists()
            else "not-started"
        )
        from harness.execution_logs import redact

        logs = conn.execute(
            "SELECT channel,text FROM execution_logs WHERE execution_id=? ORDER BY rowid",
            (execution_id,),
        ).fetchall()
        diagnostic_summary = [
            {"channel": row["channel"], "text": redact(row["text"])} for row in logs
        ]
        source_path = repo / ".claude/al-runtime/sessions" / session / f"{session}.json"
        source_identity = {}
        if source_path.is_file():
            source_state = json.loads(source_path.read_text(encoding="utf-8"))
            source_identity = {
                "state_root": source_state.get("identity", {}).get("root"),
                "source_handoff": source_state.get("integration", {}).get("handoff"),
                "expected_root": json.loads(handoff.read_text(encoding="utf-8")).get("repository"),
                "expected_handoff": str(handoff),
            }
        raise AssertionError(
            f"{error}; execution={current['status']}; "
            f"attempt={dict(attempt) if attempt else None}; "
            f"provider_invocations={provider_count}; error={redact(current['error'] or '')}; "
            f"diagnostic_logs={diagnostic_summary}; "
            f"al_source_identity={source_identity}"
        ) from error
    if error_file.exists():
        raise AssertionError(error_file.read_text(encoding="utf-8"))
    _wait_for(
        lambda: any(
            row["id"] == "B" and row["status"] == "Completed"
            for row in execution_view(conn, execution_id)["tasks"]
        ),
        20,
        "worker did not project the completed independent task",
    )

    decision = record_execution_decision(
        controller,
        execution_id,
        "architecture",
        blocker_revision=1,
        request_id="phase3-demo-choice",
        verdict="alternative",
        decision_text="Choose the second documented storage format for task A.",
        tasks=["A"],
        criteria=["AC-01"],
    )
    _wait_for(finished_file.exists, 45, "fake provider did not observe the AL acknowledgement")
    if error_file.exists():
        raise AssertionError(error_file.read_text(encoding="utf-8"))
    _wait_for(
        lambda: get_execution(conn, execution_id)["status"] == "Interrupted",
        20,
        "detached worker did not record the provider exit",
    )
    before_reconnect = execution_view(conn, execution_id)
    if before_reconnect["blockers"][0]["state"] != "Resolved":
        raise AssertionError("AL decision acknowledgement did not resolve the scoped blocker")
    if next(row for row in before_reconnect["tasks"] if row["id"] == "A")["status"] == "Blocked":
        raise AssertionError("the acknowledged task remained blocked")

    server = DashboardServer(database, task.id)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        client = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        request = json.dumps({"task": task.id, "execution": execution_id})
        client.request(
            "POST",
            "/api/execution/reconnect",
            body=request,
            headers={"Content-Type": "application/json", "Origin": server.url.rstrip("/")},
        )
        response = client.getresponse()
        response_body = json.loads(response.read())
        client.close()
        if response.status != 200:
            raise AssertionError(f"dashboard reconnect failed: {response.status} {response_body}")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
    _wait_for(
        lambda: get_execution(conn, execution_id)["status"] == "RecoveryRequired",
        20,
        "reconnect did not replay the committed AL source",
    )
    after_reconnect = execution_view(conn, execution_id)
    attempt_count = conn.execute(
        "SELECT COUNT(*) FROM execution_attempts WHERE execution_id=?", (execution_id,)
    ).fetchone()[0]
    provider_count = int(invocation_file.read_text(encoding="utf-8"))
    if attempt_count != 2:
        raise AssertionError(
            f"expected one native attempt and one replay-only reconnect, got {attempt_count}"
        )
    if provider_count != 1:
        raise AssertionError(f"reconnect launched the external provider {provider_count} times")
    if next(row for row in after_reconnect["tasks"] if row["id"] == "B")["status"] != "Completed":
        raise AssertionError("completed independent task B was not retained through reconnect")
    if after_reconnect["cursor"] != before_reconnect["cursor"]:
        raise AssertionError("reconnect changed the already committed event cursor")

    report = {
        "task_id": task.id,
        "execution_id": execution_id,
        "approval_version": saved["binding"]["spec_version"],
        "al_session_id": session,
        "decision_registry_id": decision["registry_id"],
        "decision_state": after_reconnect["decisions"][0]["state"],
        "blocker_state": after_reconnect["blockers"][0]["state"],
        "task_a_admitted_after_ack": True,
        "task_b_status": next(row for row in after_reconnect["tasks"] if row["id"] == "B")[
            "status"
        ],
        "event_cursor": after_reconnect["cursor"],
        "reconnect_cursor_stable": True,
        "native_host_started": provider_count == 1,
        "provider_launch_count": provider_count,
        "native_attempt_count": attempt_count,
        "provider_boundary": (
            "external Claude executable/model simulated; real harness worker and AL adapter used"
        ),
        "execution_status": after_reconnect["status"],
    }
    conn.close()
    return report


def test_real_approval_detached_worker_decision_and_reconnect(tmp_path):
    result = run_phase3_scenario(tmp_path)
    assert result["approval_version"] == 1
    assert result["decision_state"] == "Resolved"
    assert result["blocker_state"] == "Resolved"
    assert result["task_b_status"] == "Completed"
    assert result["task_a_admitted_after_ack"] is True
    assert result["reconnect_cursor_stable"] is True
    assert result["native_host_started"] is True
    assert result["native_attempt_count"] == 2
