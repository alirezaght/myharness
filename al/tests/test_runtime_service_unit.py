"""Pure pieces of the resident AL service (#27): client parsing, environment, launcher text."""

import os
import socket
import sys
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
    import ast
    calls = [node for node in ast.walk(ast.parse(tail)) if isinstance(node, ast.Call)]
    assert any(isinstance(node.func, ast.Attribute) and node.func.attr == 'main'
               and isinstance(node.func.value, ast.Name) and node.func.value.id == 'al_state'
               for node in calls)
    assert any(isinstance(node.func, ast.Name) and node.func.id == 'complete' for node in calls)
    compile(tail, "<string>", "exec")


def test_stdlib_preimports_skip_runtime_and_relative_imports():
    code = compile("import json\nimport al_store\nfrom . import x\nfrom .y import z\n"
                   "from installer.runtime_pin import verify_pin\nimport os.path\n"
                   "def f():\n    import zipfile\n", "m", "exec")
    assert _stdlib_imports([code]) == {"json", "os.path"}


_WATCHER_IMPORTS = {"ctypes", "ctypes.util", "json", "os", "select", "struct", "sys",
                    "threading"}


def _assert_watcher_imports_allowed(source):
    import ast

    imports = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "watcher must not import relative modules"
            imports.add(node.module)
    assert imports <= _WATCHER_IMPORTS, imports - _WATCHER_IMPORTS


def test_watcher_source_has_no_runtime_imports():
    source = (Path(__file__).resolve().parents[1] / "installer/runtime_service_watch.py").read_text()
    code = compile(source, "watch", "exec")
    assert _stdlib_imports([code]) <= _WATCHER_IMPORTS
    _assert_watcher_imports_allowed(source)


@pytest.mark.parametrize("source", [
    "import al_state",
    "import installer.engine",
    "def nested():\n    import requests",
    "from . import al_state",
    "def nested():\n    from ..installer import engine",
    "import os, al_store",
])
def test_watcher_import_guard_rejects_runtime_relative_and_nested_imports(source):
    with pytest.raises(AssertionError):
        _assert_watcher_imports_allowed(source)


def test_watcher_import_guard_allows_nested_standard_library_imports():
    _assert_watcher_imports_allowed("import os\ndef nested():\n    from ctypes import util")


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


@pytest.mark.parametrize("proof", ["synchronized", "unproven", "changed"])
def test_snapshot_operation_requires_current_proof_despite_advertised_status(watched, proof):
    import time

    R = watched.R
    worktree = watched.worktree
    watched.feed((b"R", "fsevents"))
    tree = "a" * 40
    worktree.tree, worktree.tree_generation = tree, worktree.generation
    service = R.Service.__new__(R.Service)
    service.request = {"common_dir": worktree.common_dir, "launcher_identity": "fixture"}
    service.pin, service.platform = {"pin_identity": "fixture"}, "codex"
    service.started, service.last_active, service.idle = time.time(), time.monotonic(), 300
    service.served, service.children, service.stopping, service.packs = 0, {}, False, {}
    service.pin_ready = False
    service.worktrees = {worktree.root: worktree}
    located = (worktree.root, worktree.git_dir, worktree.common_dir)
    service.location = lambda repository: located if repository == worktree.root else None
    assert service.status()["worktrees"] == [
        {"root": worktree.root, "watcher": "fsevents", "snapshot": tree}]
    synchronized = []

    def sync():
        synchronized.append(proof)
        if proof == "changed":
            watched.feed((b"E", worktree.root + "/app.py"))
        return proof != "unproven"

    worktree.sync = sync
    answer = service.snapshot_answer({"repository": worktree.root})
    assert synchronized == [proof]
    assert answer == {"event": "snapshot", "tree": tree if proof == "synchronized" else None}
    if proof == "unproven":
        assert service.status()["worktrees"][0]["snapshot"] == tree
    elif proof == "changed":
        assert service.status()["worktrees"][0]["snapshot"] is None
    assert service.snapshot_answer({"repository": "/elsewhere"}) == {"event": "snapshot", "tree": None}
    assert synchronized == [proof]


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


