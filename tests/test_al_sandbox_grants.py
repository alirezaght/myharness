"""The operation sandbox admits exactly AL's cache paths and its service socket (#31)."""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from harness.command_safety import safe_environment
from harness.execution_isolation import (
    AlGrants,
    OperationIsolation,
    al_grants,
    analysis_store,
    operation_argv,
    operation_popen,
    prepare_operation_isolation,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "al/claude/skills/al/scripts"
darwin = pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec profile")


def git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@x.invalid")
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout.decode().strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("print('x')\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    return repo.resolve()


@pytest.fixture
def service(tmp_path):
    """A stand-in service directory: a private directory with a listening socket and token."""
    directory = Path(tempfile.mkdtemp(prefix="als-", dir="/tmp")).resolve()
    directory.chmod(0o700)
    (directory / "token").write_text("t")
    (directory / "token").chmod(0o600)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(directory / "s"))
    listener.listen()
    other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    other.bind(str(directory / "t"))
    other.listen()
    yield directory
    listener.close()
    other.close()
    for name in ("s", "t", "token"):
        (directory / name).unlink(missing_ok=True)
    directory.rmdir()


def test_grants_name_exactly_the_cache_paths(repository, service):
    grants = al_grants(repository, service_directory=service, system="Darwin")
    git_dir = repository / ".git"
    _, store = analysis_store(git_dir.resolve())
    assert grants.writable == (
        str(git_dir / "al-snapshot"),
        str(git_dir / "refs/al/snapshots"),
        str(git_dir / "al-action-cache"),
        str(store),
    )
    assert grants.sockets == (str(service / "s"),)
    assert dict(grants.environment) == {"AL_SNAPSHOT_OBJECTS": "private", "AL_SERVICE_SNAPSHOT": str(service)}
    for path in grants.writable:
        assert Path(path).is_dir() and not Path(path).is_symlink()
    assert (git_dir / "al-snapshot").stat().st_mode & 0o777 == 0o700
    # Linux keeps its seccomp refusal of Unix stream sockets: no socket grant there.
    linux = al_grants(repository, service_directory=service, system="Linux")
    assert linux.sockets == () and "AL_SERVICE_SNAPSHOT" not in dict(linux.environment)


def test_symlinked_grant_path_is_refused(repository, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", repository / ".git/al-action-cache")
    with pytest.raises(ValueError, match="symlink"):
        al_grants(repository)


def test_linux_bubblewrap_binds_exactly_the_grants_after_the_read_only_git(repository, tmp_path):
    grants = al_grants(repository)
    scratch = tmp_path / "operation/tmp"
    scratch.mkdir(parents=True)
    profile = OperationIsolation(
        str(tmp_path / "operation/operation.sb"), str(repository), (), (str(repository / ".git"),), (),
        str(scratch), None, None, "Linux", al_writable=grants.writable,
    )
    argv = operation_argv(profile, ["true"])
    binds = [argv[i + 1] for i, token in enumerate(argv) if token == "--bind"]
    assert binds == [*grants.writable, str(scratch)]
    read_only = argv.index("--ro-bind", argv.index("/dev") + 1)
    assert argv[read_only + 1] == str(repository / ".git")
    assert all(argv.index(path) > read_only for path in grants.writable)


PROBE = r"""
import json, os, socket, sys
from pathlib import Path
git, service, store, other_socket = sys.argv[1:5]
results = {}
def attempt(name, action):
    try:
        action()
        results[name] = 'allowed'
    except OSError:
        results[name] = 'denied'
def write(path):
    return lambda: Path(path).write_text('x')
def connect(path):
    def run():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(path)
        finally:
            s.close()
    return run
attempt('snapshot dir', write(git + '/al-snapshot/index'))
attempt('snapshot ref', lambda: (Path(git + '/refs/al/snapshots/run').mkdir(exist_ok=True),
                                  Path(git + '/refs/al/snapshots/run/tree').write_text('x')))
attempt('action cache', write(git + '/al-action-cache/record'))
attempt('analysis store', write(store + '/pack'))
attempt('service socket', connect(service + '/s'))
# Everything else stays denied.
attempt('git config', write(git + '/config'))
attempt('real index', write(git + '/index'))
attempt('old index name', write(git + '/al-snapshot.index'))
attempt('heads', write(git + '/refs/heads/escape'))
attempt('other al refs', write(git + '/refs/al/escape'))
attempt('objects', write(git + '/objects/escape'))
attempt('hooks', write(git + '/hooks/pre-commit'))
attempt('cache sibling', write(git + '/al-action-cache-escape'))
attempt('analysis root', write(str(Path(store).parent) + '/escape'))
attempt('other socket in service dir', connect(service + '/t'))
attempt('foreign socket', connect(other_socket))
attempt('service token', write(service + '/token'))
print(json.dumps(results))
"""

ALLOWED = {"snapshot dir", "snapshot ref", "action cache", "analysis store", "service socket"}


@darwin
def test_macos_profile_admits_exactly_the_grants(repository, service, tmp_path):
    grants = al_grants(repository, service_directory=service)
    profile = prepare_operation_isolation(
        repository, writable=[], protected=[], hidden=[], directory=tmp_path / "operation", al_grants=grants
    )
    foreign_root = Path(tempfile.mkdtemp(prefix="alf-", dir="/tmp")).resolve()
    foreign = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    foreign.bind(str(foreign_root / "f"))
    foreign.listen()
    try:
        process = operation_popen(
            profile,
            [sys.executable, "-I", "-c", PROBE, str(repository / ".git"), str(service), grants.writable[3],
             str(foreign_root / "f")],
            cwd=repository,
            env=safe_environment() | {"TMPDIR": profile.temporary},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate(timeout=30)
    finally:
        foreign.close()
        (foreign_root / "f").unlink()
        foreign_root.rmdir()
    assert process.returncode == 0, stderr
    results = json.loads(stdout)
    assert {name for name, value in results.items() if value == "allowed"} == ALLOWED, results
    assert git(repository, "config", "--get", "core.bare") == "false"


@darwin
def test_macos_without_grants_denies_them_all(repository, service, tmp_path):
    grants = al_grants(repository, service_directory=service)
    profile = prepare_operation_isolation(
        repository, writable=[], protected=[], hidden=[], directory=tmp_path / "operation"
    )
    process = operation_popen(
        profile,
        [sys.executable, "-I", "-c", PROBE, str(repository / ".git"), str(service), grants.writable[3],
         str(service / "t")],
        cwd=repository,
        env=safe_environment() | {"TMPDIR": profile.temporary},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert set(json.loads(stdout).values()) == {"denied"}


SNAPSHOT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import al_snapshot
print(json.dumps({'tree': al_snapshot.snapshot(), 'subset': al_snapshot.input_tree({}, ['app.py'])}))
"""


@darwin
def test_al_snapshot_inside_the_sandbox_matches_the_unsandboxed_tree(repository, tmp_path):
    """AL captures with its private index and objects only; tree ids are the same (#31)."""
    (repository / "app.py").write_text("print('changed in the working copy')\n")
    (repository / "new.py").write_text("print('untracked')\n")
    outside = subprocess.run([sys.executable, "-I", "-c", SNAPSHOT, str(SCRIPTS)], cwd=repository,
                             capture_output=True, env=safe_environment() | {"PATH": os.environ["PATH"]})
    assert outside.returncode == 0, outside.stderr
    objects_before = sorted(p.name for p in (repository / ".git/objects").rglob("*"))
    grants = al_grants(repository)
    profile = prepare_operation_isolation(
        repository, writable=[], protected=[], hidden=[], directory=tmp_path / "operation", al_grants=grants
    )
    environment = safe_environment() | {"PATH": os.environ["PATH"], "TMPDIR": profile.temporary,
                                        **dict(grants.environment)}
    process = operation_popen(profile, [sys.executable, "-I", "-c", SNAPSHOT, str(SCRIPTS)], cwd=repository,
                              env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 0, stderr
    assert json.loads(stdout) == json.loads(outside.stdout)
    assert sorted(p.name for p in (repository / ".git/objects").rglob("*")) == objects_before
    assert any((repository / ".git/al-snapshot/objects").rglob("*"))


def test_al_grants_default_is_none():
    assert AlGrants().writable == () and AlGrants().sockets == ()


CACHE = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import al_action_cache as Cache
spec = {"area": "svc", "kind": "test", "argv": [sys.executable, "-c", "print('1 passed')"], "cwd": ".",
        "scope": ["app.py"], "sources": [], "env": [], "storage": None}
plan, reason = Cache.evidence_plan({}, spec)
out = {"key": plan and plan.digest, "reason": reason}
if plan is not None and sys.argv[2] == "record":
    import al_ledger as L
    log = os.path.join(sys.argv[3], "check.log")
    with open(log, "w") as handle:
        handle.write("1 passed\n")
    now = time.time()
    entry = {"id": "E1", "status": "PASS", "exit": 0, "note": "", "counts": {"passed": 1},
             "log_sha256": L.file_sha(log), "started_at": now, "ended_at": now}
    out["saved"] = Cache.save(plan, entry, log, {"run": "run-standalone", "session": "s", "entry": "E1"})
elif plan is not None:
    hit = Cache.lookup(plan)
    out["origin"] = hit and hit["origin"]
    if hit:
        out["provenance"] = Cache.reused_text({"action_cache": Cache.provenance(plan, hit)})
print(json.dumps(out))
"""


@darwin
def test_a_check_recorded_standalone_is_a_cache_hit_inside_the_harness_sandbox(repository, tmp_path):
    """Same keys inside and outside Harness (#31): a check that already ran on identical
    inputs is found by the contained AL process, and reported as reused."""
    base = safe_environment() | {"PATH": os.environ["PATH"]}
    recorded = subprocess.run([sys.executable, "-I", "-c", CACHE, str(SCRIPTS), "record", str(tmp_path)],
                              cwd=repository, capture_output=True, env=base)
    assert recorded.returncode == 0, recorded.stderr
    recorded = json.loads(recorded.stdout)
    assert recorded["saved"] is True, recorded

    def contained(grants, name):
        profile = prepare_operation_isolation(repository, writable=[], protected=[], hidden=[],
                                              directory=tmp_path / name, al_grants=grants)
        environment = base | {"TMPDIR": profile.temporary, **dict(grants.environment if grants else ())}
        process = operation_popen(profile, [sys.executable, "-I", "-c", CACHE, str(SCRIPTS), "lookup"],
                                  cwd=repository, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        return json.loads(stdout)

    inside = contained(al_grants(repository), "granted")
    assert inside["key"] == recorded["key"]
    assert inside["origin"]["run"] == "run-standalone"
    assert inside["provenance"].startswith("reused from run run-standalone (E1) on the same input tree ")
    # Without the grants the contained process cannot capture its inputs: no cache, it runs.
    ungranted = contained(None, "ungranted")
    assert ungranted["key"] is None and "unavailable" in ungranted["reason"]
