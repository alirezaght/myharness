"""Forward public AL commands through a protected immutable runtime pin."""

import os
from pathlib import Path
import sys

from .interpreter_execution import validate_live
from .execution_process import observe
from .runtime_pin import _directory, prepare_pin, verify_pin
from .runtime_pin_boot import program
from .runtime_pin_transaction import coordinated, read, reserve, storage, subject, validate
from .validator_origin import verify_snapshot


def run(repository, platform, session, argv):
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise ValueError("run requires the trusted isolated managed launcher")
    if not argv or argv[0].startswith("-"):
        raise ValueError("run requires an explicit AL operation")
    for token in argv[: argv.index("--") if "--" in argv else len(argv)]:
        if token.startswith("--") and "--session".startswith(token.split("=")[0]):
            raise ValueError("session is supplied only by the trusted run launcher")
    if "AL_RUNTIME_DIR" in os.environ:
        raise ValueError("new pinned runs do not accept an alternate runtime directory")
    value = subject(repository, platform, session)
    native_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if native_root and Path(native_root).resolve() != Path(value["repository_identity"]["root"]):
        raise ValueError("native project directory differs from protected repository")
    # The managed launcher verifies its immutable origin before importing this
    # helper. Target paths never select a runtime distribution or pin store.
    with coordinated(value) as path:
        transaction = read(path) if path.exists() else None
        if transaction:
            validate(transaction, value)
    if transaction is None:
        if argv[0] != "start":
            raise ValueError("no protected run binding; use the original issuer for legacy runs")
        root = Path(__file__).resolve().parents[1]
        context = sys.modules.get("_al_validator_context")
        if context is not None:
            request = context.request
            if request["mode"] != "snapshot" or Path(request["root"]) != root:
                raise ValueError("untrusted runtime distribution context")
            trusted = verify_snapshot(root, request["expected"])
        else:
            raise ValueError("new runtime requires its protected managed launcher")
        pin = prepare_pin(Path(trusted["snapshot"]), platform, _directory(storage() / "pins"))
        transaction = reserve(value, pin)
    verified = verify_pin(storage() / "pins", transaction["pin"])
    manifest = verified["manifest"]
    validate_live(
        manifest["interpreter_execution_identity"], manifest["interpreter"], observe(os.getpid())
    )
    request = {"transaction": transaction, "argv": list(argv), "storage": str(storage())}
    executable = manifest["interpreter"]["executable"]
    os.execv(executable, [executable, "-I", "-S", "-B", "-c", program(request)])
