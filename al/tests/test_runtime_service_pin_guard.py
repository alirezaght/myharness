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


def test_ready_pin_without_payload_events_is_reverified(tmp_path):
    """A ready native monitor can lose E/O; cached readiness is not perpetual pin validity."""
    import time

    assert Path(sys.modules[verify_pin.__module__].__file__).resolve() == (
        Path(__file__).resolve().parents[1] / "installer/runtime_pin.py"
    )
    setup = Setup(tmp_path / "service")
    barrier = setup.base / "watch-release"
    entered, backend = setup.base / "watch-entered.json", setup.base / "watch-backend.json"
    events = setup.base / "native-watch-events.jsonl"
    os.mkfifo(barrier, 0o600)
    release = os.open(barrier, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    member = original = mode = None
    evidence = {"qualification": "post-ready event-loss fault injection; not native E9 cause proof"}
    try:
        variant = setup.variant()
        watcher_bytes = _paused_pin_watcher(variant, barrier, entered, backend, events)
        setup.bootstrap(variant)
        code, out, err = setup.run("post-ready-loss", "start", "--spec", SPEC)
        assert code == 0, (out[-2000:], err[-2000:])
        paused = setup.wait(lambda: _record(entered))
        assert not backend.exists()
        rows = setup.wait(lambda: setup.control("status"))
        assert len(rows) == 1 and not rows[0]["pin_ready"]
        service = rows[0]
        observed = service_identity(service["pid"])
        assert paused["parent"] == observed["pid"]
        evidence.update(service=service, service_identity=observed)
        os.write(release, b"R")
        ready_backend = setup.wait(lambda: _record(backend))
        assert ready_backend["kind"] == "R"
        ready = setup.wait_ready()
        assert len(ready) == 1 and ready[0]["pid"] == observed["pid"]
        assert ready[0]["commands_served"] == 0 and not ready[0]["stopping"]
        pin = setup.pin_of("post-ready-loss")
        assert ready[0]["pin_identity"] == pin["pin_identity"]
        pins = setup.home / "installer/al-run-pins/pins"
        root = pins / pin["pin_identity"]
        started = time.monotonic()
        verified = verify_pin(pins, pin, readonly=True)
        evidence["pristine_full_verification"] = {
            "elapsed_seconds": time.monotonic() - started,
            "file_count": len(verified["bytes"]),
            "byte_volume": sum(len(raw) for raw in verified["bytes"].values()),
            "implementation": "uncached runtime_pin.verify_pin in test process",
        }
        assert verified["bytes"]["installer/runtime_service_watch.py"] == watcher_bytes
        manifest = (root / "manifest.json").read_bytes()
        member = root / "payload/codex/skills/al/scripts/al_state.py"
        original, mode = member.read_bytes(), member.stat().st_mode & 0o777
        assert original == verified["bytes"]["codex/skills/al/scripts/al_state.py"]
        member.chmod(0o600)
        member.write_bytes(original + b"\n# private post-ready event-loss tamper\n")
        member.chmod(mode)
        assert member.read_bytes() != original
        assert (root / "manifest.json").read_bytes() == manifest
        with pytest.raises(ValueError):
            verify_pin(pins, pin, readonly=True)
        evidence["real_tamper_and_uncached_rejection"] = True
        log = Path(service["directory"]) / "log"
        try:
            setup.wait(lambda: "failed verification" in log.read_text(), seconds=15)
        except AssertionError as exc:
            evidence.update(
                status_at_bound=setup.control("status"),
                failed_incarnation_log=log.read_text(),
                native_events=events.read_text(),
            )
            raise AssertionError(evidence) from exc
        wait_service_exit(observed)
        assert not setup.control("status")
    finally:
        try:
            try:
                os.write(release, b"R")
                evidence["owned_services_before_cleanup"] = dict(setup.owned_services)
                setup.stop_services()
                evidence["identity_owned_service_cleanup_complete"] = True
            finally:
                os.close(release)
            if member is not None and original is not None:
                member.chmod(0o600)
                member.write_bytes(original)
                member.chmod(mode)
                evidence["private_bytes_restored_after_settlement"] = (
                    member.read_bytes() == original
                )
        finally:
            evidence["native_event_trace"] = events.read_text() if events.exists() else "missing"
            (setup.base / "post-ready-pin-evidence.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )


def _installer_pin_module(monkeypatch):
    import importlib

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    module = importlib.import_module("installer.runtime_pin")
    assert (
        Path(module.__file__).resolve()
        == Path(__file__).resolve().parents[1] / "installer/runtime_pin.py"
    )
    return module


def test_full_audit_spacing_starts_at_successful_completion(pin_pipe, monkeypatch, tmp_path):
    from al.installer import runtime_service as runtime

    module = _installer_pin_module(monkeypatch)
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock.now))
    service = pin_pipe.service
    service.base, service.pin = tmp_path, {"pin_identity": "scheduling-unit"}
    service.captured = {"bytes": {"unit": b"known"}}
    service.pin_manifest_identity = lambda: ("unchanged",)
    service.pin_ready, service.pin_verify_deadline = True, 10.0
    service.verify_pin = Service.verify_pin.__get__(service)
    calls = []

    def full_verification(*args, **kwargs):
        calls.append(clock.now)
        clock.now += 2.0  # Pure scheduling input: full verification itself took time.
        return {"bytes": service.captured["bytes"]}

    monkeypatch.setattr(module, "verify_pin", full_verification)
    assert service.audit_pin_if_due()
    assert service.pin_verify_deadline == 12.0 + runtime.PIN_VERIFY_INTERVAL
    clock.now = service.pin_verify_deadline - 0.01
    assert service.audit_pin_if_due() and calls == [10.0]
    clock.now = service.pin_verify_deadline
    assert service.audit_pin_if_due() and calls == [10.0, 17.0]
    assert service.pin_verify_deadline == 19.0 + runtime.PIN_VERIFY_INTERVAL


