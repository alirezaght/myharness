"""Pure pieces of the resident AL service (#27): client parsing, environment, launcher text."""

import os
import socket
from pathlib import Path

import pytest

from al.installer.runtime_pin_boot import CHILD, PIN_VERIFY
from al.installer.runtime_service import TAIL_MARKER, _stdlib_imports, tail_program
from al.installer.runtime_service_boot import SERVICE, program, service_name
from al.installer.runtime_service_client import CLIENT, PROTOCOL, launcher_prelude, namespace
from al.installer.source import git_env
from al.installer.validator_boot import launcher

WIRE = namespace()


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--repository", "/r", "--platform", "codex", "--session", "s", "--", "show", "--json"],
         ("/r", "codex", "s", ["show", "--json"])),
        (["--repository=/r", "--platform=claude", "show"], ("/r", "claude", None, ["show"])),
        (["--repository", "/r", "--platform", "codex", "--", "writer", "--", "x"],
         ("/r", "codex", None, ["writer", "--", "x"])),
        (["--repo", "/r", "--platform", "codex", "show"], None),  # abbreviation: argparse decides
        (["--repository", "/r", "--platform", "codex", "--existing-run", "show"], None),
        (["--repository", "/r", "--repository", "/s", "--platform", "codex", "show"], None),
        (["--repository", "/r", "--platform", "other", "show"], None),
        (["--repository", "/r", "--platform", "codex", "--"], None),
        (["--repository", "/r", "--platform", "codex", "--", "-x"], None),
        (["--platform", "codex", "show"], None),
    ],
)
def test_thin_client_recognises_only_the_plain_run_form(argv, expected):
    assert WIRE["_al_service_parse"](argv) == expected


def test_thin_client_environment_is_the_launcher_environment(monkeypatch):
    environ = {"HOME": "/h", "GIT_DIR": "/x", "GIT_CONFIG_COUNT": "1", "SSH_AUTH_SOCK": "a",
               "DYLD_X": "1", "LD_PRELOAD": "y", "PATH": "/bin", "KEEP": "1"}
    # What cli.main does before `run`: drop these prefixes, then apply git_env().
    monkeypatch.setattr(os, "environ", dict(environ))
    for key in list(os.environ):
        if key.startswith(("GIT_", "SSH_", "DYLD_", "LD_")):
            os.environ.pop(key)
    os.environ.update(git_env())
    assert WIRE["_al_service_environment"](environ) == os.environ
    assert list(WIRE["_al_service_environment"](environ)) == list(os.environ)


def test_routes_name_one_run_and_native_sessions_need_claude():
    route = WIRE["_al_service_route"]
    assert route("/r", "codex", "s", {}) == route("/r/.", "codex", "s", {})
    assert route("/r", "codex", "s", {}) != route("/r", "codex", "t", {})
    assert route("/r", "codex", None, {"CLAUDE_SESSION_ID": "n"}) is None
    assert route("/r", "claude", None, {"CLAUDE_SESSION_ID": "n"}) != route("/r", "claude", "n", {})
    assert len(service_name("/c", "p" * 64, "l" * 64)) == 32


def test_framing_carries_descriptors_and_rejects_oversized_messages(tmp_path):
    left, right = socket.socketpair()
    path = tmp_path / "f"
    path.write_text("payload")
    fd = os.open(path, os.O_RDONLY)
    try:
        WIRE["_al_service_send"](left, {"op": "run", "value": "x" * 2000}, [fd])
        message, received = WIRE["_al_service_receive"](right, fds=3)
        assert message == {"op": "run", "value": "x" * 2000}
        assert len(received) == 1 and os.read(received[0], 7) == b"payload"
        os.close(received[0])
        WIRE["_al_service_send"](left, {"big": "y" * 64})
        with pytest.raises(ValueError):
            WIRE["_al_service_receive"](right, limit=10)
    finally:
        os.close(fd)
        left.close()
        right.close()


