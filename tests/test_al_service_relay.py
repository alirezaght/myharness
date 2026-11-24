"""#34: a Linux-contained AL command reaches the AL service, and no other socket.

The seccomp filter keeps refusing every new Unix socket. The supervisor passes one connected
descriptor into bwrap (`AL_SERVICE_FD`); its relay forwards snapshot requests, with the service
token added outside the sandbox, and refuses everything else."""

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from harness.execution_al_service import SnapshotRelay, _frame, receive
from harness.execution_isolation import (
    OperationIsolation,
    al_grants,
    operation_popen,
    seccomp_program,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "al/claude/skills/al/scripts"
TREE = "a" * 40


@pytest.fixture
def service():
    """A stand-in AL service: a private directory, token and socket answering snapshots."""
    directory = Path(tempfile.mkdtemp(prefix="als-", dir="/tmp")).resolve()
    directory.chmod(0o700)
    (directory / "token").write_text("secret-token")
    (directory / "token").chmod(0o600)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(directory / "s"))
    listener.listen()
    seen = []

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                try:
                    message = receive(conn)
                except (OSError, ValueError, EOFError):
                    continue
                seen.append(message)
                conn.sendall(_frame({"event": "snapshot", "tree": TREE}))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield directory, seen
    listener.close()
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo.resolve()


def run_filter(program, nr, args, arch):
    """Evaluate the classic-BPF seccomp program for one syscall (seccomp_data layout)."""
    data = (
        struct.pack("<iI", nr, arch)
        + struct.pack("<Q", 0)
        + b"".join(struct.pack("<Q", a) for a in (*args, *([0] * (6 - len(args)))))
    )
    accumulator, pc = 0, 0
    while True:
        code, jt, jf, k = program[pc]
        if code == 0x20:  # ld [k]
            accumulator = struct.unpack_from("<I", data, k)[0]
        elif code == 0x54:  # and #k
            accumulator &= k
        elif code == 0x15:  # jeq #k
            pc += jt if accumulator == k else jf
        elif code == 0x06:  # ret #k
            return k
        else:
            raise AssertionError(f"unexpected BPF opcode {code:#x}")
        pc += 1


ALLOW, EPERM = 0x7FFF0000, 0x00050001
AF_UNIX, AF_INET, SOCK_STREAM, SOCK_DGRAM = 1, 2, 1, 2


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_seccomp_refuses_every_new_unix_socket(machine):
    program = seccomp_program(machine)
    arch, socket_call, socketpair_call = {
        "x86_64": (0xC000003E, 41, 53),
        "aarch64": (0xC00000B7, 198, 199),
    }[machine]
    assert run_filter(program, socket_call, (AF_UNIX, SOCK_STREAM, 0), arch) == EPERM
    assert run_filter(program, socket_call, (AF_UNIX, SOCK_DGRAM, 0), arch) == EPERM
    assert run_filter(program, socket_call, (AF_INET, SOCK_STREAM, 0), arch) == ALLOW
    assert run_filter(program, socketpair_call, (AF_UNIX, SOCK_STREAM, 0), arch) == ALLOW
    assert run_filter(program, socketpair_call, (AF_UNIX, SOCK_DGRAM, 0), arch) == EPERM
    # read/write/sendmsg on an inherited descriptor are not filtered.
    assert run_filter(program, 0, (), arch) == ALLOW
    assert run_filter(program, socket_call, (AF_UNIX, SOCK_STREAM, 0), 0x1234) == 0x80000000


def test_linux_grants_name_the_service_socket_without_a_path_grant(repository, service):
    directory, _ = service
    grants = al_grants(repository, service_directory=directory, system="Linux")
    assert grants.sockets == (str(directory / "s"),)
    assert "AL_SERVICE_SNAPSHOT" not in dict(grants.environment)


def linux_profile(tmp_path, repository, sockets):
    scratch = tmp_path / "operation/tmp"
    scratch.mkdir(parents=True)
    scratch.chmod(0o700)
    return OperationIsolation(
        str(tmp_path / "operation/operation.sb"),
        str(repository),
        (),
        (),
        (),
        str(scratch),
        None,
        None,
        "Linux",
        al_sockets=sockets,
    )


