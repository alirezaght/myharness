# ruff: noqa: F811
"""Real successor authority and supervisor startup across historical package records."""

import json
import os
import sys
from pathlib import Path

import pytest

from al.tests.runtime_run_helpers import launched  # noqa: F401
from harness.execution_contract import canonical, digest
from harness.execution_hosts import package_fingerprint
from harness.execution_runtime_recovery import activate_runtime, prepare_runtime
from harness.execution_store import bind_host, get_execution
from harness.execution_worker import supervise
from tests.test_template_classification_integration import operations, registered  # noqa: F401

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="genuine macOS successor start")


@pytest.mark.parametrize("focus", [None, "tooling", "verification"])
def test_successor_supervise_ignores_legacy_package_but_preserves_current_epoch_guards(
    registered, monkeypatch, tmp_path, focus
):
    _, _, controller, _, installed, execution, _ = registered[0]
    conn, eid = controller.conn, execution["id"]
    native_id = registered[0][0].registry.root
    bind_host(conn, eid, native_id, "test-owner")
    prepared = prepare_runtime(
        controller,
        eid,
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
        request_id="worker-successor",
        reason="Repair exact fixture runtime and preserve the existing native conversation",
    )
    active = activate_runtime(
        controller,
        eid,
        request_id="worker-successor",
        preparation_sha256=prepared["preparation_sha256"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
    )
    epoch = active["epoch_id"]
    legacy_package = canonical({"sha256": digest("previous installed fixture package")})
    conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,package,created_at,updated_at,epoch_id) "
        "VALUES ('legacy',?,'legacy-token','start','Exited',?,'now','now','legacy-0')",
        (eid, legacy_package),
    )
    # A local installed-native double crosses the real Popen/stream boundary. It
    # consumes the continuation prompt and emits only session facts, without model traffic.
    provider = tmp_path / "codex"
    provider.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.164.0');sys.exit()\n"
        "prompt=sys.stdin.buffer.read()\n"
        f"open({str(tmp_path / 'prompt.txt')!r}, 'wb').write(prompt)\n"
        f"print(json.dumps({{'type':'thread.started','thread_id':{native_id!r}}}))\n"
    )
    provider.chmod(0o700)
    package = dict(
        installed, executable=str(provider), sha256=package_fingerprint(installed["package"])
    )
    monkeypatch.setattr("harness.execution_worker.installed_package", lambda *args: package)

    def reserve(token):
        conn.execute(
            "UPDATE executions SET status='WorkerOwned',worker_token=?,error=NULL WHERE id=?",
            (token, eid),
        )
        conn.execute(
            "INSERT INTO execution_attempts "
            "(id,execution_id,token,mode,status,created_at,updated_at,epoch_id) "
            "VALUES (?,?,?,'resume','Reserved','now','now',?)",
            (token, eid, token, epoch),
        )

    reserve("successor-worker")
    supervise(conn, eid, "successor-worker", "resume", focus)
    prompt = (tmp_path / "prompt.txt").read_text()
    if focus:
        assert (
            "tooling recovery" if focus == "tooling" else "retrying pending verification"
        ) in prompt
    assert "Harness MCP tools" in prompt
    current = get_execution(conn, eid)
    attempt = dict(
        conn.execute("SELECT * FROM execution_attempts WHERE token='successor-worker'").fetchone()
    )
    assert current["epoch_id"] == epoch and current["native_id"] == native_id
    assert current["status"] == "Interrupted" and "no guarded AL" in current["error"]
    assert attempt["status"] == "Exited" and attempt["child_pid"] is not None
    from harness.execution_handoff import read_handoff
    from harness.execution_initialization import initialization_identity

    protected = initialization_identity(current, read_handoff(current["handoff_path"]))
    pinned = Path(protected["pin_root"]) / "payload/codex/skills/al"
    selected_package = dict(
        package,
        package=str(pinned),
        script=str(pinned / "scripts/al_state.py"),
        sha256=package_fingerprint(pinned),
    )
    assert json.loads(attempt["package"]) == selected_package
    assert (
        conn.execute("SELECT package FROM execution_attempts WHERE id='legacy'").fetchone()[0]
        == legacy_package
    )

    # Current-epoch selected-pin history remains authoritative; global installation
    # metadata is not the runtime package selected by protected initialization.
    conn.execute(
        "UPDATE execution_attempts SET package=? WHERE token='successor-worker'",
        (canonical(dict(selected_package, sha256=digest("unexpected selected-pin drift"))),),
    )
    reserve("drift-worker")
    supervise(conn, eid, "drift-worker", "resume")
    assert "installed AL package changed" in get_execution(conn, eid)["error"]
    assert (
        conn.execute(
            "SELECT child_pid,status FROM execution_attempts WHERE token='drift-worker'"
        ).fetchone()[0]
        is None
    )

    conn.execute(
        "UPDATE execution_attempts SET package=? WHERE token='successor-worker'",
        (canonical(selected_package),),
    )
    # The process-settlement fence deliberately spans epochs; a live old child still blocks.
    monkeypatch.setattr("harness.execution_worker.installed_package", lambda *args: package)
    conn.execute(
        "UPDATE execution_attempts SET child_pid=?,status='Running' WHERE id='legacy'",
        (os.getpid(),),
    )
    reserve("live-legacy-worker")
    supervise(conn, eid, "live-legacy-worker", "resume")
    assert "live or unreconciled" in get_execution(conn, eid)["error"]
    assert (
        conn.execute(
            "SELECT child_pid FROM execution_attempts WHERE token='live-legacy-worker'"
        ).fetchone()[0]
        is None
    )