def test_private_paths_must_be_owned_and_closed(tmp_path):
    directory = tmp_path / "d"
    directory.mkdir(mode=0o700)
    token = directory / "token"
    token.write_text("t")
    token.chmod(0o600)
    assert WIRE["_al_service_private"](str(directory), "dir")
    assert WIRE["_al_service_private"](str(token), "file")
    directory.chmod(0o755)
    assert not WIRE["_al_service_private"](str(directory), "dir")
    link = tmp_path / "link"
    link.symlink_to(token)
    assert not WIRE["_al_service_private"](str(link), "file")


def test_unreachable_service_means_fallback(tmp_path):
    root = tmp_path / "services"
    root.mkdir(mode=0o700)
    argv = ["--repository", str(tmp_path), "--platform", "codex", "--session", "s", "--", "show"]
    assert WIRE["_al_service_run"](str(root), "l", argv, {}) is None  # no route
    (root / "routes").mkdir(mode=0o700)
    route = root / "routes" / WIRE["_al_service_route"](str(tmp_path), "codex", "s", {})
    route.write_text("abc")
    route.chmod(0o600)
    assert WIRE["_al_service_run"](str(root), "l", argv, {}) is None  # no service directory
    assert WIRE["_al_service_run"](str(root), "l", argv, {"AL_SERVICE": "0"}) is None
    start = ["--repository", str(tmp_path), "--platform", "codex", "--session", "s", "--", "start"]
    assert WIRE["_al_service_run"](str(root), "l", start, {}) is None


def test_launcher_serves_run_before_self_verification(tmp_path):
    root = tmp_path / "state/registry/installer/cli/snapshots/abc"
    request = {"mode": "snapshot", "root": str(root), "interpreter": "/usr/bin/python3",
               "expected": {"snapshot_identity": "i" * 64}}
    text = launcher(request).decode()
    prelude = launcher_prelude(tmp_path / "state/registry/al-services", "i" * 64)
    assert prelude in text
    assert text.index(prelude) < text.index("captured=_boot(")
    assert CLIENT in prelude and "PROTOCOL=" + repr(PROTOCOL) in prelude
    canonical = dict(request, mode="canonical")
    assert "_al_service_run" not in launcher(canonical).decode()


def test_service_and_run_child_share_one_pin_verification():
    assert PIN_VERIFY in CHILD and PIN_VERIFY in SERVICE
    assert "request=REQUEST" not in program({"x": 1})
    tail = tail_program(CHILD)
    lines, original = tail.split("\n"), CHILD.split("\n")
    assert len(lines) == len(original)
    start = original.index(TAIL_MARKER)
    assert lines[start:] == original[start:] and not any(lines[:start])
    assert "al_state.main()" in lines[start:]
    compile(tail, "<string>", "exec")


def test_stdlib_preimports_skip_runtime_and_relative_imports():
    code = compile("import json\nimport al_store\nfrom . import x\nfrom .y import z\n"
                   "from installer.runtime_pin import verify_pin\nimport os.path\n"
                   "def f():\n    import zipfile\n", "m", "exec")
    assert _stdlib_imports([code]) == {"json", "os.path"}


def test_watcher_source_has_no_runtime_imports():
    source = (Path(__file__).resolve().parents[1] / "installer/runtime_service_watch.py").read_text()
    code = compile(source, "watch", "exec")
    assert _stdlib_imports([code]) <= {"ctypes", "ctypes.util", "json", "os", "select", "struct", "sys",
                                       "threading"}


class _Watched:
    """A Worktree whose watcher is a fake: records are fed directly, cookies always come back."""

    def __init__(self, root):
        from al.installer import runtime_service as R
        self.R = R
        self.worktree = R.Worktree(None, root, root + "/.git", root + "/.git")
        self.worktree.process = object()
        self.worktree.sync = lambda: True

    def feed(self, *records):
        import tempfile
        data = b"".join(kind + os.fsencode(path) + b"\0" for kind, path in records)
        with tempfile.TemporaryFile() as stream:
            stream.write(data)
            stream.seek(0)
            self.worktree.process = type("P", (), {"stdout": stream})()
            try:
                while stream.tell() < len(data):
                    self.worktree.readable()
            finally:
                self.worktree.process = object()


