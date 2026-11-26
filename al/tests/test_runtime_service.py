"""The resident AL service (#27) through a real managed launcher.

One launcher and run are shared by the module (bootstrapping is the slow part). Every
command is compared with the in-process path (`AL_SERVICE=0`) byte for byte."""

import json
import re
import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from al.installer.runtime_pin_boot import CHILD
from al.installer.runtime_service_boot import program
from al.installer.bootstrap import bootstrap_cli
from al.installer.source_materialization import write_captured
from al.installer.validator_origin import capture_canonical, verify_snapshot

pytestmark = pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX service")

SPEC = "Pin transaction controls."
COMMANDS = [["show", "--json"], ["show"], ["bogus-op"], ["phase", "NOPE"]]


def invoke(argv, cwd, env, timeout=120):
    return subprocess.run([str(a) for a in argv], cwd=cwd, env=env, capture_output=True, timeout=timeout)


def git(cwd, *args):
    result = invoke(["git", *args], cwd, None)
    assert result.returncode == 0, result.stderr
    return result.stdout.decode().strip()


def service_identity(pid):
    from al.installer.activity_process import identity

    observed = identity(pid)
    assert observed is not None and not observed["zombie"], "service exited before identity capture"
    assert observed["pgid"] == observed["sid"] == pid, "service has no private session"
    return observed


def wait_service_exit(expected, seconds=20):
    from al.installer.activity_process import identity
    from al.installer.owned_process import group_members

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = identity(expected["pid"])
        if current is not None:
            assert {k: v for k, v in current.items() if k != "zombie"} == {
                k: v for k, v in expected.items() if k != "zombie"
            }, "owned service process identity changed"
        members = group_members(expected["pgid"])
        assert all(row["sid"] == expected["sid"] and row["start"] >= expected["start"]
                   for row in members), "foreign process in owned service group"
        if (current is None or current["zombie"]) and not any(not row["zombie"] for row in members):
            return
        time.sleep(0.05)
    raise AssertionError("owned service or command/watch children have not settled")


