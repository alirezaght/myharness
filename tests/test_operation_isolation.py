"""Actual operation subprocess denial includes protected inode alias attacks."""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from harness.command_safety import safe_environment
from harness.execution_isolation import operation_popen, prepare_operation_isolation
from tests.native_model_fixture import operation_sandbox_unavailable

pytestmark = pytest.mark.skipif(
    operation_sandbox_unavailable(), reason="Linux operation sandbox capability unavailable"
)


def test_actual_operation_boundary(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = repository / "source"
    source.mkdir()
    metadata = tmp_path / "native-metadata"
    metadata.write_text("protected-native-identity")
    profile = prepare_operation_isolation(
        repository,
        writable=["source"],
        protected=[metadata],
        hidden=[metadata],
        directory=tmp_path / "operation",
    )
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_root = tempfile.TemporaryDirectory(dir="/tmp", prefix="hipc-")
    endpoint = Path(socket_root.name) / "s"
    listener.bind(str(endpoint))
    listener.listen()
    listener.settimeout(0.1)
    code = """import os,socket,sys,asyncio,ctypes,struct
from pathlib import Path
repo,metadata,endpoint=sys.argv[1:]
assert Path.cwd() == Path(repo) / 'source', 'operation cwd did not reach admitted unit'
Path('cwd-relative').write_text('correct unit')
asyncio.run(asyncio.sleep(0))
if sys.platform.startswith('linux'):
    try:socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
    except PermissionError:pass
    else:raise SystemExit('reconnectable Unix datagram socketpair allowed')
    a,b=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    for change in [lambda:None, lambda:a.shutdown(socket.SHUT_RDWR),
                   lambda:ctypes.CDLL(None).connect(a.fileno(),struct.pack('H',0),2)]:
        try:change()
        except OSError:pass
        try:a.connect(endpoint)
        except OSError:pass
        else:raise SystemExit('anonymous IPC socket reached host Unix endpoint')
    a.close();b.close()
Path(repo+'/source/allowed').write_text('approved')
try:
    if Path(metadata).read_text()=='protected-native-identity':
        raise SystemExit('protected read escaped')
except PermissionError:pass
for action in [lambda:Path(metadata).write_text('escape'),
               lambda:os.link(metadata,repo+'/source/alias'),
               lambda:Path(repo+'/outside').write_text('escape')]:
    try:action()
    except (PermissionError,OSError):pass
    else:raise SystemExit('operation escaped boundary')
for family,address in [(socket.AF_INET,('127.0.0.1',9)),(socket.AF_UNIX,'/var/run/docker.sock')]:
    try:
        s=socket.socket(family,socket.SOCK_STREAM);s.settimeout(.1);s.connect(address)
    except OSError:pass
    else:raise SystemExit('operation reached host service')
"""
    env = safe_environment() | {"TMPDIR": profile.temporary}
    process = operation_popen(
        profile,
        [sys.executable, "-I", "-c", code, str(repository), str(metadata), str(endpoint)],
        cwd=source,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, (stdout, stderr)
    try:
        listener.accept()
    except TimeoutError:
        pass
    else:
        raise AssertionError("host Unix endpoint accepted operation")
    finally:
        listener.close()
        socket_root.cleanup()
    assert metadata.read_text() == "protected-native-identity"
    assert (source / "cwd-relative").read_text() == "correct unit"
    assert (source / "allowed").read_text() == "approved"
    assert not (source / "alias").exists()
    # Reject aliases that existed before policy construction as well.
    os.link(metadata, source / "preexisting-alias")
    try:
        prepare_operation_isolation(
            repository,
            writable=["source"],
            protected=[metadata],
            hidden=[metadata],
            directory=tmp_path / "second-operation",
        )
    except ValueError as error:
        assert "hardlink" in str(error)
    else:
        raise AssertionError("preexisting inode alias admitted")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS process-argument syscall")
def test_parent_arguments_and_environment_are_not_a_read_capability(tmp_path):
    probe = """import ctypes,json,sys
lib=ctypes.CDLL(None,use_errno=True)
mib=(ctypes.c_int*3)(1,49,int(sys.argv[1]))
buf=ctypes.create_string_buffer(1048576);size=ctypes.c_size_t(len(buf))
code=lib.sysctl(mib,3,buf,ctypes.byref(size),None,0)
marker=code==0 and b'synthetic-parent-only-marker' in buf.raw[:size.value]
print(json.dumps({'allowed':code==0,'marker':marker}))
"""
    program = """import json,os,subprocess,sys
from pathlib import Path
from harness.command_safety import safe_environment
from harness.execution_isolation import operation_popen,prepare_operation_isolation
repo=Path(sys.argv[1]);repo.mkdir()
profile=prepare_operation_isolation(repo,writable=[],protected=[],hidden=[],directory=repo.parent/'operation')
argv=[sys.executable,'-I','-c',sys.argv[2],str(os.getpid())]
outside=subprocess.run(argv,env=safe_environment(),capture_output=True,text=True,timeout=10)
assert outside.returncode==0,outside.stderr
child=operation_popen(profile,argv,cwd=repo,env=safe_environment(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
out,err=child.communicate(timeout=10);assert child.returncode==0,(out,err)
print(json.dumps({'outside':json.loads(outside.stdout),'inside':json.loads(out)}))
"""
    root = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys;sys.path.insert(0," + repr(root) + ");" + program,
            str(tmp_path / "repo"),
            probe,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=safe_environment() | {"HARNESS_SENTINEL": "synthetic-parent-only-marker"},
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["outside"] == {"allowed": True, "marker": True}
    assert observed["inside"] == {"allowed": False, "marker": False}


@pytest.mark.parametrize(
    "invalid", ["outside", "alias", "ancestor", "missing", "file", "traversal"]
)
def test_operation_cwd_refuses_before_spawn(tmp_path, monkeypatch, invalid):
    repository = tmp_path / "repo"
    repository.mkdir()
    unit = repository / "unit"
    unit.mkdir()
    (repository / "alias").symlink_to(unit, target_is_directory=True)
    (unit / "file").write_text("data")
    profile = prepare_operation_isolation(
        repository, writable=["unit"], protected=[], hidden=[], directory=tmp_path / "operation"
    )
    cwd = {
        "outside": tmp_path,
        "alias": repository / "alias",
        "ancestor": repository / "alias" / "nested",
        "missing": unit / "missing",
        "file": unit / "file",
        "traversal": unit / "..",
    }[invalid]
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("invalid cwd spawned"))
    with pytest.raises(ValueError, match="operation cwd"):
        operation_popen(profile, [sys.executable, "-c", "pass"], cwd=cwd)


def test_linux_operation_cwd_preserves_grants_and_exec(tmp_path, monkeypatch):
    from dataclasses import replace

    from harness.execution_isolation import operation_argv, operation_exec

    repository = tmp_path / "repo"
    repository.mkdir()
    unit = repository / "unit"
    unit.mkdir()
    profile = replace(
        prepare_operation_isolation(
            repository, writable=["unit"], protected=[], hidden=[], directory=tmp_path / "operation"
        ),
        system="Linux",
    )
    argv = [sys.executable, "-c", "pass"]
    default = operation_argv(profile, argv)
    nested = operation_argv(profile, argv, cwd=unit)
    index = default.index("--chdir") + 1
    assert default[index] == str(repository)
    assert nested[index] == str(unit)
    nested[index] = default[index]
    assert nested == default
    monkeypatch.chdir(unit)
    monkeypatch.setattr("harness.execution_isolation.platform.machine", lambda: "aarch64")
    seen = []
    monkeypatch.setattr(os, "execvpe", lambda executable, command, env: seen.append(command))
    operation_exec(profile, argv, {})
    command = seen.pop()
    assert command[command.index("--chdir") + 1] == str(unit)
    assert "--unshare-net" in command


def test_process_temporary_is_the_only_writable_tmp_directory(tmp_path):
    """Issue #18: the short TMPDIR is granted exactly; the rest of /tmp stays denied."""
    import secrets

    from harness.execution_isolation import (
        process_temporary_environment,
        release_process_temporary,
    )

    repository = tmp_path / "repo"
    repository.mkdir()
    profile = prepare_operation_isolation(
        repository,
        writable=[],
        protected=[],
        hidden=[],
        directory=tmp_path / "operation",
        process_temporary=True,
    )
    short = Path(profile.process_temporary)
    assert short.parent == Path("/tmp") and short.name.startswith("h-")
    assert short.stat().st_mode & 0o777 == 0o700 and short.stat().st_uid == os.getuid()
    sibling = Path(tempfile.mkdtemp(prefix="h-", dir="/tmp"))
    code = """import os,socket,sys;from pathlib import Path
from multiprocessing.managers import SyncManager
if sys.platform.startswith('linux'):
    try:socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    except PermissionError:pass
    else:raise SystemExit('new Unix socket escaped Linux denial')
    a,b=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        a.sendall(b'private-ipc');assert b.recv(11)==b'private-ipc'
    finally:a.close();b.close()
else:
    m=SyncManager();m.start();m.shutdown()
Path(os.environ['TMPDIR'],'own').write_text('ok')
for target in sys.argv[1:]:
    try:Path(target).write_text('escape')
    except OSError:pass
    else:raise SystemExit('wrote '+target)
    s=None
    try:
        s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        s.bind(target+'.sock')
    except OSError:pass
    else:raise SystemExit('bound a socket at '+target)
    finally:
        if s is not None:s.close()
"""
    targets = [f"/tmp/h-escape-{secrets.token_hex(6)}", str(sibling / "escape")]
    try:
        env = process_temporary_environment(
            profile, safe_environment() | {"TMPDIR": profile.temporary}
        )
        assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(short)
        process = operation_popen(
            profile,
            [sys.executable, "-I", "-c", code, *targets],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
        assert (short / "own").read_text() == "ok"
        assert not any(Path(t).exists() for t in targets)
    finally:
        for target in targets:
            Path(target).unlink(missing_ok=True)
            Path(target + ".sock").unlink(missing_ok=True)
        sibling.rmdir()
    release_process_temporary(profile.process_temporary)
    assert not short.exists()
    plain = prepare_operation_isolation(
        repository, writable=[], protected=[], hidden=[], directory=tmp_path / "plain"
    )
    assert plain.process_temporary == ""


def test_linux_grants_bind_only_the_process_temporary(tmp_path):
    from dataclasses import replace

    from harness.execution_isolation import operation_argv, release_process_temporary

    repository = tmp_path / "repo"
    repository.mkdir()
    profile = replace(
        prepare_operation_isolation(
            repository,
            writable=[],
            protected=[],
            hidden=[],
            directory=tmp_path / "operation",
            process_temporary=True,
        ),
        system="Linux",
    )
    try:
        command = operation_argv(profile, [sys.executable, "-c", "pass"])
        binds = [command[i + 1] for i, part in enumerate(command) if part == "--bind"]
        assert profile.process_temporary in binds and "/tmp" not in binds
        assert command[command.index("--ro-bind") + 1] == "/"
    finally:
        release_process_temporary(profile.process_temporary)
