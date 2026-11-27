"""A real pin edit during the service watcher's pre-attachment startup window."""

import io
import json
import os
import selectors
import socket
import subprocess
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from al.installer.runtime_pin import verify_pin
from al.installer.runtime_service import Service, WIRE
from al.tests.test_runtime_service import Setup, SPEC, service_identity, wait_service_exit

pytestmark = pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX service")


def _paused_pin_watcher(source, barrier, entered, backend, events):
    """Instrument only a verified private source copy, before its real backend attaches."""
    watcher = source / "installer/runtime_service_watch.py"
    original = watcher.read_text()
    anchor = "    deep, shallow, skip = request['deep'], request['shallow'], set(request['skip'])\n"
    assert original.count(anchor) == 1, "watcher startup seam changed"
    control = f'''
    if not shallow and not skip:
        # FAULT INJECTION: model a backend that does not replay pre-attachment changes.
        # Record every native emission, filter E/O only, and preserve genuine R/X bytes.
        global emit
        real_emit = emit
        def observed_emit(kind, path=''):
            trace = os.open({str(events)!r}, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                os.write(trace, (json.dumps({{'kind': kind, 'detail': path, 'forwarded': kind not in ('E', 'O')}}) + '\\n').encode())
            finally:
                os.close(trace)
            if kind in ('E', 'O'):
                return
            real_emit(kind, path)
            if kind in ('R', 'X'):
                fd = os.open({str(backend)!r}, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    os.write(fd, json.dumps({{'kind': kind, 'detail': path}}).encode())
                finally:
                    os.close(fd)
        emit = observed_emit
        fd = os.open({str(barrier)!r}, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            marker = os.open({str(entered)!r}, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.write(marker, json.dumps({{'pid': os.getpid(), 'parent': os.getppid(), 'deep': deep}}).encode())
            finally:
                os.close(marker)
            if not select.select([fd], [], [], 60)[0] or os.read(fd, 1) != b'R':
                raise OSError('private fixture watcher barrier was not released')
        finally:
            os.close(fd)
'''
    watcher.write_text(original.replace(anchor, anchor + control, 1))
    # Settle relatime before the installer's immutable source capture, as in Setup.variant users.
    info = watcher.stat()
    os.utime(watcher, ns=(info.st_mtime_ns + 10**9, info.st_mtime_ns))
    watcher.read_bytes()
    return watcher.read_bytes()