class Setup:
    def __init__(self, base):
        self.base = base
        self.base.mkdir(parents=True, exist_ok=True)
        self.profile_count = 0
        self.owned_services = {}
        self.source = Path(__file__).resolve().parents[1]
        self.source_files = self.measured('source-capture', lambda: capture_canonical(self.source))["origin"]["closure"]
        self.home = base / "home"
        self.home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1", AL_SERVICE="1",
                        AL_SERVICE_IDLE_SECONDS="300", GIT_AUTHOR_NAME="f", GIT_AUTHOR_EMAIL="f@x.invalid",
                        GIT_COMMITTER_NAME="f", GIT_COMMITTER_EMAIL="f@x.invalid")
        for name in ("AL_RUNTIME_DIR", "AL_DISTRIBUTION_STATE", "PYTHONPATH", "PYTHONHOME"):
            self.env.pop(name, None)
        assert self.measured('git-init', lambda: invoke(["git", "init", "-q"], self.repo, self.env)).returncode == 0
        (self.repo / "app.py").write_text("print('x')\n")
        assert self.measured('git-add', lambda: invoke(["git", "add", "-A"], self.repo, self.env)).returncode == 0
        assert self.measured('git-commit', lambda: invoke(["git", "commit", "-qm", "initial"], self.repo, self.env)).returncode == 0
        self.launcher = self.home / "bin/alctl"
        self.bootstrap()
        self.services = self.home / "installer/al-services"

    def measured(self, stage, operation):
        started = time.monotonic()
        try:
            return operation()
        finally:
            self.profile_count += 1
            if self.profile_count <= 512:
                value = {'stage': stage, 'seconds': time.monotonic() - started}
                with (self.base / 'stage-profile.jsonl').open('a') as output:
                    output.write(json.dumps(value) + '\n')
            elif self.profile_count == 513:
                with (self.base / 'stage-profile.jsonl').open('a') as output:
                    output.write(json.dumps({'truncated': True}) + '\n')

    def bootstrap(self, source=None):
        boot = self.measured('bootstrap', lambda: bootstrap_cli(source or self.source, self.launcher, self.home / "installer"))
        verified = self.measured('snapshot-verify', lambda: verify_snapshot(Path(boot["snapshot"]), boot["validator_binding"]))
        if source is None:
            self.boot, self.snapshot = boot, Path(boot["snapshot"])
        return verified

    def variant(self):
        verified = verify_snapshot(self.snapshot, self.boot["validator_binding"])
        root = self.base / "variant"
        root.mkdir(mode=0o700)
        for name, raw in verified["bytes"].items():
            write_captured(self.snapshot / name, root / name, raw,
                           verified["origin"]["closure"][name]["mode"])
        return root

    def restore_service(self):
        self.stop_services()
        code, out, err = self.run("s1", "show", "--json")
        assert code == 0, (out[-2000:], err[-2000:])
        rows = self.wait(lambda: self.control("status"))
        assert [row["pin_identity"] for row in rows] == [self.pin_of("s1")["pin_identity"]]
        before = self.served()
        code, out, err = self.run("s1", "show", "--json")
        assert code == 0, (out[-2000:], err[-2000:])
        assert self.served() == before + 1


    def run(self, session, *argv, repo=None, service=True, env=None):
        repo = repo or self.repo
        environment = dict(env or self.env, AL_SERVICE="1" if service else "0")
        command = argv[0] if argv and argv[0] in {'start', 'show', 'phase', 'bogus-op'} else 'command'
        stage = 'run-' + command + ('-service' if service else '-in-process')
        result = self.measured(stage, lambda: invoke([self.launcher, "run", "--repository", repo, "--platform", "codex", "--session", session,
                         "--", *argv], repo, environment))
        return result.returncode, result.stdout, result.stderr

    def control(self, action):
        stage = 'service-' + action if action in {'status', 'stop'} else 'service-control'
        result = self.measured(stage, lambda: invoke([self.launcher, "service", action], self.repo, self.env))
        assert result.returncode == 0, result.stdout + result.stderr
        rows = json.loads(result.stdout)["services"]
        from al.installer.activity_process import identity
        from al.installer.owned_process import group_members
        for row in rows:
            assert Path(row["directory"]).parent == self.services
            current = identity(row["pid"])
            if current is None:
                assert row["pid"] in self.owned_services or not group_members(row["pid"]), \
                    "unobserved service identity leaves child settlement unproven"
                continue
            assert current["pgid"] == current["sid"] == row["pid"], "service has no private session"
            observed = current
            previous = self.owned_services.get(row["pid"])
            assert previous is None or previous["start"] == observed["start"], \
                "owned service PID was reused"
            self.owned_services[row["pid"]] = observed
        return rows

    def stop_services(self):
        self.control("status")
        self.control("stop")
        for observed in self.owned_services.values():
            wait_service_exit(observed)
        self.owned_services.clear()

    def wait(self, predicate, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.1)
        raise AssertionError("condition not reached")

    def served(self, pin=None):
        rows = [row for row in self.control("status") if pin is None or row["pin_identity"] == pin]
        return sum(row["commands_served"] for row in rows)

    def pin_of(self, session):
        for path in (self.home / "installer/al-run-pins/runs").glob("*.json"):
            record = json.loads(path.read_bytes())
            if record["session_id"] == session:
                return record["pin"]
        raise KeyError(session)


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    value = Setup(tmp_path_factory.mktemp("service"))
    code, out, err = value.run("s1", "start", "--spec", SPEC)
    assert code == 0, (out[-2000:], err[-2000:])
    # The first command after start runs in process and leaves a warm service behind.
    code, out, err = value.run("s1", "show", "--json")
    assert code == 0, (out[-4000:], err[-4000:])
    value.wait(lambda: value.control("status"))
    yield value
    try:
        assert capture_canonical(value.source)["origin"]["closure"] == value.source_files
        verify_snapshot(value.snapshot, value.boot["validator_binding"])
    finally:
        value.stop_services()


@pytest.fixture
def restored_service(setup):
    yield
    setup.restore_service()


@pytest.fixture
def restored_pin(setup, restored_service):
    pin = setup.pin_of("s1")
    member = setup.home / "installer/al-run-pins/pins" / pin["pin_identity"] / "payload/codex/skills/al/scripts/al_state.py"
    original, mode = member.read_bytes(), member.stat().st_mode & 0o777
    yield
    setup.stop_services()
    member.chmod(0o600)
    member.write_bytes(original)
    member.chmod(mode)


def _timeless(result):
    # Progress lines carry wall-clock elapsed times, which differ between any two runs.
    code, out, err = result
    return code, out, re.sub(rb"\d+\.\ds elapsed", b"Ns elapsed", err)