@pytest.mark.parametrize("exception", ["PermissionError", "ValueError"])
def test_command_tail_permission_failure_has_recovery_without_replay(tmp_path, exception):
    import subprocess
    import sys

    marker = tmp_path / "executions"
    program = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from installer import runtime_service as R
service = R.Service.__new__(R.Service)
service.child_text = 'request=REQUEST'
service.tail = compile("with Path(sys.argv[2]).open('a') as stream: stream.write('once\\n')\nraise " + sys.argv[3] + "('controlled command failure')", '<string>', 'exec')
service.base = Path(sys.argv[2]).parent
service.manifest = {}
service.captured = {'bytes': {}}
service.payload = service.prefix = service.base
service.platform = 'codex'
prepared = {'request': {}, 'transaction': {}, 'run_context': {}, 'value': {}, 'functions': {}}
raise SystemExit(service.execute(prepared))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program,
         str(Path(__file__).resolve().parents[1]), str(marker), exception],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1
    assert marker.read_text() == "once\n"
    assert exception + ": controlled command failure" in result.stderr
    assert "Traceback (most recent call last)" in result.stderr
    hint = "AL resident service permission failure:"
    assert (hint in result.stderr) == (exception == "PermissionError")
    if exception == "PermissionError":
        assert "AL_SERVICE=0" in result.stderr and "normal host permission review" in result.stderr
        assert "No command was automatically replayed" in result.stderr
        assert "possible partial effects" in result.stderr


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='native Linux inotify backend')
@pytest.mark.parametrize('external_git_dir', [False, True], ids=['nested-dot-git', 'external-git-dir'])
def test_linux_worktree_observes_source_git_inputs_and_cookie(tmp_path, monkeypatch, external_git_dir):
    import selectors
    import subprocess
    import time
    from types import SimpleNamespace
    from al.installer import runtime_service as R

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'codex/skills/al/scripts'))
    root = tmp_path / 'repo'
    git_dir = tmp_path / 'git' if external_git_dir else root / '.git'
    command = ['git', 'init', '-q']
    if external_git_dir:
        command += ['--separate-git-dir', str(git_dir)]
    subprocess.run([*command, str(root)], env=git_env(), check=True,
                   capture_output=True, timeout=10)
    source = root / 'app.py';source.write_text('VALUE = 1\n')
    (git_dir / 'info').mkdir(exist_ok=True)
    exclude = git_dir / 'info/exclude';exclude.write_text('initial-excluded-file\n')
    selector = selectors.DefaultSelector()

    def forget(stream):
        try:
            selector.unregister(stream)
        except KeyError:
            pass

    worktree = R.Worktree(SimpleNamespace(selector=selector, forget=forget),
                          str(root), str(git_dir), str(git_dir))
    child = None
    try:
        worktree.start((Path(R.__file__).with_name('runtime_service_watch.py')).read_text())
        child = worktree.process
        deadline = time.monotonic() + 10
        while not worktree.ready and worktree.process is not None:
            remaining = deadline - time.monotonic()
            assert remaining > 0, 'native watcher did not become ready'
            for key, _ in selector.select(remaining):
                key.data[1].readable()
        assert worktree.ready and worktree.backend == 'inotify'
        baseline = worktree.changes('')
        assert baseline is not None  # Actual Git-directory cookie must come back.
        assert baseline['paths'] == []
        source.write_text('VALUE = 2\n')
        config = git_dir / 'config'
        config.write_text(config.read_text() + '\n# changed snapshot configuration\n')
        exclude.write_text(exclude.read_text() + '\nignored-fixture-file\n')
        ignored_object = git_dir / 'objects/ignored-fixture-object'
        ignored_object.write_text('not a snapshot input\n')
        changed = worktree.changes(baseline['token'])
        assert changed is not None
        assert changed['token'] != baseline['token']
        assert {str(source), str(config), str(exclude)} <= set(changed['paths'])
        assert str(ignored_object) not in changed['paths']
        assert not worktree.cookies
        assert worktree.sync()  # Subsequent cookies still work after delivery.
    finally:
        # This test owns exactly this Popen, including partial start failures.
        child = child if child is not None else worktree.process
        try:
            if child is not None:
                try:
                    if child.poll() is None:
                        child.kill()
                finally:
                    child.wait(timeout=5)
        finally:
            try:
                if child is not None:
                    try:
                        if child.stdin is not None:
                            child.stdin.close()
                    finally:
                        if child.stdout is not None:
                            child.stdout.close()
            finally:
                selector.close()



