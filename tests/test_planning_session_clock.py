"""Operational clock refusal must preserve identity, replay and lease authority."""

import ctypes
import errno
import json
from types import SimpleNamespace

import pytest

from tests.planning_session_fixture import ordinary_owner, request


@pytest.fixture(autouse=True)
def clear_clock_cache():
    import harness.planning_session_clock as clock

    cached = getattr(clock, "_validated_observation", clock.boot_identity)
    cached.cache_clear()
    yield
    cached.cache_clear()


def denied_boot_read(monkeypatch):
    import harness.planning_session_clock as clock

    class Denied:
        def __call__(self, *args):
            ctypes.set_errno(errno.EPERM)
            return -1

    monkeypatch.setattr(clock.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        clock.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(sysctlbyname=Denied())
    )
    cached = getattr(clock, "_validated_observation", clock.boot_identity)
    cached.cache_clear()


def successful_boot_read(monkeypatch):
    import harness.planning_session_clock as clock

    class Successful:
        def __call__(self, name, output, length, new, size):
            output._obj.seconds = 123456789
            output._obj.microseconds = 42
            length._obj.value = ctypes.sizeof(output._obj)
            return 0

    monkeypatch.setattr(
        clock.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(sysctlbyname=Successful())
    )


def test_permission_denied_public_start_refuses_before_admission_and_retries_identically(
    controller, tmp_path, monkeypatch
):
    import uuid

    from harness.authoring import project_identity
    from harness.planning_session import PlanningSessionService
    from harness.planning_session_identity import prepare_capability_artifact, resolve_caller

    root = tmp_path / "project"
    root.mkdir()
    caller = resolve_caller(
        "codex", {"CODEX_SESSION_ID": str(uuid.uuid4()), "CODEX_THREAD_ID": str(uuid.uuid4())}
    )
    document = {
        "schema_version": 1,
        "project": project_identity(root),
        "request_id": "clock-denied-start",
        "request": "Add CSV export",
        "complexity": "trivial",
    }
    cap = prepare_capability_artifact(
        {"project": document["project"], "request_id": document["request_id"]},
        caller,
        tmp_path / "capabilities",
    )
    bridge = PlanningSessionService(controller, root)
    before = controller.conn.total_changes
    denied_boot_read(monkeypatch)
    with pytest.raises(ValueError) as failed:
        bridge.start(document, caller, cap)
    assert failed.value.code == "SESSION_CLOCK_UNAVAILABLE"
    public = failed.value.public()
    assert public["reason"] == "permission_denied"
    assert "same Harness command with host permission" in public["error"]
    assert cap["secret"] not in json.dumps(public)
    assert controller.conn.total_changes == before
    for table in (
        "tasks",
        "planning_session_owners",
        "planning_session_epochs",
        "planning_session_contacts",
        "planning_session_events",
        "planning_session_receipts",
    ):
        assert controller.conn.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert bridge.task_id is None
    # Failure is not cached; reuse exactly the same artifact and request.
    successful_boot_read(monkeypatch)
    receipt = bridge.start(document, caller, cap)
    assert receipt["attachment_id"] == cap["attachment_id"]
    contact = tuple(controller.conn.execute("SELECT * FROM planning_session_contacts").fetchone())
    denied_boot_read(monkeypatch)
    assert bridge.start(document, caller, cap) == receipt
    assert (
        tuple(controller.conn.execute("SELECT * FROM planning_session_contacts").fetchone())
        == contact
    )
    assert controller.conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert (
        controller.conn.execute(
            "SELECT count(*) FROM planning_session_receipts WHERE scope LIKE 'session-start:%'"
        ).fetchone()[0]
        == 1
    )


def test_existing_owner_clock_unavailable_is_readable_but_grants_no_authority(
    controller, tmp_path, monkeypatch
):
    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    pending = request(bridge, receipt, "clock-denied-wait")
    tables = (
        "planning_session_owners",
        "planning_session_contacts",
        "planning_session_deliveries",
        "planning_session_events",
        "planning_session_event_results",
        "planning_session_handling",
    )
    before = {
        name: [tuple(row) for row in controller.conn.execute("SELECT * FROM " + name)]
        for name in tables
    }
    denied_boot_read(monkeypatch)
    status = bridge.status()
    assert (
        status["state"] == "clock_unavailable"
        and status["clockUnavailable"]["reason"] == "permission_denied"
    )
    with pytest.raises(ValueError) as failed:
        bridge.wait(pending, caller, cap, timeout_seconds=0)
    assert failed.value.code == "SESSION_CLOCK_UNAVAILABLE"
    assert {
        name: [tuple(row) for row in controller.conn.execute("SELECT * FROM " + name)]
        for name in tables
    } == before
    from tests.planning_session_fixture import new_cap

    fresh = new_cap(bridge, caller, "unavailable-reattach", tmp_path / "replacement")
    attach = {
        "schema_version": 1,
        "project": bridge.project,
        "task": bridge.task_id,
        "request_id": "unavailable-reattach",
        "expected": bridge.base(),
        "previous_epoch": receipt["epoch"],
    }
    for takeover in (False, True):
        with pytest.raises(ValueError) as failed:
            bridge.attach(attach, caller, fresh, takeover=takeover)
        assert failed.value.code == "SESSION_CLOCK_UNAVAILABLE"
    assert {
        name: [tuple(row) for row in controller.conn.execute("SELECT * FROM " + name)]
        for name in tables
    } == before