@pytest.mark.parametrize("path", ["service", "in-process"])
@pytest.mark.parametrize("argv", COMMANDS, ids=" ".join)
def test_service_and_in_process_results_are_byte_identical(setup, argv, path):
    expected = setup.run("s1", *argv, service=False)
    before = setup.served()
    actual = setup.run("s1", *argv, service=path == "service")
    assert _timeless(actual) == _timeless(expected)
    assert setup.served() == before + (1 if path == "service" else 0)


def test_status_names_the_run_pin_and_watches_the_worktree(setup):
    rows = setup.control("status")
    assert [row["pin_identity"] for row in rows] == [setup.pin_of("s1")["pin_identity"]]
    row = rows[0]
    assert row["common_dir"] == str((setup.repo / ".git").resolve())
    assert row["platform"] == "codex" and row["pid"] > 0
    directory = Path(row["directory"])
    assert (directory.stat().st_mode & 0o777) == 0o700 and ((directory / "token").stat().st_mode & 0o777) == 0o600
    assert (setup.services.stat().st_mode & 0o777) == 0o700


def _direct_snapshot(setup):
    scripts = setup.snapshot / "codex/skills/al/scripts"
    code = f"import sys;sys.path.insert(0,{str(scripts)!r});import al_snapshot;print(al_snapshot.snapshot())"
    for _ in range(50):
        result = invoke([sys.executable, "-c", code], setup.repo, setup.env)
        if result.returncode == 0:
            return result.stdout.decode().strip()
        time.sleep(0.1)  # the service's background snapshot holds the private index
    raise AssertionError(result.stderr)


def _service_tree(setup):
    worktrees = setup.control("status")[0]["worktrees"]
    return worktrees[0]["snapshot"] if worktrees and worktrees[0]["watcher"] != "none" else None


def test_watcher_keeps_the_in_memory_snapshot_current(setup):
    first = setup.wait(lambda: _service_tree(setup))
    assert first == _direct_snapshot(setup)
    (setup.repo / "app.py").write_text("print('changed')\n")
    second = setup.wait(lambda: (tree := _service_tree(setup)) != first and tree)
    assert second == _direct_snapshot(setup)
    # AL's own runtime files and excluded directories never change the tree.
    (setup.repo / "node_modules").mkdir(exist_ok=True)
    (setup.repo / "node_modules/x.js").write_text("x")
    time.sleep(1)
    assert _service_tree(setup) == second == _direct_snapshot(setup)


def _request(directory, value, *, fds=()):
    import socket

    from al.installer.runtime_service_client import namespace

    wire = namespace()
    sock, token = wire["_al_service_connect"](str(directory))
    sock.settimeout(10)
    wire["_al_service_send"](sock, {"protocol": 1, "token": token, **value}, fds)
    return wire, sock


def test_snapshot_op_answers_the_proven_current_tree(setup):
    directory = setup.control("status")[0]["directory"]
    tree = setup.wait(lambda: _service_tree(setup))
    wire, sock = _request(directory, {"op": "snapshot", "repository": str(setup.repo)})
    with sock:
        assert wire["_al_service_receive"](sock) == {"event": "snapshot", "tree": tree}
    # A repository outside the service's common directory is never answered.
    other = setup.base / "unrelated"
    other.mkdir(exist_ok=True)
    git(other, "init", "-q")
    wire, sock = _request(directory, {"op": "snapshot", "repository": str(other)})
    with sock:
        assert wire["_al_service_receive"](sock) == {"event": "snapshot", "tree": None}


def test_subscription_pushes_changes_of_named_files(setup, tmp_path):
    directory = setup.control("status")[0]["directory"]
    target = tmp_path / "state.json"
    target.write_text("{}")
    wire, sock = _request(directory, {"op": "subscribe", "paths": [str(target)]})
    with sock:
        assert wire["_al_service_receive"](sock) == {"event": "subscribed", "paths": [str(target)]}
        replacement = tmp_path / ".state.tmp"
        replacement.write_text('{"x":1}')
        os.replace(replacement, target)  # AL saves atomically
        assert wire["_al_service_receive"](sock) == {"event": "changed", "path": str(target), "present": True}
    wire, sock = _request(directory, {"op": "subscribe", "paths": ["relative.json"]})
    with sock:
        assert wire["_al_service_receive"](sock)["event"] == "fallback"


