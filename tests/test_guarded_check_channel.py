"""A trusted ledger process delegates checks to sibling OS sandboxes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.command_safety import safe_environment
from harness.execution_check_channel import CheckChannel
from harness.execution_isolation import operation_popen, prepare_operation_isolation
from tests.native_model_fixture import operation_sandbox_unavailable

pytestmark = pytest.mark.skipif(
    operation_sandbox_unavailable(), reason="Linux operation sandbox capability unavailable"
)


def test_check_channel_preserves_ledger_authority_without_delegating_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    handoff = tmp_path / "handoff"
    operations = handoff / "operations"
    operations.mkdir(parents=True)
    session = repo / ".codex/al-runtime/sessions/test"
    session.mkdir(parents=True)
    ledger = session / "state.json"
    ledger.write_text("before")
    profile = prepare_operation_isolation(
        repo,
        writable=[],
        protected=[handoff],
        hidden=[],
        directory=operations / "parent",
        session_directory=session,
    )
    context = operations / "context.json"
    context.write_text(
        json.dumps(
            {
                "handoff": str(handoff / "handoff.json"),
                "repository": str(repo),
                "temporary": profile.temporary,
                "al_session": str(session),
                "protected": [str(handoff)],
                "hidden": [],
            }
        )
    )
    scripts = Path(__file__).resolve().parents[1] / "al/codex/skills/al/scripts"
    check = (
        "import os;from pathlib import Path\n"
        "assert 'HARNESS_CHECK_FD' not in os.environ\n"
        "for target in " + repr([str(ledger), str(repo / "UNOWNED")]) + ":\n"
        " try: Path(target).write_text('escape')\n"
        " except OSError: print('refused')\n"
        " else: raise SystemExit('check escaped')\n"
    )
    program = (
        "import sys,subprocess;from pathlib import Path;sys.path.insert(0,"
        + repr(str(scripts))
        + ");from al_harness_process import popen;"
        + "p=popen([sys.executable,'-I','-c',"
        + repr(check)
        + "],stdout=subprocess.PIPE,"
        "stderr=subprocess.PIPE);out,err=p.communicate(timeout=15);"
        "assert p.returncode==0,(out,err);assert out.count(b'refused')==2;"
        "Path(" + repr(str(ledger)) + ").write_text('recorded');print('verified')"
    )
    channel = CheckChannel(context, lambda: None)
    try:
        env = safe_environment() | {
            "PATH": os.environ.get("PATH", os.defpath),
            "HARNESS_CHECK_CONTEXT": str(context),
            "HARNESS_CHECK_FD": str(channel.descriptor),
        }
        process = operation_popen(
            profile,
            [sys.executable, "-I", "-c", program],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=(channel.descriptor,),
        )
        channel.spawned()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
        assert ledger.read_text() == "recorded" and not (repo / "UNOWNED").exists()
    finally:
        channel.close()


def test_dispatched_check_gets_short_private_tmpdir_released_at_settlement(tmp_path):
    """Issue #18: a check under a Harness execution can bind AF_UNIX sockets in TMPDIR
    on Darwin (multiprocessing manager). Linux retains its new-Unix-socket denial
    while permitting anonymous stream socketpairs. The check may write only its
    own /tmp directory, which is removed once it has settled."""
    repo = tmp_path / "repo"
    repo.mkdir()
    handoff = tmp_path / "handoff"
    operations = handoff / "operations"
    operations.mkdir(parents=True)
    session = repo / ".codex/al-runtime/sessions/test"
    session.mkdir(parents=True)
    profile = prepare_operation_isolation(
        repo,
        writable=[],
        protected=[handoff],
        hidden=[],
        directory=operations / "parent",
        session_directory=session,
    )
    context = operations / "context.json"
    context.write_text(
        json.dumps(
            {
                "handoff": str(handoff / "handoff.json"),
                "repository": str(repo),
                "temporary": profile.temporary,
                "al_session": str(session),
                "protected": [str(handoff)],
                "hidden": [],
            }
        )
    )
    scripts = Path(__file__).resolve().parents[1] / "al/codex/skills/al/scripts"
    check = (
        "import os,stat,secrets,socket,sys;from pathlib import Path\n"
        "from multiprocessing.managers import SyncManager\n"
        "t=os.environ['TMPDIR'];assert os.environ['TMP']==t==os.environ['TEMP']\n"
        "info=os.stat(t);assert stat.S_IMODE(info.st_mode)==0o700 and info.st_uid==os.getuid()\n"
        "if sys.platform.startswith('linux'):\n"
        " try:socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
        " except PermissionError:pass\n"
        " else:raise SystemExit('new Unix socket escaped Linux denial')\n"
        " a,b=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)\n"
        " try:a.sendall(b'private-ipc');assert b.recv(11)==b'private-ipc'\n"
        " finally:a.close();b.close()\n"
        "else:\n"
        " m=SyncManager();m.start();m.shutdown()\n"
        "Path(t,'own').write_text('ok')\n"
        "try: Path('/tmp/h-escape-'+secrets.token_hex(6)).write_text('escape')\n"
        "except OSError: pass\n"
        "else: raise SystemExit('wrote outside its temporary directory')\n"
        "print('TMP='+t)\n"
    )
    program = (
        "import sys,subprocess;sys.path.insert(0," + repr(str(scripts)) + ");"
        "from al_harness_process import popen;"
        "p=popen([sys.executable,'-I','-c'," + repr(check) + "],stdout=subprocess.PIPE,"
        "stderr=subprocess.PIPE);out,err=p.communicate(timeout=30);"
        "assert p.returncode==0,(out,err);sys.stdout.write(out.decode())"
    )
    channel = CheckChannel(context, lambda: None)
    try:
        env = safe_environment() | {
            "PATH": os.environ.get("PATH", os.defpath),
            "HARNESS_CHECK_CONTEXT": str(context),
            "HARNESS_CHECK_FD": str(channel.descriptor),
        }
        process = operation_popen(
            profile,
            [sys.executable, "-I", "-c", program],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=(channel.descriptor,),
        )
        channel.spawned()
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        channel.close()
    used = Path(stdout.decode().split("TMP=")[1].split()[0])
    assert used.parent == Path("/tmp") and used.name.startswith("h-")
    assert not used.exists()
