import json
from pathlib import Path

import pytest

from harness.execution import start_execution
from harness.execution_authority import require_execution_authority
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution


def test_exact_private_handoff_and_idempotent_reservation(controller, sample_repo, monkeypatch):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    saved = get_execution(controller.conn, eid)
    path = Path(saved["handoff_path"])
    envelope = json.loads(path.read_bytes())
    assert not path.is_relative_to(sample_repo)
    assert path.stat().st_mode & 0o777 == 0o600
    assert envelope["sealed_bytes"] == Path(envelope["approval"]["path"]).read_text()
    assert envelope["task_package"]["accepted_spec"] in envelope["sealed_bytes"]
    assert envelope["plan"]["approved"]
    assert require_execution_authority(controller, eid)["execution_id"] == eid
    retry = start_execution(controller, tid, launch=False)
    assert retry["execution"]["id"] == eid and not retry["created"]
    with pytest.raises(ExecutionConflict, match="another host"):
        start_execution(controller, tid, host="codex", launch=False)
    assert controller.get_task(tid).phase == "approval"


def test_stale_and_tampered_authority_refused(controller, sample_repo, monkeypatch):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    saved = get_execution(controller.conn, eid)
    path = Path(saved["handoff_path"])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="handoff bytes"):
        require_execution_authority(controller, eid)
    # Merely changing the stored hash cannot change the approval's sealed bytes.
    from harness.execution_contract import digest

    controller.conn.execute(
        "UPDATE executions SET handoff_hash=? WHERE id=?", (digest(path.read_bytes()), eid)
    )
    approval_path = Path(json.loads(path.read_bytes())["approval"]["path"])
    approval_path.write_text(approval_path.read_text() + "\nchanged")
    with pytest.raises(ValueError):
        require_execution_authority(controller, eid)
    with pytest.raises(ValueError):
        start_execution(controller, tid, launch=False)


def test_draft_cannot_start(controller, sample_repo, monkeypatch):
    monkeypatch.chdir(sample_repo)
    tid = controller.create_task("Draft has no execution authority", "trivial").id
    with pytest.raises(ValueError, match="no approval"):
        start_execution(controller, tid, launch=False)
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_explicit_relative_specs_survive_detached_authority_cwd(
    controller, sample_repo, monkeypatch, tmp_path
):
    import os
    import subprocess
    import sys

    from harness.approval import ApprovalGate
    from harness.cli_spec import spec_store
    from harness.execution_contract import canonical
    from tests.complete_task import settle
    from tests.execution_helpers import binding

    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(sample_repo)
    tid = controller.create_task(
        "Implement a task with explicit relative specification storage", "trivial"
    ).id
    settle(controller, tid)
    controller.advance(tid)
    store = spec_store(controller, tid, "docs/specs")
    approval, _, _ = ApprovalGate(controller, tid, store, sample_repo).approve()
    result = start_execution(controller, tid, specs="docs/specs", launch=False)
    eid = result["execution"]["id"]
    saved = get_execution(controller.conn, eid)
    other = sample_repo / "nested-client"
    other.mkdir()
    command = [
        sys.executable,
        "-m",
        "harness.execution_authority",
        "--handoff",
        saved["handoff_path"],
        "--operation",
        "start",
        "--nonce",
        "detached-request",
        "--binding",
        canonical(binding(controller.conn, eid)),
    ]
    reply = subprocess.run(
        command,
        cwd=other,
        env=dict(os.environ, PYTHONPATH=str(root)),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert reply.returncode == 0, reply.stderr
    assert json.loads(reply.stdout)["allowed"] is True
    assert Path(approval.path).is_absolute()
    monkeypatch.chdir(other)
    assert require_execution_authority(controller, eid)["execution_id"] == eid


def test_new_reservation_canonicalizes_private_runtime_alias(
    controller, sample_repo, monkeypatch, tmp_path, isolated_home
):
    alias = tmp_path / "harness-home-alias"
    isolated_home.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(isolated_home, target_is_directory=True)
    monkeypatch.setenv("HARNESS_HOME", str(alias))
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    handoff = Path(get_execution(controller.conn, eid)["handoff_path"])
    assert str(handoff) == str(handoff.resolve())
    assert handoff.is_relative_to(isolated_home.resolve())


def test_legacy_database_alias_authority_preserves_envelope(
    controller, sample_repo, monkeypatch, tmp_path, db_path
):
    import harness.execution as execution_service

    alias = tmp_path / "database-directory-alias"
    alias.symlink_to(db_path.parent, target_is_directory=True)
    # Construct an existing valid envelope whose database reference predates canonicalization.
    monkeypatch.setattr(execution_service, "database_path", lambda conn: alias / db_path.name)
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    handoff = Path(execution["handoff_path"])
    before = handoff.read_bytes()
    assert json.loads(before)["database"] != str(db_path.resolve())
    assert require_execution_authority(controller, eid)["execution_id"] == eid
    assert handoff.read_bytes() == before
