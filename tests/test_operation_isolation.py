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
        cwd=repository,
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