def _record(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@pytest.mark.parametrize("tamper", [True, False], ids=["tampered-pin", "pristine-pin"])
def test_pin_startup_without_event_replay_requires_currentness(tmp_path, tamper):
    setup = Setup(tmp_path / "service")
    barrier = setup.base / "watch-release"
    entered, backend = setup.base / "watch-entered.json", setup.base / "watch-backend.json"
    events = setup.base / "native-watch-events.jsonl"
    os.mkfifo(barrier, 0o600)
    release = os.open(barrier, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    member = original = mode = None
    try:
        variant = setup.variant()
        watcher_bytes = _paused_pin_watcher(variant, barrier, entered, backend, events)
        setup.bootstrap(variant)
        code, out, err = setup.run("pin-window", "start", "--spec", SPEC)
        assert code == 0, (out[-2000:], err[-2000:])
        paused = setup.wait(lambda: _record(entered))
        assert not backend.exists(), "backend attached before the controlled pin edit"
        rows = setup.wait(lambda: setup.control("status"))
        assert len(rows) == 1
        service = rows[0]
        observed = service_identity(service["pid"])
        assert paused["parent"] == observed["pid"]
        pin = setup.pin_of("pin-window")
        assert service["pin_identity"] == pin["pin_identity"]
        assert service["commands_served"] == 0
        pins = setup.home / "installer/al-run-pins/pins"
        root = pins / pin["pin_identity"]
        assert paused["deep"] == [os.path.realpath(root)]
        verified = verify_pin(pins, pin, readonly=True)
        assert verified["bytes"]["installer/runtime_service_watch.py"] == watcher_bytes
        manifest = (root / "manifest.json").read_bytes()
        assert not service["pin_ready"]
        expected = setup.run("pin-window", "show", "--json", service=False)
        assert expected[0] == 0
        assert setup.run("pin-window", "show", "--json") == expected
        initializing = setup.control("status")
        assert len(initializing) == 1 and initializing[0]["pid"] == observed["pid"]
        assert initializing[0]["commands_served"] == 0 and not initializing[0]["stopping"]
        assert not initializing[0]["pin_ready"] and not backend.exists()
        if tamper:
            member = root / "payload/codex/skills/al/scripts/al_state.py"
            original, mode = member.read_bytes(), member.stat().st_mode & 0o777
            member.chmod(0o600)
            member.write_bytes(original + b"\n# private pre-attachment tamper\n")
            member.chmod(mode)
            assert member.read_bytes() != original
            assert (root / "manifest.json").read_bytes() == manifest
            with pytest.raises(ValueError):
                verify_pin(pins, pin, readonly=True)
        os.write(release, b"R")
        ready = setup.wait(lambda: _record(backend))
        assert ready["kind"] == "R", {"backend": ready, "service": service}
        trace = [json.loads(line) for line in events.read_text().splitlines()]
        assert any(row["kind"] == "R" and row["forwarded"] for row in trace)
        assert all(not row["forwarded"] for row in trace if row["kind"] in ("E", "O"))
        if not tamper:
            rows = setup.wait_ready()
            assert len(rows) == 1 and rows[0]["pid"] == observed["pid"]
            assert not rows[0]["stopping"] and rows[0]["commands_served"] == 0
            assert verify_pin(pins, pin, readonly=True)["bytes"] == verified["bytes"]
            assert setup.run("pin-window", "show", "--json") == expected
            assert setup.control("status")[0]["commands_served"] == 1
        else:
            log = Path(service["directory"]) / "log"
            try:
                setup.wait(lambda: "failed verification" in log.read_text(), seconds=15)
            except AssertionError as exc:
                raise AssertionError({"backend": ready, "paused": paused, "service": service,
                                      "fault_injection": "native E/O recorded but not forwarded",
                                      "native_events": events.read_text(), "service_log": log.read_text()}) from exc
            wait_service_exit(observed)
            assert not setup.control("status")
    finally:
        # Release even when a prerequisite assertion fails, then settle the identity-owned
        # service and all observed same-session group children before restoring private bytes.
        try:
            os.write(release, b"R")
            setup.stop_services()
        finally:
            os.close(release)
        if member is not None and original is not None:
            member.chmod(0o600)
            member.write_bytes(original)
            member.chmod(mode)


@pytest.fixture
def pin_pipe():
    """Feed actual nonblocking pipe reads through the service's pin-record handler."""
    read, write = os.pipe()
    stream = os.fdopen(read, "rb", buffering=0)
    os.set_blocking(read, False)
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ, ("pin", None))
    service = Service.__new__(Service)
    service.selector, service.pin_watch = selector, SimpleNamespace(stdout=stream)
    service.stopping = False
    service.pin_buffer, service.pin_ready = b"", False
    verified = []
    service.verify_pin = lambda reason: verified.append(reason) or True
    control = SimpleNamespace(service=service, verified=verified, writer=write)
    try:
        yield control
    finally:
        if control.writer is not None:
            os.close(control.writer)
        stream.close()
        selector.close()


def test_pin_ready_record_requires_full_verification(pin_pipe):
    os.write(pin_pipe.writer, b"Rinotify\0")
    pin_pipe.service.pin_event()
    assert len(pin_pipe.verified) == 1, "backend readiness must close the startup validation gap"
    assert not pin_pipe.service.stopping
    assert pin_pipe.service.pin_ready


def test_unavailable_pin_monitor_stops_service_even_with_unchanged_pin(pin_pipe):
    os.write(pin_pipe.writer, b"Xwatch backend unavailable\0")
    pin_pipe.service.pin_event()
    assert pin_pipe.service.stopping, "an unmonitored pin cannot authorize resident execution"


@pytest.mark.parametrize("prefix", [b"", b"Rincomplete"], ids=["clean-eof", "partial-record-eof"])
def test_pin_monitor_eof_stops_service(pin_pipe, prefix):
    if prefix:
        os.write(pin_pipe.writer, prefix)
        pin_pipe.service.pin_event()
        assert not pin_pipe.verified
    owned = pin_pipe.service.pin_watch
    os.close(pin_pipe.writer)
    pin_pipe.writer = None
    pin_pipe.service.pin_event()
    assert pin_pipe.service.pin_watch is owned, "EOF must retain ownership until child settlement"
    assert pin_pipe.service.stopping, "losing the pin monitor must fail closed"


@pytest.mark.parametrize("kind", [b"R", b"E", b"O", b"X"])
def test_pin_records_are_applied_only_after_complete_frame(pin_pipe, kind):
    os.write(pin_pipe.writer, kind + b"part")
    pin_pipe.service.pin_event()
    assert not pin_pipe.verified, "partial records must not trigger premature verification"
    assert not pin_pipe.service.stopping
    os.write(pin_pipe.writer, b"ial\0")
    pin_pipe.service.pin_event()
    if kind == b"X":
        assert pin_pipe.service.stopping
    else:
        assert len(pin_pipe.verified) == 1, "the completed record must retain its leading kind"
        assert not pin_pipe.service.stopping


@pytest.mark.parametrize("record", [b"?unknown\0", b"\0", b"R\0"], ids=["unknown-kind", "empty-record", "empty-ready"])
def test_unknown_pin_monitor_records_cannot_authorize_execution(pin_pipe, record):
    os.write(pin_pipe.writer, record)
    pin_pipe.service.pin_event()
    assert pin_pipe.service.stopping and not pin_pipe.service.pin_ready
    assert not pin_pipe.verified


def test_run_before_pin_ready_falls_back_without_stopping_or_forking(pin_pipe):
    service = pin_pipe.service
    service.token = "private-token"
    service.request = {"launcher_identity": "verified-launcher"}
    service.memory = lambda *_: pytest.fail("initializing service reached command preparation")
    server, client = socket.socketpair()
    client.settimeout(2)
    fds = []
    try:
        fds = [os.open(os.devnull, os.O_RDWR) for _ in range(3)]
        service.dispatch(server, {"protocol": 1, "op": "run", "token": service.token,
                                  "launcher": "verified-launcher", "env": {}}, fds)
        answer = WIRE["_al_service_receive"](client)
        assert answer["event"] == "fallback"
        assert "initializ" in answer["reason"]
        assert not service.stopping and not service.pin_ready
        for fd in fds:
            with pytest.raises(OSError):
                os.fstat(fd)
        fds.clear()
    finally:
        server.close()
        client.close()
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


def test_first_request_cannot_adopt_an_unverified_manifest_identity(pin_pipe, tmp_path):
    service = pin_pipe.service
    service.base, service.pin = tmp_path, {"pin_identity": "private-pin"}
    manifest = tmp_path / "pins/private-pin/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"changed": true}\n')
    service.pin_ready, service.manifest_identity = True, None
    assert service.pin_unchanged()
    assert len(pin_pipe.verified) == 1, "first-request metadata cannot replace full verification"


def test_oversized_incomplete_pin_record_revokes_authority(pin_pipe):
    pin_pipe.service.pin_ready = True
    for _ in range(17):
        os.write(pin_pipe.writer, b"R" * 4096)
        pin_pipe.service.pin_event()
    assert pin_pipe.service.stopping and not pin_pipe.service.pin_ready
    assert not pin_pipe.verified


@pytest.mark.parametrize("failure", [PermissionError("cannot signal owned watcher"),
                                     subprocess.TimeoutExpired("owned watcher", 2)])
def test_failed_watcher_settlement_retains_handle_and_closes_streams(pin_pipe, failure):
    process = SimpleNamespace(stdout=io.BytesIO(), stdin=io.BytesIO(), kill=lambda: None)

    def wait(timeout):
        raise failure

    def cannot_signal():
        raise failure

    process.wait = wait
    if isinstance(failure, PermissionError):
        process.kill = cannot_signal
    pin_pipe.service.pin_watch = process
    pin_pipe.service.pin_ready = True
    with pytest.raises(type(failure)) as caught:
        pin_pipe.service.stop_pin_watch()
    assert caught.value is failure
    assert pin_pipe.service.pin_watch is process, "unsettled ownership must remain visible"
    assert process.stdout.closed and process.stdin.closed
    assert not pin_pipe.service.pin_ready