def test_native_pin_event_verifies_even_before_scheduled_audit(pin_pipe):
    service = pin_pipe.service
    service.pin_ready, service.pin_verify_deadline = True, float("inf")
    os.write(pin_pipe.writer, b"Eprivate-payload-change\0")
    service.pin_event()
    assert pin_pipe.verified == ["a change in its pin directory"]


@pytest.mark.parametrize(
    "condition", ["initializing", "partial", "eof", "unavailable", "malformed"]
)
def test_due_audit_cannot_restore_unproven_monitor_authority(pin_pipe, condition):
    service = pin_pipe.service
    service.pin_ready, service.pin_verify_deadline = True, 0.0
    if condition == "initializing":
        service.pin_ready = False
    elif condition == "eof":
        os.close(pin_pipe.writer)
        pin_pipe.writer = None
        service.pin_event()
    else:
        record = {
            "partial": b"Eunfinished",
            "unavailable": b"Xunavailable\0",
            "malformed": b"?unknown\0",
        }[condition]
        os.write(pin_pipe.writer, record)
        service.pin_event()
    before = (service.pin_ready, service.stopping, service.pin_buffer, service.pin_watch)
    assert not service.audit_pin_if_due()
    assert not service.pin_unchanged()
    assert (service.pin_ready, service.stopping, service.pin_buffer, service.pin_watch) == before
    assert service.pin_verify_deadline == 0.0 and not pin_pipe.verified


def _measure_private_service_audits(source, trace):
    """Add bounded timing around the genuine full-verification body in a private variant."""
    path = source / "installer/runtime_service.py"
    text = path.read_text()
    start = "        from installer.runtime_pin import verify_pin\n        try:\n"
    assert text.count(start) == 1
    text = text.replace(
        start,
        "        from installer.runtime_pin import verify_pin\n"
        "        probe_wall, probe_cpu = time.monotonic(), time.process_time()\n        try:\n",
        1,
    )
    end = "            self.pin_ready = False\n            return False\n        return True\n\n    def audit_pin_if_due"
    assert text.count(end) == 1
    observation = f"""
        finally:
            count = getattr(self, '_private_pin_probe_count', 0)
            if count < 32:
                self._private_pin_probe_count = count + 1
                row = {{'reason': reason, 'started': probe_wall,
                       'wall_seconds': time.monotonic() - probe_wall,
                       'cpu_seconds': time.process_time() - probe_cpu,
                       'deadline': self.pin_verify_deadline,
                       'pin_ready': self.pin_ready, 'stopping': self.stopping}}
                raw = (json.dumps(row) + '\\n').encode()
                assert len(raw) <= 4096
                fd = os.open({str(trace)!r}, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                try:
                    assert os.write(fd, raw) == len(raw)
                finally:
                    os.close(fd)
"""
    text = text.replace(
        end,
        "            self.pin_ready = False\n            return False\n"
        + observation
        + "        return True\n\n    def audit_pin_if_due",
        1,
    )
    path.write_text(text)
    info = path.stat()
    os.utime(path, ns=(info.st_mtime_ns + 10**9, info.st_mtime_ns))
    path.read_bytes()