RELAY_CLIENT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import al_snapshot
print(json.dumps(al_snapshot._relay_answer(sys.argv[2], os.environ['AL_SERVICE_FD'])))
"""


def test_linux_operation_passes_one_relay_descriptor_that_reaches_only_snapshots(
    repository, service, tmp_path, monkeypatch
):
    directory, seen = service
    captured = {}

    def popen(command, **kwargs):
        captured["command"], captured["kwargs"] = command, kwargs
        # The child's copy of the relay end, as bwrap would hold it.
        relay = int(kwargs["env"]["AL_SERVICE_FD"].split(":")[0])
        captured["held"] = os.dup(relay)
        return subprocess.CompletedProcess(command, 0)

    profile = linux_profile(tmp_path, repository, (str(directory / "s"),))
    with monkeypatch.context() as patch:
        patch.setattr("harness.execution_isolation.platform.machine", lambda: "x86_64")
        patch.setattr("harness.execution_isolation.subprocess.Popen", popen)
        operation_popen(
            profile,
            ["al", "check"],
            env={"PATH": os.environ["PATH"], "AL_SERVICE_SNAPSHOT": str(directory)},
        )
    command, kwargs = captured["command"], captured["kwargs"]
    assert command[:3] == ["bwrap", "--seccomp", command[2]]
    descriptor, inode = (int(v) for v in kwargs["env"]["AL_SERVICE_FD"].split(":"))
    assert kwargs["pass_fds"] == (int(command[2]), descriptor)
    assert "AL_SERVICE_SNAPSHOT" not in kwargs["env"]
    # Nothing of the service directory is bound, nor any socket path: only the inherited end.
    assert not any(str(directory) in token for token in command)
    held = captured["held"]
    assert os.fstat(held).st_ino == inode
    try:
        # The contained AL client asks for a snapshot through the descriptor.
        result = subprocess.run(
            [sys.executable, "-I", "-c", RELAY_CLIENT, str(SCRIPTS), str(repository)],
            env={"AL_SERVICE_FD": f"{held}:{inode}", "PATH": os.environ["PATH"]},
            pass_fds=(held,),
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"event": "snapshot", "tree": TREE}
        assert seen == [
            {
                "protocol": 1,
                "op": "snapshot",
                "token": "secret-token",
                "repository": str(repository),
            }
        ]
        # Any other request is refused by the relay and never reaches the service.
        with socket.socket(fileno=os.dup(held)) as sock:
            sock.settimeout(10)
            for request in (
                {"op": "run", "repository": str(repository)},
                {"op": "status"},
                {"op": "snapshot", "repository": str(repository), "token": "x"},
                {"op": "subscribe", "paths": ["/"]},
            ):
                sock.sendall(_frame(request))
                assert receive(sock)["event"] == "refused"
        assert len(seen) == 1
    finally:
        os.close(held)


def test_a_stale_descriptor_number_is_not_used(repository):
    """A process that did not inherit the relay may reuse its number for another file."""
    with tempfile.TemporaryFile() as handle:
        fd = handle.fileno()
        result = subprocess.run(
            [sys.executable, "-I", "-c", RELAY_CLIENT, str(SCRIPTS), str(repository)],
            env={"AL_SERVICE_FD": f"{fd}:{os.fstat(fd).st_ino}", "PATH": os.environ["PATH"]},
            pass_fds=(fd,),
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) is None
        assert handle.read() == b""


def test_relay_refuses_a_directory_it_cannot_vouch_for(tmp_path):
    (tmp_path / "token").write_text("t")
    with pytest.raises(ValueError):
        SnapshotRelay(tmp_path)


def test_without_a_service_no_descriptor_is_passed(repository, tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr("harness.execution_isolation.platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        "harness.execution_isolation.subprocess.Popen",
        lambda command, **kwargs: captured.update(kwargs),
    )
    operation_popen(linux_profile(tmp_path, repository, ()), ["al"], env={})
    assert "AL_SERVICE_FD" not in captured["env"] and len(captured["pass_fds"]) == 1


linux_bwrap = pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"), reason="Linux bubblewrap"
)

CONTAINED = r"""
import json, os, socket, sys
sys.path.insert(0, sys.argv[1])
import al_snapshot
out = {'relay': al_snapshot._relay_answer(sys.argv[2], os.environ['AL_SERVICE_FD'])}
for name, path in (('service', sys.argv[3]), ('other', sys.argv[4])):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        out[name] = 'reachable'
    except OSError:
        out[name] = 'refused'
print(json.dumps(out))
"""


@linux_bwrap
def test_contained_linux_command_reaches_only_the_service(repository, service, tmp_path):
    directory, _ = service
    other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    other.bind(str(directory / "t"))
    other.listen()
    profile = linux_profile(tmp_path, repository, (str(directory / "s"),))
    process = operation_popen(
        profile,
        [
            sys.executable,
            "-I",
            "-c",
            CONTAINED,
            str(SCRIPTS),
            str(repository),
            str(directory / "s"),
            str(directory / "t"),
        ],
        env={"PATH": os.environ["PATH"]},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(timeout=60)
    other.close()
    assert process.returncode == 0, stderr
    assert json.loads(stdout) == {
        "relay": {"event": "snapshot", "tree": TREE},
        "service": "refused",
        "other": "refused",
    }
