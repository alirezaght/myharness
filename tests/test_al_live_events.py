"""Step 8 consumes AL service events when available and polls otherwise (#31)."""

import json
import os
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path

import pytest

from harness import execution_al_live as Live
from harness import execution_al_service as Service

STATE = {
    "run_id": "run-1",
    "status": "RUNNING",
    "phase": "IMPLEMENTATION",
    "progress_notes": [
        {
            "at": 1.0,
            "note": "parsed the spec",
            "role": "al-engineer",
            "agent": "a1",
            "done": 1,
            "total": 3,
        },
        {
            "at": 2.0,
            "note": "wrote the parser",
            "role": "al-engineer",
            "agent": "a1",
            "done": 2,
            "total": 3,
        },
    ],
    "evidence": {
        "plan": {"unit": {}, "lint": {}},
        "entries": [
            {
                "id": "E1",
                "area": "unit",
                "status": "PASS",
                "action_cache": {
                    "origin": {"run": "run-0", "entry": "E7"},
                    "tree": "a" * 40,
                    "key": "k",
                },
            },
            {"id": "E2", "area": "lint", "status": "RUNNING"},
        ],
    },
    "agent_questions": [
        {
            "id": "Q1",
            "kind": "blocking",
            "state": "open",
            "question": "Which port?",
            "agent": "a1",
            "at": 3.0,
        },
        {
            "id": "Q2",
            "kind": "non-blocking",
            "state": "answered",
            "question": "Name?",
            "answer": "x",
            "agent": "a1",
            "forwarded": True,
        },
    ],
}


def test_live_view_carries_progress_cache_hits_snapshots_and_questions():
    view = Live.live_view(STATE)
    assert view["progress"]["done"] == 2 and view["progress"]["total"] == 3
    assert view["checks"] == {"done": 1, "total": 2}
    assert view["agents"] == [dict(STATE["progress_notes"][-1], id="a1")]
    assert view["cache_hits"] == [
        {
            "entry": "E1",
            "area": "unit",
            "status": "PASS",
            "reused_from": {"run": "run-0", "entry": "E7"},
            "tree": "a" * 40,
        }
    ]
    assert view["snapshots"] == [{"entry": "E1", "area": "unit", "tree": "a" * 40}]
    assert [q["id"] for q in view["questions"]] == ["Q1"]


def test_service_and_polling_publish_the_same_view(tmp_path):
    execution = {"handoff_path": str(tmp_path / "handoff.json")}
    by_service = Live.publish(execution, STATE, "service")
    first = Live.read(execution)
    by_polling = Live.publish(execution, STATE, "polling")
    strip = lambda v: {k: x for k, x in v.items() if k not in {"transport", "updated_at"}}  # noqa: E731
    assert strip(by_service) == strip(by_polling) == strip(first)
    assert Live.read(execution)["transport"] == "polling"
    assert Live.publish(execution, STATE, "polling") is None  # unchanged: nothing rewritten


def test_dashboard_answer_is_queued_then_delivered_through_al(tmp_path):
    execution = {"handoff_path": str(tmp_path / "handoff.json")}
    Live.publish(execution, STATE, "polling")
    with pytest.raises(ValueError, match="not open"):
        Live.queue_answer(execution, "Q2", "late")
    with pytest.raises(ValueError):
        Live.queue_answer(execution, "Q1", "")
    request = Live.queue_answer(execution, "Q1", "  8080 ")
    assert request["text"] == "8080" and request["state"] == "Queued"
    calls = []

    def run_al(argv):
        calls.append(argv)
        return (
            0,
            "AL · answered Q1\nAL · send the answer to the asking agent: Send"
            + 'Message(to="a1")\n',
            "",
        )

    delivered = Live.answer_pending(execution, run_al)
    assert calls == [["ask", "--answer", "Q1", "--text", "8080"]]
    assert delivered[0]["state"] == "Delivered" and "a1" in delivered[0]["result"]
    assert Live.answer_pending(execution, run_al) == [] and len(calls) == 1


class FakeService:
    """A private service directory speaking the AL service subscribe protocol."""

    def __init__(self):
        self.directory = Path(tempfile.mkdtemp(prefix="alsvc-", dir="/tmp")).resolve()
        self.directory.chmod(0o700)
        (self.directory / "token").write_text("secret")
        (self.directory / "token").chmod(0o600)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.directory / "s"))
        self.listener.listen()
        self.conn = None
        self.requests = []
        self.ready = threading.Event()
        threading.Thread(target=self.serve, daemon=True).start()

    def send(self, value):
        data = json.dumps(value).encode()
        self.conn.sendall(struct.pack(">I", len(data)) + data)

    def serve(self):
        try:
            self.conn, _ = self.listener.accept()
        except OSError:
            return  # closed without a client
        request = Service.receive(self.conn)
        self.requests.append(request)
        if request.get("token") == "secret" and request.get("op") == "subscribe":
            self.send({"event": "subscribed", "paths": request["paths"]})
        self.ready.set()

    def close(self):
        if self.conn is not None:
            self.conn.close()
        self.listener.close()
        for name in ("s", "token"):
            (self.directory / name).unlink(missing_ok=True)
        self.directory.rmdir()


def wait(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_subscription_delivers_events_then_falls_back_when_the_service_ends(tmp_path):
    service = FakeService()
    changed = []
    source = str(tmp_path / "state.json")
    try:
        subscription = Service.Subscription.open(service.directory, [source], changed.append)
        assert subscription is not None and subscription.active
        assert service.ready.wait(5)
        assert service.requests[0]["paths"] == [source]
        service.send({"event": "changed", "path": source, "present": True})
        service.send({"event": "changed", "path": "/elsewhere", "present": True})
        assert wait(lambda: changed == [source])
        service.conn.close()  # the service stopped: the supervisor polls again
        assert wait(lambda: not subscription.active)
    finally:
        service.close()


def test_locate_follows_the_route_of_the_run(tmp_path):
    root = tmp_path / "al-services"
    root.mkdir(mode=0o700)
    (root / "routes").mkdir(mode=0o700)
    service = FakeService()
    try:
        name = "abc123"
        link = root / name
        os.symlink(service.directory, link)
        route = root / "routes" / Service.route("/repo", "claude", "s1")
        route.write_text(name)
        route.chmod(0o600)
        assert (
            Service.locate(root, "/repo", "claude", "s1") is None
        )  # a symlinked service is refused
        link.unlink()
        service.directory.rename(link)
        service.directory = link
        assert Service.locate(root, "/repo", "claude", "s1") == link
        assert Service.locate(root, "/repo", "claude", "other") is None
    finally:
        service.close()


def test_source_events_poll_without_a_service(monkeypatch, tmp_path):
    from harness import execution_worker as Worker

    monkeypatch.setattr(Worker, "al_source", lambda execution, envelope: tmp_path / "state.json")
    events = Worker.SourceEvents(
        {"host": "claude", "al_session_id": "s"},
        {"repository": str(tmp_path)},
        {"launcher": str(tmp_path / "missing-alctl")},
        lambda _: None,
    )
    events.refresh()
    assert events.transport == "polling"
    assert events.due(time.monotonic() - 2) and not events.due(time.monotonic())
    monkeypatch.setenv("HARNESS_AL_SERVICE_EVENTS", "0")
    disabled = Worker.SourceEvents(
        {"host": "claude", "al_session_id": "s"},
        {"repository": str(tmp_path)},
        {"launcher": "x"},
        lambda _: None,
    )
    assert disabled.locator is None and disabled.transport == "polling"
