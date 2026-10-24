"""Forward public AL commands through a protected immutable runtime pin."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

from .interpreter_execution import validate_live
from .execution_process import observe
from .runtime_pin import _directory, prepare_pin, verify_pin
from .runtime_pin_boot import program
from .runtime_pin_transaction import coordinated, read, reserve, storage, subject, validate
from .validator_origin import verify_snapshot


def native_session(repository, platform, argv):
    """Inside a Claude conversation, mint (start) or resolve (otherwise) the conversation's run id.

    The conversation pointer only selects which existing protected binding is used;
    the binding itself is still validated against repository, platform and session."""
    native = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if platform != "claude" or not native or len(native) > 1024:
        raise ValueError("run requires --session outside a Claude conversation")
    if argv[0] == "start":
        return "al-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
    root = Path(repository).resolve(strict=True)
    path = root / ".claude" / "al-runtime" / "conversations" / (hashlib.sha256(native.encode()).hexdigest() + ".json")
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("no active AL run for this Claude conversation; start one or pass --session")
        data = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("corrupt native conversation pointer") from exc
    if not isinstance(data, dict) or set(data) != {"conversation_id", "session_id"} or data["conversation_id"] != native:
        raise ValueError("corrupt native conversation pointer")
    return data["session_id"]


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
    implicit = session is None
    if implicit:
        session = native_session(repository, platform, argv)
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
    if list(argv) == ["clear"] and not implicit:
        # Clearing the active pointer reads no run state, so the current launcher does it itself: a run
        # whose pinned runtime can no longer read its own state can still be put aside.
        pointer = Path(value["repository_identity"]["root"]) / ("." + platform) / "al-runtime" / "current"
        if pointer.is_file() and pointer.read_text().strip() == session:
            pointer.unlink()
        print("AL state cleared")
        return 0
    verified = verify_pin(storage() / "pins", transaction["pin"])
    manifest = verified["manifest"]
    validate_live(
        manifest["interpreter_execution_identity"], manifest["interpreter"], observe(os.getpid())
    )
    request = {"transaction": transaction, "argv": list(argv), "storage": str(storage()),
               "native_implicit": implicit}
    executable = manifest["interpreter"]["executable"]
    os.execv(executable, [executable, "-I", "-S", "-B", "-c", program(request)])
