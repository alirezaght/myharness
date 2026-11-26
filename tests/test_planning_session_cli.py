"""Public caller identity and cooperative CLI contract."""

import pytest

from tests.planning_session_fixture import session as session


@pytest.mark.parametrize(
    "provider,environment,substitution",
    [("codex", {}, None), ("claude", {}, "${CLAUDE_SESSION_ID}"), ("other", {}, None)],
)
def test_unavailable_identity_is_never_generated(provider, environment, substitution):
    from harness.planning_session_identity import resolve_caller

    with pytest.raises(ValueError, match="SESSION_IDENTITY_UNAVAILABLE"):
        resolve_caller(provider, environment, substitution)


def test_capability_permissions_links_and_sibling_identity(session, tmp_path):
    import os
    import uuid
    from pathlib import Path

    from harness.planning_session_identity import read_capability

    service, receipt, caller, cap = session
    path = Path(cap["path"])
    with pytest.raises(ValueError, match="SESSION_CAPABILITY_INVALID"):
        read_capability(path, caller | {"current_thread_id": str(uuid.uuid4())})
    path.chmod(0o644)
    with pytest.raises(ValueError, match="SESSION_CAPABILITY_INVALID"):
        read_capability(path, caller)
    path.chmod(0o600)
    link = path.parent / "symlink.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="SESSION_CAPABILITY_INVALID"):
        read_capability(link, caller)
    hard = path.parent / "hardlink.json"
    os.link(path, hard)
    with pytest.raises(ValueError, match="SESSION_CAPABILITY_INVALID"):
        read_capability(hard, caller)


def test_legacy_connection_reads_but_cannot_mutate_migrated_state(session, db_path):
    import sqlite3

    service, receipt, caller, cap = session
    legacy = sqlite3.connect(db_path, autocommit=True)
    try:
        assert (
            legacy.execute("SELECT request FROM tasks WHERE id=?", (service.task_id,)).fetchone()[0]
            == "AR-2906"
        )
        with pytest.raises(
            sqlite3.Error, match="harness_session_protocol_version|harness_session_write_allowed"
        ):
            legacy.execute(
                "UPDATE tasks SET request='must not persist' WHERE id=?", (service.task_id,)
            )
        assert (
            legacy.execute("SELECT request FROM tasks WHERE id=?", (service.task_id,)).fetchone()[0]
            == "AR-2906"
        )
    finally:
        legacy.close()


def test_actual_cli_start_status_publish_and_legacy_refusal(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import uuid
    from pathlib import Path

    from harness.authoring import project_identity

    root = tmp_path / "project"
    root.mkdir()
    db = tmp_path / "state.db"
    environment = dict(
        os.environ, CODEX_SESSION_ID=str(uuid.uuid4()), CODEX_THREAD_ID=str(uuid.uuid4())
    )

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "harness.cli", "--db", str(db), *args],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    payload = {
        "schema_version": 1,
        "project": project_identity(root),
        "request_id": "actual-cli-start-001",
        "request": "AR-2906",
        "complexity": "trivial",
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload))
    started = cli(
        "session",
        "start",
        "--repo",
        str(root),
        "--file",
        str(path),
        "--provider",
        "codex",
        "--capability-dir",
        str(tmp_path / "capabilities"),
    )
    assert started.returncode == 0, started.stderr
    receipt = json.loads(started.stdout)
    status = cli("session", "status", "--repo", str(root), "--task", receipt["task"], "--context")
    assert status.returncode == 0 and json.loads(status.stdout)["session"]["mode"] == "attached"
    assert json.loads(status.stdout)["context"]["request"] == "AR-2906"
    publication = {
        "schema_version": 1,
        "project": receipt["project"],
        "task": receipt["task"],
        "attachment_id": receipt["attachment_id"],
        "epoch": receipt["epoch"],
        "request_id": "actual-cli-publish",
        "expected": json.loads(status.stdout)["base"],
        "consumes": [],
        "content": {"task_message": {"text": "I am the same invoking fixture agent."}},
    }
    path.write_text(json.dumps(publication))
    published = cli(
        "session",
        "publish",
        "--repo",
        str(root),
        "--file",
        str(path),
        "--provider",
        "codex",
        "--capability",
        receipt["capability_path"],
    )
    assert published.returncode == 0, published.stderr
    assert (
        json.loads(published.stdout)["result"]["text"]
        == publication["content"]["task_message"]["text"]
    )
    refused = cli("interview", "next", receipt["task"])
    assert refused.returncode != 0 and "SESSION_OPERATION_INCOMPATIBLE" in refused.stderr
    assert cli("show", receipt["task"]).returncode == 0


def test_cli_accepts_the_full_bounded_source_envelope(session, tmp_path, db_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from tests.planning_session_fixture import attached_payload

    bridge, receipt, caller, cap = session
    for index in range(24):
        (bridge.root / f"source_{index}.py").write_text(
            "# Actual synthetic repository source\n" + "x = 1\n" * 1200
        )
    payload = attached_payload(bridge, receipt, "large-bounded-cli-envelope", decisions=False)
    payload["content"]["source_bundle"]["attached_envelope"]["candidate"]["body"] = (
        "## Complete proposed fixture\n" + "a" * 90000
    )
    document = tmp_path / "large-publication.json"
    document.write_text(json.dumps(payload))
    assert 262144 < document.stat().st_size < 1048576
    environment = dict(
        os.environ,
        CODEX_SESSION_ID=caller["root_session_id"],
        CODEX_THREAD_ID=caller["current_thread_id"],
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness.cli",
            "--db",
            str(db_path),
            "session",
            "publish",
            "--repo",
            str(bridge.root),
            "--file",
            str(document),
            "--provider",
            "codex",
            "--capability",
            cap["path"],
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"]["origin"] == "attached"