def test_periodic_audits_keep_service_usable_and_overdue_dispatch_refuses_tamper(
    tmp_path, monkeypatch, pin_pipe
):
    import time

    _installer_pin_module(monkeypatch)
    setup = Setup(tmp_path / "service")
    barrier = setup.base / "watch-release"
    entered, backend = setup.base / "watch-entered.json", setup.base / "watch-backend.json"
    events, trace = (
        setup.base / "native-watch-events.jsonl",
        setup.base / "full-audit-timings.jsonl",
    )
    os.mkfifo(barrier, 0o600)
    release = os.open(barrier, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    member = original = mode = None
    evidence = {
        "qualification": "private service timing and dispatch controls; not CPU guarantee or E9 cause"
    }
    try:
        variant = setup.variant()
        _paused_pin_watcher(variant, barrier, entered, backend, events)
        _measure_private_service_audits(variant, trace)
        setup.bootstrap(variant)
        code, out, err = setup.run("pristine-audits", "start", "--spec", SPEC)
        assert code == 0, (out[-2000:], err[-2000:])
        setup.wait(lambda: _record(entered))
        rows = setup.wait(lambda: setup.control("status"))
        assert len(rows) == 1 and not rows[0]["pin_ready"]
        observed = service_identity(rows[0]["pid"])
        os.write(release, b"R")
        assert setup.wait(lambda: _record(backend))["kind"] == "R"
        assert setup.wait_ready()[0]["pid"] == observed["pid"]
        responses = []

        def two_scheduled_audits():
            started = time.monotonic()
            status = setup.control("status")
            assert len(status) == 1 and status[0]["pid"] == observed["pid"]
            assert status[0]["pin_ready"] and not status[0]["stopping"]
            assert len(responses) < 128
            responses.append(time.monotonic() - started)
            records = [
                json.loads(row) for row in trace.read_text().splitlines() if row.endswith("}")
            ]
            scheduled = [row for row in records if row["reason"] == "its scheduled audit"]
            return records if len(scheduled) >= 2 else None

        records = setup.wait(two_scheduled_audits, seconds=15)
        scheduled = [row for row in records if row["reason"] == "its scheduled audit"]
        assert all(not row["stopping"] and row["pin_ready"] for row in scheduled)
        before = setup.served()
        expected = setup.run("pristine-audits", "show", "--json", service=False)
        actual = setup.run("pristine-audits", "show", "--json")
        assert actual[0] == expected[0] == 0 and json.loads(actual[1]) == json.loads(expected[1])
        assert setup.served() == before + 1
        pin = setup.pin_of("pristine-audits")
        pins = setup.home / "installer/al-run-pins/pins"
        verified = verify_pin(pins, pin, readonly=True)
        evidence.update(
            audits=records,
            status_response_seconds=responses,
            file_count=len(verified["bytes"]),
            byte_volume=sum(len(raw) for raw in verified["bytes"].values()),
            measurement="Full Service.verify_pin body wall/process CPU; CLI status roundtrip wall; private instrumentation only",
        )
        service = pin_pipe.service
        service.base, service.pin, service.captured = pins.parent, pin, verified
        service.manifest_identity = service.pin_manifest_identity()
        service.pin_ready, service.pin_verify_deadline = True, 0.0
        service.verify_pin = Service.verify_pin.__get__(service)
        service.token, service.request = (
            "private-unit-token",
            {"launcher_identity": "private-unit-launcher"},
        )
        service.served, service.children = 0, {}
        service.memory = lambda *_: pytest.fail("overdue tampered pin reached command preparation")
        member = pins / pin["pin_identity"] / "payload/codex/skills/al/scripts/al_state.py"
        original, mode = member.read_bytes(), member.stat().st_mode & 0o777
        manifest_path = pins / pin["pin_identity"] / "manifest.json"
        manifest = manifest_path.read_bytes()
        member.chmod(0o600)
        member.write_bytes(original + b"\n# private overdue-dispatch tamper\n")
        member.chmod(mode)
        assert member.read_bytes() != original and manifest_path.read_bytes() == manifest
        server, client = socket.socketpair()
        client.settimeout(2)
        fds = []
        try:
            for _ in range(3):
                fds.append(os.open(os.devnull, os.O_RDWR))
            dispatched_fds = tuple(fds)
            dispatch_owned = fds
            # Dispatch owns these from invocation; finally closes only untransferred handles.
            fds = []
            service.dispatch(
                server,
                {
                    "protocol": 1,
                    "token": service.token,
                    "op": "run",
                    "launcher": service.request["launcher_identity"],
                    "env": {},
                },
                dispatch_owned,
            )
            answer = WIRE["_al_service_receive"](client)
            assert answer["event"] == "fallback" and answer["reason"] == "pinned runtime changed"
            assert service.stopping and not service.pin_ready and service.pin_verify_deadline == 0.0
            assert service.served == 0 and not service.children
            for fd in dispatched_fds:
                with pytest.raises(OSError):
                    os.fstat(fd)
            evidence["overdue_real_verifier_refused_before_publication"] = True
        finally:
            server.close()
            client.close()
            for fd in fds:
                os.close(fd)
    finally:
        try:
            try:
                os.write(release, b"R")
                setup.stop_services()
                evidence["identity_owned_service_cleanup_complete"] = True
            finally:
                os.close(release)
            if member is not None and original is not None:
                member.chmod(0o600)
                member.write_bytes(original)
                member.chmod(mode)
                evidence["private_bytes_restored_after_settlement"] = (
                    member.read_bytes() == original
                )
        finally:
            evidence["native_event_trace"] = events.read_text() if events.exists() else "missing"
            (setup.base / "pristine-audit-evidence.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