@pytest.fixture
def watched(monkeypatch):
    # The service imports al_service_cache from the pinned scripts; here from this checkout.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "claude/skills/al/scripts"))
    return _Watched("/w")


def test_change_sets_name_every_relevant_path_since_a_token(watched):
    worktree = watched.worktree
    watched.feed((b"R", "fsevents"))
    start = worktree.changes("")
    assert start["paths"] == [] and start["token"].startswith(worktree.epoch + ":")
    watched.feed((b"E", "/w/a.py"), (b"E", "/w/node_modules/x.js"), (b"E", "/w/.git/objects/ab"),
                 (b"E", "/w/b.py"), (b"E", "/w/a.py"), (b"E", "/w/.git/config"))
    since = worktree.changes(start["token"])
    # Only what can change the snapshot (or a listing) is kept: no generated directories or Git objects.
    assert since["paths"] == ["/w/.git/config", "/w/a.py", "/w/b.py"]
    assert worktree.changes(since["token"]) == {"token": since["token"], "paths": []}


def test_change_sets_refuse_unknown_or_incomplete_history(watched):
    worktree = watched.worktree
    assert worktree.changes("") is not None
    watched.feed((b"R", "fsevents"))
    token = worktree.changes("")["token"]
    epoch, number = token.split(":")
    assert worktree.changes("other:" + number) is None
    assert worktree.changes(epoch + ":999") is None
    assert worktree.changes(epoch + ":x") is None
    watched.feed((b"O", "/w"))
    assert worktree.changes(token) is None, "events may have been lost since the token"
    token = worktree.changes("")["token"]
    watched.feed(*[(b"E", "/w/f%d.py" % n) for n in range(watched.R.MAX_EVENTS + 1)])
    assert worktree.changes(token) is None, "forgotten events"
    worktree.sync = lambda: False
    assert worktree.changes("") is None, "no cookie proof"


def test_snapshot_channel_answers_trees_and_change_sets_in_order(watched):
    import json
    R = watched.R
    worktree = watched.worktree
    watched.feed((b"R", "fsevents"))
    worktree.current = lambda: "a" * 40
    service = R.Service.__new__(R.Service)
    service.pending = {}
    service.selector = type("S", (), {"unregister": lambda self, f: None})()
    parent, child = socket.socketpair()
    try:
        token = worktree.changes("")["token"]
        watched.feed((b"E", "/w/a.py"))
        # Two requests arriving together, the second one split across reads.
        child.sendall(b"?c" + token.encode())
        service.snapshot_request(parent, worktree)
        assert service.pending == {parent: b"c" + token.encode()}
        child.sendall(b"\n")
        service.snapshot_request(parent, worktree)
        lines = b""
        while lines.count(b"\n") < 2:
            lines += child.recv(4096)
        tree, answer = lines.decode().splitlines()
        assert tree == "a" * 40 and json.loads(answer)["paths"] == ["/w/a.py"]
        # An unknown token is no answer.
        child.sendall(b"cother:1\n")
        service.snapshot_request(parent, worktree)
        assert child.recv(4096) == b"-\n"
        # A malformed request ends the conversation.
        child.sendall(b"x")
        service.snapshot_request(parent, worktree)
        assert parent not in service.pending
    finally:
        parent.close()
        child.close()


def test_child_asker_parses_answers_and_refuses_other_roots():
    from al.installer import runtime_service as R
    parent, child = socket.socketpair()
    try:
        current, changes = R._asker(child, "/w")
        assert changes("/elsewhere", "") is None and current("/elsewhere") is None
        parent.sendall(b'{"paths":["/w/a.py"],"token":"e:2"}\n-\n')
        assert changes("/w", "e:1") == {"paths": ["/w/a.py"], "token": "e:2"}
        assert changes("/w", "e:1") is None
        assert parent.recv(64) == b"ce:1\nce:1\n"
        assert changes("/w", "bad\ntoken") is None
    finally:
        parent.close()
        child.close()