def test_harness_contained_command_never_runs_in_the_service(setup):
    """A command carrying Harness containment keeps its own sandbox (#31)."""
    before = setup.served()
    env = dict(setup.env, HARNESS_CHECK_CONTEXT="/nonexistent/check-context.json")
    expected = setup.run("s1", "show", "--json", service=False, env=env)
    assert setup.run("s1", "show", "--json", env=env) == expected
    assert setup.served() == before


def test_runs_pinned_to_different_runtimes_are_isolated(setup, restored_service, request):
    # A new AL release: the launcher changes and a new run gets a new pin.
    variant = setup.variant()
    changed = variant / "codex/skills/al/reference/performance.md"
    original = changed.read_bytes()
    request.addfinalizer(lambda: changed.write_bytes(original))
    with changed.open("a") as handle:
        handle.write("\n<!-- release 2 -->\n")
    # The first read after a write moves the access time (relatime), and the installer's
    # source capture refuses metadata that moves between its own observations. Settle the
    # access time past the new modification time before bootstrapping, as an edit made
    # some time before a release would be.
    info = changed.stat()
    os.utime(changed, ns=(info.st_mtime_ns + 10**9, info.st_mtime_ns))
    changed.read_bytes()
    assert changed.stat().st_atime_ns == info.st_mtime_ns + 10**9
    setup.bootstrap(variant)
    worktree = setup.base / "second"
    git(setup.repo, "worktree", "add", "-q", str(worktree), "-b", "second")
    code, out, err = setup.run("s2", "start", "--spec", SPEC, repo=worktree)
    assert code == 0, (out[-2000:], err[-2000:])
    first_pin, second_pin = setup.pin_of("s1"), setup.pin_of("s2")
    assert first_pin != second_pin
    for session, repo in (("s1", setup.repo), ("s2", worktree)):
        setup.run(session, "show", "--json", repo=repo)
    # Services are per launcher too: the first release's service for run 1 may still be
    # alive beside the current launcher's. Use only the current launcher's services, the
    # one that serves run 2 (whose pin only the current release produced).
    def current():
        status = setup.control("status")
        launchers = {row["launcher_identity"] for row in status if row["pin_identity"] == second_pin["pin_identity"]}
        rows = {row["pin_identity"]: row for row in status if row["launcher_identity"] in launchers}
        return rows if {first_pin["pin_identity"], second_pin["pin_identity"]} <= set(rows) else None

    rows = setup.wait(current)
    assert len({row["launcher_identity"] for row in rows.values()}) == 1
    assert rows[first_pin["pin_identity"]]["pid"] != rows[second_pin["pin_identity"]]["pid"]
    for session, repo, pin in (("s1", setup.repo, first_pin), ("s2", worktree, second_pin)):
        before = setup.served(pin["pin_identity"])
        assert setup.run(session, "show", "--json", repo=repo) == setup.run(
            session, "show", "--json", repo=repo, service=False)
        assert setup.served(pin["pin_identity"]) == before + 1
    # Misroute run 2 to run 1's service: it refuses before running, and the launcher's
    # in-process path gives the same result (and restores the route).
    routes = sorted((setup.services / "routes").iterdir(), key=lambda p: p.stat().st_mtime)
    second_service = rows[second_pin["pin_identity"]]["directory"]
    first_service = rows[first_pin["pin_identity"]]["directory"]
    misrouted = [p for p in routes if p.read_text() == Path(second_service).name]
    assert misrouted
    for path in misrouted:
        path.write_text(Path(first_service).name)
    expected = setup.run("s2", "show", "--json", repo=worktree, service=False)
    assert setup.run("s2", "show", "--json", repo=worktree) == expected
    assert "pinned runtime differs" in (Path(first_service) / "log").read_text()
    assert all(p.read_text() == Path(second_service).name for p in misrouted)


def test_stop_falls_back_then_restarts(setup):
    assert setup.control("stop")
    setup.wait(lambda: not setup.control("status"))
    expected = setup.run("s1", "show", "--json", service=False)
    assert setup.run("s1", "show", "--json") == expected  # no service: in process, starts one
    setup.wait(lambda: setup.control("status"))
    assert setup.run("s1", "show", "--json") == expected


