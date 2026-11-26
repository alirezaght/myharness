"""Transport guard tests, separate from Harness source/profile qualification proof."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading


def test_private_context_absent_preserves_ordinary_installer_behavior():
    from al.installer.runtime_retry_control import current
    assert current() is None


def test_environment_flag_cannot_activate_without_authenticated_channel(tmp_path):
    source = Path(__file__).resolve().parents[1]
    code = "import sys;sys.path.insert(0,sys.argv[1]);from installer.runtime_retry_control import activate;activate('launcher',repository=sys.argv[2],platform='codex',session='test',argv=['retry','al-engineer','--reason','fixed'])"
    env = dict(os.environ, HARNESS_RETRY_CONTROL_FD="999999", HARNESS_RETRY_CONTROL_NONCE="forged")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(source), str(tmp_path)],
                            env=env, capture_output=True, timeout=10)
    assert result.returncode != 0


def test_authenticated_transport_installs_unwrapped_spawn_and_exec_guard(tmp_path):
    from al.installer.runtime_retry_control import PROTOCOL

    server, child = socket.socketpair()
    nonce = "transport-fixture-nonce"
    source = Path(__file__).resolve().parents[1]
    errors = []

    def serve():
        try:
            def exact(size):
                raw = bytearray()
                while len(raw) < size:raw.extend(server.recv(size-len(raw)))
                return bytes(raw)
            request = json.loads(exact(struct.unpack("!I", exact(4))[0]))
            assert request["method"] == "activate" and request["nonce"] == nonce
            value = {"sequence": 1, "result": {"protocol": PROTOCOL, "profile": "sealed-al-retry-v1", "repository": str(tmp_path),
                     "interpreter": sys.executable, "pin_identity": "fixture"}}
            raw = json.dumps(value).encode()
            server.sendall(struct.pack("!I", len(raw)) + raw)
        except BaseException as error:errors.append(error)
        finally:server.close()

    thread = threading.Thread(target=serve);thread.start()
    code = r'''
import os,sys,subprocess
sys.path.insert(0,sys.argv[1])
from installer.runtime_retry_control import activate
activate('launcher',repository=sys.argv[2],platform='codex',session='test',argv=['retry','al-engineer','--reason','fixed'])
for action in (lambda:os.fork(),lambda:subprocess.run([sys.executable,'-c','raise SystemExit(99)']),lambda:os.execv(sys.executable,[sys.executable,'-c','raise SystemExit(99)'])):
    try:action()
    except ValueError:pass
    else:raise RuntimeError('unwrapped spawn escaped')
print('guarded')
'''
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(source), str(tmp_path)],
              env=dict(os.environ, HARNESS_RETRY_CONTROL_FD=str(child.fileno()), HARNESS_RETRY_CONTROL_NONCE=nonce),
              pass_fds=(child.fileno(),), capture_output=True, timeout=10)
    child.close();thread.join(10)
    assert not thread.is_alive() and not errors
    assert result.returncode == 0 and result.stdout == b"guarded\n", result.stderr


def test_managed_isolated_no_site_bootstrap_ignores_startup_canaries(tmp_path):
    import venv
    from al.tests.conftest import copy_al_source

    source = copy_al_source(tmp_path / "source/al")
    home = tmp_path / "home";home.mkdir()
    repository = tmp_path / "repository";repository.mkdir()
    virtual = tmp_path / "private-interpreter"
    # A copied uv-managed macOS executable can lose its relative libpython path.
    # Keep the genuine base executable/library relationship and private venv site.
    venv.EnvBuilder(with_pip=False, symlinks=True).create(virtual)
    interpreter = virtual / "bin/python"
    canary = tmp_path / "startup-executed"
    payload = "from pathlib import Path;Path(" + repr(str(canary)) + ").write_text('unapproved startup')\n"
    sites = list(virtual.glob("lib/python*/site-packages"))
    assert len(sites) == 1
    (sites[0] / "injection.pth").write_text(str(repository) + "\n" + payload)
    for name in ("sitecustomize.py", "usercustomize.py"):
        (repository / name).write_text(payload)
    startup = tmp_path / "startup.py";startup.write_text(payload)
    environment = dict(os.environ, HOME=str(home), PYTHONPATH=str(repository), PYTHONSTARTUP=str(startup))
    for key in ("AL_RUNTIME_DIR", "AL_DISTRIBUTION_STATE", "PYTHONHOME"):
        environment.pop(key, None)
    # Prove that startup injection is live in this private environment before
    # requiring the isolated no-site bootstrap to suppress it.
    control = subprocess.run([str(interpreter), "-B", "-c", "pass"], cwd=repository,
                             env=environment, capture_output=True, timeout=15)
    assert control.returncode == 0, control.stderr
    assert canary.read_text() == 'unapproved startup'
    canary.unlink()
    git = "/Library/Developer/CommandLineTools/usr/bin/git" if sys.platform == "darwin" else "/usr/bin/git"
    subprocess.run([git, "init", "-q"], cwd=repository, env=environment, check=True)
    subprocess.run([git, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "--allow-empty", "-qm", "initial"], cwd=repository,
                    env=environment, check=True, capture_output=True)
    launcher = home / "bin/alctl"
    bootstrap = subprocess.run([str(interpreter), "-I", "-S", "-B", str(source / "alctl.py"),
        "bootstrap", "--source-root", str(source), "--destination", str(launcher),
        "--state-root", str(home / "installer")], cwd=repository, env=environment,
        capture_output=True, timeout=40)
    assert bootstrap.returncode == 0, bootstrap.stderr
    result = subprocess.run([str(launcher), "run", "--repository", str(repository), "--platform", "codex",
        "--session", "startup-fixture", "--", "start", "--spec", "A bounded isolated fixture."],
        cwd=repository, env=environment, capture_output=True, timeout=40)
    assert result.returncode == 0, result.stderr
    assert not canary.exists()


def test_sealed_control_denies_all_bound_process_creation_paths(tmp_path):
    import pytest
    from types import SimpleNamespace
    from harness.execution_isolation import prepare_operation_isolation
    from harness.policy.control_boundary import prepare

    if sys.platform != 'darwin':pytest.skip('this qualified profile is macOS-specific')
    repository=tmp_path/'repository';repository.mkdir()
    operation=SimpleNamespace(envelope={'repository':str(repository)},
        profile=lambda writable:prepare_operation_isolation(repository,writable=writable,
            protected=[],hidden=[],directory=tmp_path/'strict-control-profile'))
    profile,state=prepare(operation,sys.executable)
    assert state['path']==profile.policy
    assert '(deny process-fork)' in Path(profile.policy).read_text()