@pytest.fixture
def cookie_protocol(tmp_path, monkeypatch):
    """Real Worktree and owned child; control delays only actual published cookie events."""
    import json
    import select
    import selectors
    import subprocess
    import time
    from types import SimpleNamespace

    from al.installer import runtime_service as R
    from al.installer.owned_process import OwnedProcess, cleanup_barrier

    assert Path(R.__file__).resolve() == Path(__file__).resolve().parents[1] / 'installer/runtime_service.py'
    assert R.COOKIE_TIMEOUT == 2.0
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'codex/skills/al/scripts'))
    repository = tmp_path / 'repo'
    gitdir = repository / '.git'
    gitdir.mkdir(parents=True)
    app = repository / 'app.py'
    app.write_text('before\n')
    selector = selectors.DefaultSelector()

    def forget(stream):
        try:
            selector.unregister(stream)
        except (KeyError, ValueError):
            pass

    worktree = R.Worktree(SimpleNamespace(selector=selector, forget=forget),
                          str(repository), str(gitdir), str(gitdir))
    read, write = os.pipe()
    notice_read, notice_write = os.pipe()
    descriptors = {'read': read, 'write': write, 'notice_read': notice_read, 'notice_write': notice_write}
    os.set_blocking(notice_read, False)
    owner = OwnedProcess(tmp_path / 'producer-owner', 'cookie-timeout-recovery-protocol')
    producer = r"""
import json, os, sys
from pathlib import Path
control, notice = map(int, sys.argv[1:3])
def emit(kind, path=''):
    raw = kind.encode() + os.fsencode(path) + b'\0'
    assert os.write(1, raw) == len(raw)
emit('R', 'controlled-protocol')
held = None
expired = None
for line in os.fdopen(control, 'rb'):
    message = json.loads(line)
    if message['op'] == 'cookie':
        path = message['path']
        assert Path(path).is_file(), 'event requires the real published cookie'
        if message['hold']:
            tail = None
            if message['partial']:
                raw = b'E' + os.fsencode(message['app']) + b'\0'
                split = len(raw) // 2
                assert os.write(1, raw[:split]) == split
                tail = raw[split:]
            if expired:
                assert tail is None
                emit('E', expired)
            held = (path, tail)
            os.write(notice, os.fsencode(path) + b'\n')
        else:
            emit('E', path)
    elif message['op'] == 'release':
        assert held is not None
        if held[1] is None:
            emit('E', message['app'])
        else:
            assert os.write(1, held[1]) == len(held[1])
        emit('E', held[0])
        expired = held[0]
        held = None
    elif message['op'] == 'error':
        emit('X', 'controlled terminal backend error')
"""
    state = {'base': str(Path(__file__).resolve().parents[2]), 'production_module': R.__file__,
             'timeout': R.COOKIE_TIMEOUT, 'qualification': 'controlled protocol only; no native/AL approval'}
    created, mode = [], {'hold': False, 'partial': False}
    real_open = os.open

    def command(value):
        raw = json.dumps(value).encode() + b'\n'
        assert os.write(descriptors['write'], raw) == len(raw)

    def observed_open(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        candidate = Path(os.fsdecode(path))
        if candidate.parent == gitdir and candidate.name.startswith('al-service-cookie.'):
            assert flags & os.O_EXCL and flags & os.O_NOFOLLOW
            created.append(str(candidate))
            try:
                command({'op': 'cookie', 'path': str(candidate), **mode, 'app': str(app)})
            except BaseException:
                os.close(descriptor)
                raise
        return descriptor

    def close_descriptor(name):
        descriptor = descriptors.pop(name)
        os.close(descriptor)

    def receive_until(predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([child.stdout], [], [], remaining)[0], 'missing controlled record'
            worktree.readable()

    try:
        child = owner.spawn_streams([sys.executable, '-I', '-S', '-B', '-c', producer, str(read), str(notice_write)],
            cwd=repository, env=dict(os.environ, AL_SERVICE='0'), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(read, notice_write))
        close_descriptor('read')
        close_descriptor('notice_write')
        state['ownership'] = owner.record()
        worktree.process = child
        os.set_blocking(child.stdout.fileno(), False)
        selector.register(child.stdout, selectors.EVENT_READ)
        receive_until(lambda: worktree.ready)
        monkeypatch.setattr(R.os, 'open', observed_open)
        baseline = worktree.changes('')
        assert baseline is not None and baseline['paths'] == []
        yield SimpleNamespace(R=R, worktree=worktree, child=child, baseline=baseline, app=app,
            repository=repository, mode=mode, created=created, command=command, state=state,
            notice_read=notice_read, close_descriptor=close_descriptor, receive_until=receive_until,
            forget=forget, directory=tmp_path)
    finally:
        with cleanup_barrier([]):
            try:
                state['cleanup'] = owner.cleanup()
            finally:
                if owner.process is not None:
                    for name in ('stdin', 'stdout', 'stderr'):
                        stream = getattr(owner.process, name, None)
                        if stream is not None:
                            stream.close()
                for descriptor in descriptors.values():
                    os.close(descriptor)
                selector.close()
                (tmp_path / 'protocol-result.json').write_text(json.dumps(state, indent=2) + '\n')
            assert state['cleanup']['complete']
            assert not any(state['cleanup'].get(k) for k in ('errors', 'unresolved', 'secondary_signals'))


def test_cookie_timeout_preserves_live_producer_for_recovery(cookie_protocol):
    import time

    p = cookie_protocol
    worktree, child = p.worktree, p.child
    old_generation, old_epoch = worktree.generation, worktree.epoch
    worktree.tree, worktree.tree_generation = 'a' * 40, old_generation
    p.app.write_text('after\n')
    p.mode['hold'] = True
    started = time.monotonic()
    answer = worktree.changes(p.baseline['token'])
    p.state.update(answer=answer, elapsed=time.monotonic() - started, cookie_paths=p.created[:],
                   producer_returncode=child.poll(), retained_same_process=worktree.process is child)
    assert answer is None, 'expired cookie cannot grant a change-set proof'
    held_notice = os.read(p.notice_read, 4096).decode().strip()
    assert held_notice == p.created[-1], 'producer must have observed the actual held cookie'
    p.state['held_cookie_observed'] = held_notice
    assert child.poll() is None and worktree.process is child, \
        'cookie latency killed the healthy producer before its valid late event could arrive'
    assert worktree.ready and not worktree.watch_failed
    assert worktree.tree is None and worktree.generation > old_generation
    assert worktree.epoch != old_epoch and not worktree.events
    p.mode['hold'] = False
    p.command({'op': 'release', 'app': str(p.app)})
    fresh = worktree.changes('')
    assert fresh is not None and fresh['token'] != p.baseline['token']
    assert len(p.created) == 3, 'recovery requires its own new exclusive cookie'
    assert worktree.changes(p.baseline['token']) is None, 'expired epoch cannot claim complete history'
    assert child.poll() is None and worktree.process is child


def test_partial_record_survives_timeout_and_expired_cookie_cannot_ack_new_request(cookie_protocol):
    p = cookie_protocol
    p.app.write_text('after\n')
    p.mode.update(hold=True, partial=True)
    assert p.worktree.changes(p.baseline['token']) is None
    assert os.read(p.notice_read, 4096).decode().strip() == p.created[-1]
    assert p.worktree.buffer.startswith(b'E') and b'\0' not in p.worktree.buffer
    assert p.worktree.ready and p.child.poll() is None
    p.command({'op': 'release', 'app': str(p.app)})
    p.mode['partial'] = False
    # The producer repeats the expired cookie while withholding this new real one.
    assert p.worktree.changes('') is None, 'an expired cookie cannot certify a new request'
    assert os.read(p.notice_read, 4096).decode().strip() == p.created[-1]
    assert not p.worktree.buffer and p.worktree.current() is None
    assert p.child.poll() is None and p.worktree.process is p.child
    p.mode['hold'] = False
    p.command({'op': 'release', 'app': str(p.app)})
    fresh = p.worktree.changes('')
    assert fresh is not None and fresh['token'] != p.baseline['token']
    assert p.worktree.changes(p.baseline['token']) is None


def test_helper_publication_requires_fresh_generation_and_new_cookie(cookie_protocol):
    import contextlib
    import json
    import subprocess
    from types import SimpleNamespace

    from al.installer.owned_process import OwnedProcess

    p = cookie_protocol
    service = SimpleNamespace(forget=p.forget)
    scripts = Path(__file__).resolve().parents[1] / 'codex/skills/al/scripts'

    @contextlib.contextmanager
    def snapshot_helper(name):
        directory = p.directory / name
        directory.mkdir()
        result = directory / 'tree'
        code = ("import subprocess,sys; from pathlib import Path; "
                "subprocess.run(['git','init','-q',sys.argv[1]],check=True); "
                "subprocess.run(['git','-C',sys.argv[1],'add','--','app.py'],check=True) "
                "if not (Path(sys.argv[1])/'.git/index').exists() else None; "
                "sys.path.insert(0,sys.argv[2]); import al_snapshot; "
                "tree=al_snapshot.snapshot(sys.argv[1]); Path(sys.argv[3]).write_text(tree); print(tree,end='')")
        owner = OwnedProcess(directory / 'owner', 'cookie-snapshot-helper-' + name)
        try:
            child = owner.spawn_streams([sys.executable, '-B', '-c', code, str(p.repository),
                str(scripts), str(result)], cwd=p.repository, env=dict(os.environ, AL_SERVICE='0'),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert child.wait(timeout=5) == 0, child.stderr.read()
            yield child, result.read_text()
        finally:
            try:
                cleanup = owner.cleanup()
                (directory / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
                assert cleanup['complete'] and not any(cleanup.get(k) for k in ('errors', 'unresolved', 'secondary_signals'))
            finally:
                if owner.process is not None:
                    owner.process.stdout.close()
                    owner.process.stderr.close()

    with snapshot_helper('old-helper') as (old, old_tree):
        generation = p.worktree.generation
        p.worktree.helper = (old.pid, generation)
        p.app.write_text('after\n')
        p.mode['hold'] = True
        assert p.worktree.changes(p.baseline['token']) is None
        assert os.read(p.notice_read, 4096).decode().strip() == p.created[-1]
        count = len(p.created)
        p.R.Service.helper_done(service, old.stdout, p.worktree)
        assert p.worktree.current() is None and len(p.created) == count, 'old-generation helper cannot publish'
        assert p.worktree.helper is None
    p.mode['hold'] = False
    p.command({'op': 'release', 'app': str(p.app)})
    p.receive_until(lambda: any(path == str(p.app) for _, path in p.worktree.events))
    with snapshot_helper('fresh-helper') as (fresh, fresh_tree):
        assert fresh_tree != old_tree, 'helper must recompute the changed working copy'
        p.worktree.helper = (fresh.pid, p.worktree.generation)
        count = len(p.created)
        p.R.Service.helper_done(service, fresh.stdout, p.worktree)
        assert len(p.created) == count + 1, 'fresh helper publication requires its own cookie proof'
        assert p.worktree.current() == fresh_tree


@pytest.mark.parametrize('terminal', ['eof', 'error'])
def test_terminal_monitor_stops_and_refuses_proof(cookie_protocol, terminal):
    p = cookie_protocol
    p.worktree.tree, p.worktree.tree_generation = 'a' * 40, p.worktree.generation
    if terminal == 'eof':
        p.close_descriptor('write')
    else:
        p.command({'op': 'error'})
    p.receive_until(lambda: p.worktree.process is None)
    assert p.child.poll() is not None and p.worktree.watch_failed and not p.worktree.ready
    assert p.worktree.current() is None and p.worktree.changes(p.baseline['token']) is None


def test_uncertain_monitor_settlement_retains_ownership_and_refuses_proof(cookie_protocol, monkeypatch):
    import subprocess

    p = cookie_protocol
    real_wait = p.child.wait

    def unavailable_wait(*args, **kwargs):
        raise subprocess.TimeoutExpired(p.child.args, 2)

    monkeypatch.setattr(p.child, 'wait', unavailable_wait)
    try:
        assert p.worktree.stop() is False
        assert p.worktree.process is p.child, 'failed settlement cannot discard owned Popen'
        assert p.worktree.current() is None and p.worktree.changes(p.baseline['token']) is None
    finally:
        monkeypatch.setattr(p.child, 'wait', real_wait)