def test_idle_service_exits(setup, restored_service):
    setup.control("stop")
    setup.wait(lambda: not setup.control("status"))
    env = dict(setup.env, AL_SERVICE_IDLE_SECONDS="1")
    assert setup.run("s1", "show", "--json", env=env)[0] == 0
    # With a 1 s idle limit the service can start and exit between two status polls on a loaded
    # machine, so wait for its own idle-exit record rather than for a live status row first.
    setup.wait(lambda: any("idle timeout" in p.read_text() for p in setup.services.glob("*/log")))
    setup.wait(lambda: not setup.control("status"))


def test_tampered_pin_is_detected(setup, restored_pin):
    """Damage run 1's pin, then restore its real bytes before the next case."""
    setup.control("stop")
    setup.wait(lambda: not setup.control("status"))
    assert setup.run("s1", "show", "--json")[0] == 0
    service = Path(setup.wait(lambda: setup.control("status"))[0]["directory"])
    pin = setup.pin_of("s1")
    member = setup.home / "installer/al-run-pins/pins" / pin["pin_identity"] / "payload/codex/skills/al/scripts/al_state.py"
    assert setup.control("status")[0]["pin_identity"] == pin["pin_identity"]
    mode = member.stat().st_mode
    member.chmod(0o600)
    with member.open("a") as handle:
        handle.write("\n# tampered\n")
    member.chmod(mode & 0o777)
    # A running service re-verifies on any change in its pin and stops. The full re-verification
    # can take long on a loaded machine, so wait for the service's own record of it (the event),
    # with a bound far above its cost, before expecting it gone.
    log = service / "log"
    try:
        setup.wait(lambda: log.is_file() and "failed verification" in log.read_text(), seconds=180)
    except AssertionError as exc:
        raise AssertionError({"service": str(service), "pin": pin, "status": setup.control("status"),
                              "service_log": log.read_text() if log.is_file() else "missing"}) from exc
    setup.wait(lambda: not setup.control("status"), seconds=60)
    # A service started on the tampered pin refuses before it listens.
    directory = setup.base / "manual"
    directory.mkdir(mode=0o700)
    manifest = json.loads((setup.home / "installer/al-run-pins/pins" / pin["pin_identity"] / "manifest.json").read_text())
    request = {"protocol": 1, "storage": str(setup.home / "installer/al-run-pins"), "pin": pin, "platform": "codex",
               "common_dir": str((setup.repo / ".git").resolve()), "launcher_identity": "x",
               "service_directory": str(directory), "child": CHILD, "idle_seconds": 5}
    started = invoke([manifest["interpreter"]["executable"], "-I", "-S", "-B", "-c", program(request)], "/", setup.env)
    assert started.returncode != 0
    assert b"runtime pin member changed" in started.stderr
    assert not (directory / "s").exists() and not (directory / "token").exists()
    # The command refuses through the in-process path; no service is left serving it.
    code, _, _ = setup.run("s1", "show", "--json")
    assert code != 0
    time.sleep(1)
    assert not setup.control("status")
    shutil.rmtree(directory)


def test_stop_services_waits_for_live_child_after_status_is_empty(tmp_path):
    from al.installer.activity_process import identity

    launcher = tmp_path / "alctl"
    launcher.write_text("#!/bin/sh\nprintf '%s\\n' '{\"services\":[]}'\n")
    launcher.chmod(0o700)
    child_code = "import time; time.sleep(0.8)"
    parent_code = (
        "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',"
        + repr(child_code) + "]); print(child.pid,flush=True); sys.stdin.read(1)"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], start_new_session=True,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    child = None
    try:
        child_pid = int(parent.stdout.readline())
        expected = service_identity(parent.pid)
        child = identity(child_pid)
        assert child is not None and child["pgid"] == expected["pgid"]
        parent.stdin.close()
        parent.wait(timeout=3)
        current = identity(child_pid)
        assert current is not None and not current["zombie"]
        setup = object.__new__(Setup)
        setup.base, setup.repo, setup.launcher = tmp_path, tmp_path, launcher
        setup.env, setup.profile_count = dict(os.environ), 0
        setup.services = tmp_path / "al-services"
        setup.owned_services = {parent.pid: expected}
        setup.stop_services()
        settled = identity(child_pid)
        assert settled is None or settled["zombie"]
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        if child is not None:
            current = identity(child["pid"])
            if current is not None and current["start"] == child["start"] and not current["zombie"]:
                os.kill(child["pid"], signal.SIGKILL)
        if parent.stdin is not None and not parent.stdin.closed:
            parent.stdin.close()
        parent.stdout.close()
