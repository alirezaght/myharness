"""Owned group completion must not claim that detached descendants are gone."""

import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import sys
import time
import tempfile

import pytest

from al.installer.activity_process import identity
from al.installer.owned_process import OwnedProcess
from al.verification_plan import (
    allocate_verification_storage,
    finalize_verification_storage,
    record_verification_storage,
)


DETACHED_FIXTURE = r"""
import json,os,select,socket,sys,time
from pathlib import Path
control,token,mode,output=sys.argv[1:]
first=os.fork()
if first:
    if mode=='double':os.waitpid(first,0)
    raise SystemExit(0)
os.setsid()
if mode=='double':
    second=os.fork()
    if second:os._exit(0)
    os.setsid()
for descriptor in (0,1,2):
    try:os.close(descriptor)
    except OSError:pass
connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
connection.settimeout(5)
connection.connect(control)
connection.sendall((json.dumps({'token':token,'pid':os.getpid()})+'\n').encode())
deadline=time.monotonic()+15
try:
    while time.monotonic()<deadline:
        if select.select([connection],[],[],.02)[0]:
            command=connection.recv(16)
            if command==b'STOP' or not command:break
        try:Path(output,'still-producing').write_text('fixture remains live')
        except OSError:pass
    connection.sendall(b'STOPPED')
finally:
    connection.close()
    os._exit(0)
"""


def _same_process(pid, original):
    observed = identity(pid)
    return (
        observed is not None
        and observed["start"] == original["start"]
        and observed["sid"] == original["sid"]
    )


def _stop_fixture(connection, pid, original):
    if connection is not None:
        try:
            connection.sendall(b"STOP")
            connection.settimeout(3)
            assert connection.recv(16) == b"STOPPED"
        except (OSError, AssertionError):
            if original is not None and _same_process(pid, original):
                os.killpg(original["pgid"], signal.SIGKILL)
        finally:
            connection.close()
    if original is None:
        return
    # The detached orphan is reaped by the OS, not by this non-parent test driver.
    # Private stop control and its fixed bounded source establish fixture ownership;
    # this observation is never promoted into general descendant settlement proof.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            observed = identity(pid)
        except OSError:
            time.sleep(0.02)
            continue
        if observed is None or observed["start"] != original["start"]:
            return
        if observed["zombie"]:
            time.sleep(0.02)
            continue
        time.sleep(0.02)
    raise AssertionError("controlled detached fixture was not observed reaped")


@pytest.mark.parametrize("mode", ["setsid", "double"])
def test_real_detached_producer_prevents_group_only_verification_cleanup(
    tmp_path, monkeypatch, mode
):
    if sys.platform not in ("darwin", "linux"):
        pytest.skip("supported kernel process identities require macOS or Linux")
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "private-home"))
    durable = tmp_path / "durable"
    durable.mkdir(mode=0o700)
    manager, binding = allocate_verification_storage(durable, "controlled-detached-" + mode)
    allocation = binding["allocation"]
    control_root = Path(tempfile.mkdtemp(prefix="hs-settlement-", dir="/private/tmp"))
    control = control_root / "s.sock"
    token = secrets.token_hex(16)
    connection = None
    pid = None
    observed = None
    owner = OwnedProcess(durable / "process", "controlled-detached-" + mode)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(control))
        listener.listen(1)
        listener.settimeout(5)
        try:
            log = durable / "output.log"
            with log.open("wb") as output:
                process = owner.spawn(
                    [
                        sys.executable,
                        "-I",
                        "-S",
                        "-c",
                        DETACHED_FIXTURE,
                        str(control),
                        token,
                        mode,
                        allocation["path"],
                    ],
                    cwd=tmp_path,
                    env={"PATH": os.defpath},
                    output=output,
                )
                connection, _ = listener.accept()
                message = bytearray()
                while not message.endswith(b"\n") and len(message) <= 4096:
                    message.extend(connection.recv(4096))
                value = json.loads(message)
                if value.get("token") != token or type(value.get("pid")) is not int:
                    raise AssertionError("controlled fixture identity refused")
                pid = value["pid"]
                observed = identity(pid)
                assert observed is not None and not observed["zombie"]
                assert observed["pgid"] != process.pid and observed["sid"] != process.pid
                assert process.wait(timeout=5) == 0
                cleanup = owner.cleanup()
            assert cleanup["complete"] and owner.members() == []
            assert _same_process(pid, observed), (
                "detached producer must still be live for causal case"
            )
            storage_result = record_verification_storage(
                durable,
                manager,
                binding,
                owner,
                success=True,
                complete=cleanup["complete"],
                artifacts=[log],
            )
            report = durable / "observed-result.json"
            report.write_text('{"observed_test_exit":0}')
            result = finalize_verification_storage(report, references=[storage_result])
            assert result["results"][0]["state"] == "RETAINED"
            assert result["results"][0]["reason"] == "descendant_settlement_unproven"
            assert Path(allocation["path"]).is_dir()
        finally:
            try:
                _stop_fixture(connection, pid, observed)
                final = owner.cleanup()
                assert final["complete"] and owner.process.poll() is not None
            finally:
                control.unlink(missing_ok=True)
                control_root.rmdir()