@pytest.mark.parametrize(
    "failure,expected",
    [
        (errno.EPERM, "permission_denied"),
        (errno.EACCES, "permission_denied"),
        (errno.EIO, "read_failed"),
    ],
)
def test_failed_syscall_classifies_actual_errno_without_public_values(
    monkeypatch, failure, expected
):
    import harness.planning_session_clock as clock

    class Failed:
        def __call__(self, *args):
            ctypes.set_errno(failure)
            return -1

    monkeypatch.setattr(clock.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        clock.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(sysctlbyname=Failed())
    )
    observation = clock.boot_observation()
    assert observation.identity is None and observation.reason == expected
    assert "123456789" not in str(clock.unavailable_error(observation).public())
    assert ("host permission" in str(clock.unavailable_error(observation))) == (
        expected == "permission_denied"
    )


def test_invalid_success_and_unsupported_platform_have_distinct_reasons(monkeypatch):
    import harness.planning_session_clock as clock

    monkeypatch.setattr(clock.platform, "system", lambda: "OtherOS")
    assert clock.boot_observation().reason == "unsupported_platform"
    monkeypatch.setattr(clock.platform, "system", lambda: "Darwin")

    class Invalid:
        def __call__(self, *args):
            return 0

    monkeypatch.setattr(
        clock.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(sysctlbyname=Invalid())
    )
    assert clock.boot_observation().reason == "invalid_observation"
    successful_boot_read(monkeypatch)
    assert clock.boot_observation().identity is not None
    assert "123456789" not in repr(clock.boot_observation())


def test_cli_start_permission_diagnostic_preserves_request_and_capability(
    controller, tmp_path, monkeypatch, capsys, db_path
):
    import uuid

    from harness.authoring import project_identity
    from harness.cli import main
    from harness.planning_session_identity import prepare_capability_artifact, resolve_caller

    root = tmp_path / "cli-project"
    root.mkdir()
    caller = resolve_caller(
        "codex", {"CODEX_SESSION_ID": str(uuid.uuid4()), "CODEX_THREAD_ID": str(uuid.uuid4())}
    )
    monkeypatch.setenv("CODEX_SESSION_ID", caller["root_session_id"])
    monkeypatch.setenv("CODEX_THREAD_ID", caller["current_thread_id"])
    document = {
        "schema_version": 1,
        "project": project_identity(root),
        "request_id": "cli-denied-boot-start",
        "request": "Add CSV export",
        "complexity": "trivial",
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(document))
    cap = prepare_capability_artifact(
        {"project": document["project"], "request_id": document["request_id"]},
        caller,
        tmp_path / "capabilities",
    )
    from pathlib import Path

    original_request = path.read_bytes()
    original_cap = Path(cap["path"]).read_bytes()
    args = [
        "--db",
        str(db_path),
        "session",
        "start",
        "--repo",
        str(root),
        "--provider",
        "codex",
        "--capability",
        cap["path"],
        "--file",
        str(path),
    ]
    denied_boot_read(monkeypatch)
    assert main(args) == 1
    output = capsys.readouterr()
    assert "SESSION_CLOCK_UNAVAILABLE" in output.err and "permission_denied" in output.err
    assert "same Harness command with host permission" in output.err
    assert cap["secret"] not in output.err and caller["root_session_id"] not in output.err
    successful_boot_read(monkeypatch)
    assert main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["attachment_id"] == cap["attachment_id"]
    assert path.read_bytes() == original_request and Path(cap["path"]).read_bytes() == original_cap


def test_committed_attach_replay_needs_no_clock_or_new_lease(controller, tmp_path, monkeypatch):
    from tests.planning_session_fixture import new_cap

    bridge, receipt, caller, cap = ordinary_owner(controller, tmp_path)
    fresh = new_cap(bridge, caller, "clock-safe-attach-replay", tmp_path / "replacement")
    document = {
        "schema_version": 1,
        "project": bridge.project,
        "task": bridge.task_id,
        "request_id": "clock-safe-attach-replay",
        "expected": bridge.base(),
        "previous_epoch": receipt["epoch"],
    }
    attached = bridge.attach(document, caller, fresh, takeover=True)
    contact = tuple(
        bridge.conn.execute(
            "SELECT * FROM planning_session_contacts WHERE epoch=?", (attached["epoch"],)
        ).fetchone()
    )
    before = bridge.conn.total_changes
    denied_boot_read(monkeypatch)
    assert bridge.attach(document, caller, fresh, takeover=True) == attached
    assert bridge.conn.total_changes == before
    assert (
        tuple(
            bridge.conn.execute(
                "SELECT * FROM planning_session_contacts WHERE epoch=?", (attached["epoch"],)
            ).fetchone()
        )
        == contact
    )
