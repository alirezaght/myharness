"""Curated runtimes and genuine isolated dependency setup preserve native boundaries."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.execution_contract import digest
from harness.execution_isolation import (
    operation_popen,
    prepare_operation_isolation,
    provisioning_profile,
)
from harness.execution_processes import ProcessManager
from harness.task_environment import SetupReport, context_revision, prepare
from harness.task_tooling import TaskTooling


def test_runtime_path_is_curated_and_credentials_are_not_forwarded(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("STAGING_SECRET", "DO_NOT_FORWARD")
    tooling = TaskTooling(tmp_path / "tooling", root, {"epoch": "one"})
    env = tooling.environment(root, tmp_path)
    assert env["PATH"] == str(tooling.bin) + os.pathsep + os.defpath
    assert "STAGING_SECRET" not in env and env["HOME"] == "/nonexistent"
    process = subprocess.run(
        [str(tooling.bin / "python"), "--version"], cwd=root, env=env, capture_output=True
    )
    assert process.returncode == 0 and b"Python 3." in process.stdout
    assert tooling.inspect()["setup_is_test_evidence"] is False
    (tooling.bin / "python").unlink()
    (tooling.bin / "python").symlink_to("/bin/echo")
    with pytest.raises(ValueError, match="executable changed"):
        tooling.environment(root, tmp_path)


def test_prepared_dependencies_reject_changed_locks_and_map_base_sources(tmp_path):
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    lock = root / "app/uv.lock"
    lock.write_text("locked")
    tooling = TaskTooling(tmp_path / "tooling", root, {"epoch": "one"})
    record = {
        "kind": "python",
        "inputs": {"app/uv.lock": digest(lock.read_bytes())},
        "environment": str(tmp_path / "venv"),
        "sources": [str(root / "app/src")],
        "tools": digest(tooling.catalog()["tools"]),
    }
    tooling.ready("app", record)
    base = tmp_path / "base"
    (base / "app").mkdir(parents=True)
    (base / "app/uv.lock").write_text("locked")
    assert tooling.environment(root / "app", tmp_path, source_root=base)["PYTHONPATH"] == str(
        base / "app/src"
    )
    (base / "app/uv.lock").write_text("different base lock")
    with pytest.raises(ValueError, match="stale"):
        tooling.environment(root / "app", tmp_path, source_root=base)
    lock.write_text("changed")
    with pytest.raises(ValueError, match="stale"):
        tooling.environment(root / "app", tmp_path)
    # Setup can repair stale dependencies without inheriting the stale environment.
    assert "VIRTUAL_ENV" not in tooling.environment(root / "app", tmp_path, prepared=False)


def test_setup_failure_is_not_test_failure_or_ready_environment(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    tooling = TaskTooling(tmp_path / "tooling", root, {})
    report = SetupReport(tooling, ".", {"inputs": {}})
    report.consume("stderr", b"dependency server unavailable")
    result = report.publish(1)
    assert result["status"] == "Unavailable" and result["test_evidence"] is False
    assert not (tooling.directory / "environments.json").exists()


def test_genuine_frozen_node_setup_never_runs_repository_hooks(tmp_path):
    import shutil

    if sys.platform != "darwin" or not shutil.which("pnpm"):
        pytest.skip("local macOS pnpm integration")
    root = tmp_path / "repo"
    app = root / "app"
    app.mkdir(parents=True)
    version = subprocess.check_output([shutil.which("pnpm"), "--version"], text=True).strip()
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "setup-root",
                "private": True,
                "packageManager": "pnpm@" + version,
                "dependencies": {"setup-app": "workspace:*"},
                "scripts": {"postinstall": "node -e 'process.exit(99)'"},
            }
        )
    )
    (app / "package.json").write_text(
        json.dumps(
            {
                "name": "setup-app",
                "private": True,
                "scripts": {"postinstall": "node -e 'process.exit(98)'"},
            }
        )
    )
    (root / "pnpm-workspace.yaml").write_text("packages:\n  - app\n")
    (root / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [shutil.which("pnpm"), "install", "--lockfile-only", "--offline", "--ignore-scripts"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    original = {p: p.read_bytes() for p in root.rglob("package.json")}
    tooling = TaskTooling(tmp_path / "tooling", root, {})
    directory = tmp_path / "operations"
    directory.mkdir()
    manager = ProcessManager()
    operation = SimpleNamespace(
        tooling=tooling,
        envelope={"repository": str(root)},
        directory=directory,
        processes=manager,
        setup_pending={},
        profile=lambda writable: prepare_operation_isolation(
            root,
            writable=writable,
            protected=[tooling.directory],
            hidden=[],
            directory=directory / os.urandom(8).hex(),
        ),
    )
    state = {
        "amendments": [],
        "integration": {
            "graph_revision": 1,
            "tasks": {"T": {"revision": 1, "files": ["app"]}},
        },
    }
    try:
        result = prepare(
            operation,
            None,
            {"role": "orchestrator", "thread_id": "root"},
            state,
            {
                "operation": "prepare",
                "cwd": "app",
                "task_id": "T",
                "expected_revision": context_revision(state, "T"),
            },
        )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            observed = manager.poll("root", result["handle"])
            if observed["status"] != "Running":
                break
            time.sleep(0.05)
        assert observed["returncode"] == 0, (observed.get("result") or {}).get("output", observed)
        assert observed["result"]["status"] == "Ready"
        assert observed["result"]["test_evidence"] is False
        assert all(path.read_bytes() == raw for path, raw in original.items())
    finally:
        manager.close()


def test_genuine_private_locked_python_setup(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("local macOS sandbox integration")
    import shutil

    if not shutil.which("uv"):
        pytest.skip("uv unavailable")
    root = tmp_path / "repo"
    app = root / "app"
    app.mkdir(parents=True)
    (app / "pyproject.toml").write_text(
        '[project]\nname="setup-fixture"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n'
    )
    (app / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.11"\n\n'
        '[[package]]\nname = "setup-fixture"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
    )
    (root / ".env").write_text("SECRET=DO_NOT_READ")
    tooling = TaskTooling(tmp_path / "tooling", root, {})
    manager = ProcessManager()
    directory = tmp_path / "operations"
    directory.mkdir()

    def profile(writable):
        return prepare_operation_isolation(
            root,
            writable=writable,
            protected=[tooling.directory],
            hidden=[],
            directory=directory / os.urandom(8).hex(),
        )

    operation = SimpleNamespace(
        tooling=tooling,
        envelope={"repository": str(root)},
        directory=directory,
        processes=manager,
        setup_pending={},
        profile=profile,
    )
    state = {
        "amendments": [],
        "integration": {"graph_revision": 1, "tasks": {"T": {"revision": 1, "files": ["app"]}}},
    }
    principal = {"role": "orchestrator", "thread_id": "root"}
    try:
        result = prepare(
            operation,
            None,
            principal,
            state,
            {
                "operation": "prepare",
                "cwd": "app",
                "task_id": "T",
                "expected_revision": context_revision(state, "T"),
            },
        )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            observed = manager.poll("root", result["handle"])
            if observed["status"] != "Running":
                break
            time.sleep(0.05)
        assert observed["returncode"] == 0 and observed["result"]["status"] == "Ready", observed
        env = tooling.environment(app, tmp_path)
        assert Path(env["VIRTUAL_ENV"]).is_relative_to(directory)
        assert not (app / ".venv").exists() and env["UV_NO_SYNC"] == "1"
        assert observed["result"]["test_evidence"] is False
        # A pnpm script starts in its unit with the outer workspace environment.
        # Both nested Python and uv must still select this unit's private venv.
        normal = profile([])
        outer = tooling.environment(root, normal.temporary)
        for argv in (
            [str(tooling.bin / "python"), "-c", "import sys;print(sys.prefix)"],
            [str(tooling.bin / "uv"), "sync", "--frozen"],
            [str(tooling.bin / "uv"), "run", "python", "-c", "import sys;print(sys.prefix)"],
        ):
            process = operation_popen(
                normal, argv, cwd=app, env=outer, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            output, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
            if "sync" not in argv:
                assert str(env["VIRTUAL_ENV"]).encode() in output
    finally:
        manager.close()


def test_installer_network_does_not_grant_source_or_secret_access(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("local macOS sandbox integration")
    root = tmp_path / "repo"
    root.mkdir()
    secret = root / ".env"
    secret.write_text("private")
    source = root / "source.txt"
    source.write_text("unchanged")
    profile = prepare_operation_isolation(
        root, writable=[], protected=[], hidden=[], directory=tmp_path / "op"
    )
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        code = (
            "import socket,sys;from pathlib import Path\n"
            f"for p,mode in [({str(secret)!r},'rb'),({str(source)!r},'wb')]:\n"
            " try:\n  with open(p,mode): pass\n except PermissionError: pass\n"
            " else: raise SystemExit('unauthorized filesystem access')\n"
            f"s=socket.socket();s.connect(('127.0.0.1',{port}));s.close()\n"
        )
        generic = operation_popen(
            profile,
            [sys.executable, "-c", code],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, error = generic.communicate(timeout=10)
        assert generic.returncode != 0 and b"PermissionError" in error
        installer = operation_popen(
            provisioning_profile(profile),
            [sys.executable, "-c", code],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, error = installer.communicate(timeout=10)
        assert installer.returncode == 0, error
    assert source.read_text() == "unchanged" and secret.read_text() == "private"


def test_setup_manifest_read_never_blocks_on_fifo_or_follows_alias(tmp_path):
    from harness.task_environment import ordinary

    root = tmp_path / "repo"
    root.mkdir()
    os.mkfifo(root / "uv.lock")
    with pytest.raises(ValueError):
        ordinary(root, "uv.lock")
    (root / "uv.lock").unlink()
    outside = tmp_path / "outside"
    outside.write_text("private")
    (root / "uv.lock").symlink_to(outside)
    with pytest.raises(ValueError):
        ordinary(root, "uv.lock")


def test_registry_setup_rejects_local_secrets_and_executable_git_dependencies(tmp_path):
    from harness.setup_manifests import node_lock, node_manifest, python_lock

    node_lock(b"settings:\n  excludeLinksFromLockfile: false\nget-caller-file: 2.0.5\n")
    node_manifest('{"dependencies":{"pkg":"~1.2.3"}}', tmp_path, "package.json")

    for version in (
        "/private/credentials",
        "../other",
        "file:../secret",
        "git+ssh://host/repo",
        "owner/repository",
    ):
        with pytest.raises(ValueError):
            node_manifest(json.dumps({"dependencies": {"pkg": version}}), tmp_path, "package.json")
    with pytest.raises(ValueError):
        node_lock(b"packages:\n  pkg:\n    resolution: {tarball: file:///private/secret}\n")
    with pytest.raises(ValueError):
        python_lock(
            {
                "package": [
                    {
                        "source": {"registry": "https://pypi.org/simple"},
                        "wheels": [{"url": "file:///private/secret"}],
                    }
                ]
            }
        )


def test_failed_setup_is_cached_without_becoming_ready(tmp_path):
    root = tmp_path / "repo"
    app = root / "app"
    app.mkdir(parents=True)
    (app / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1.0"\n')
    (app / "uv.lock").write_text("version=1\n")
    tooling = TaskTooling(tmp_path / "tooling", root, {})
    catalog = tooling.catalog()
    inputs = {
        f"app/{name}": digest((app / name).read_bytes()) for name in ("pyproject.toml", "uv.lock")
    }
    fp = digest({"inputs": inputs, "tools": catalog["tools"], "capability": catalog["binding"]})
    tooling.failure("app", {"fingerprint": fp}, {"status": "Unavailable", "test_evidence": False})
    operation = SimpleNamespace(tooling=tooling, envelope={"repository": str(root)})
    state = {
        "amendments": [],
        "integration": {"graph_revision": 1, "tasks": {"T": {"revision": 1, "files": ["app"]}}},
    }
    request = {
        "operation": "prepare",
        "cwd": "app",
        "task_id": "T",
        "expected_revision": context_revision(state, "T"),
    }
    result = prepare(operation, None, {"role": "orchestrator"}, state, request)
    assert result["status"] == "Unavailable" and result["cached"] is True
    assert not (tooling.directory / "environments.json").exists()
