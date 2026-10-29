"""Own and reap the one isolated initialization child and its bounded channel."""

import os
import secrets
import selectors
import signal
import socket
import subprocess
import time

from . import runtime_successor_store as Store
from .owned_process import OwnedProcess
from .runtime_pin_boot import program
from .runtime_pin_transaction import storage
from .runtime_successor_channel import InitializationChannel


def run_child(
    coordinator,
    transaction,
    verified,
    argv,
    descriptor,
    lineage_ref,
    validate_start,
    initialize,
    directory,
    journal_path,
    journal,
):
    from .registry import write_json
    from .runtime_start_isolation import launch

    parent, child = socket.socketpair()
    nonce = secrets.token_hex(32)
    server = InitializationChannel(
        parent,
        nonce,
        Store.read_reference(lineage_ref)["preparation_sha256"],
        validate_start,
        initialize,
    )
    request = {
        "transaction": transaction,
        "argv": argv,
        "storage": str(storage()),
        "existing_run": False,
        "native_implicit": False,
        "prepared_start": {
            "descriptor": child.fileno(),
            "attempt_nonce": nonce,
            "preparation_sha256": server.preparation_sha256,
        },
    }
    command = launch(
        descriptor, verified["manifest"]["interpreter"]["executable"], program(request)
    )
    owner = OwnedProcess(
        directory / ("attempt-" + nonce), "prepared-start-" + transaction["run_id"]
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": descriptor["scratch"],
        "TMPDIR": descriptor["scratch"],
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    failure = None
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}

    def interrupted(number, frame):
        raise InterruptedError("prepared start interrupted")

    for number in previous:
        signal.signal(number, interrupted)
    try:
        process = owner.spawn(
            command,
            cwd=transaction["repository_identity"]["root"],
            env=env,
            output=subprocess.PIPE,
            pass_fds=(child.fileno(),),
        )
        child.close()
        journal.update(state="STARTING", ownership=owner.record())
        write_json(journal_path, journal)
        total, deadline = 0, time.monotonic() + 300
        with (
            selectors.DefaultSelector() as selector,
            (directory / ("private-output-" + nonce)).open("xb") as output,
        ):
            os.fchmod(output.fileno(), 0o600)
            selector.register(parent, selectors.EVENT_READ, "initialize")
            selector.register(coordinator, selectors.EVENT_READ, "disconnect")
            selector.register(process.stdout, selectors.EVENT_READ, "output")
            while process.poll() is None or any(
                k.data == "output" for k in selector.get_map().values()
            ):
                if time.monotonic() >= deadline:
                    raise TimeoutError("prepared start deadline exceeded")
                for key, _ in selector.select(0.1):
                    if key.data == "initialize":
                        try:
                            server.service_one()
                        except EOFError:
                            if server.next_sequence != 3:
                                raise
                            selector.unregister(parent)
                    elif key.data == "disconnect":
                        raise EOFError("prepared coordinator disconnected during start")
                    else:
                        raw = os.read(process.stdout.fileno(), 4096)
                        if not raw:
                            selector.unregister(process.stdout)
                        else:
                            total += len(raw)
                            if total > 65536:
                                raise ValueError("prepared start private output exceeded bound")
                            output.write(raw)
            if process.returncode != 0:
                raise ValueError("contained prepared start failed; private diagnostic retained")
    except BaseException as error:
        failure = error
    finally:
        cleanup = owner.cleanup()
        journal.update(cleanup=cleanup)
        write_json(journal_path, journal)
        for number, handler in previous.items():
            signal.signal(number, handler)
        if owner.process and owner.process.stdout:
            owner.process.stdout.close()
        child.close()
        server.close()
    if failure is not None:
        raise failure
    if not cleanup["complete"]:
        raise ValueError("prepared child process settlement is incomplete")
