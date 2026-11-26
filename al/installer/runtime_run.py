"""Forward public AL commands through a protected immutable runtime pin."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

from .interpreter_execution import launcher, validate_launch
from .runtime_pin import _directory, prepare_pin
from .runtime_pin_cache import verify_pin_cached
from .runtime_pin_boot import program
from .runtime_pin_transaction import coordinated, publication_lock, read, reserve_locked, storage, subject, validate
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


def _writer_progress(started, message):
    if started is None or sys.stderr is None:
        return
    try:
        print(f"AL writer start [{time.monotonic() - started:.1f}s] {message}",
              file=sys.stderr, flush=True)
    except (OSError, ValueError):
        # Diagnostics must not change admission when stderr is closed/unavailable.
        pass


def _managed_issuer():
    root = Path(__file__).resolve().parents[1]
    context = sys.modules.get('_al_validator_context')
    if (context is None or context.request.get('mode') != 'snapshot'
            or Path(context.request['root']) != root):
        raise ValueError('run requires its protected managed issuer context')
    trusted = verify_snapshot(root, context.request['expected'])
    interpreter = trusted['origin']['interpreter']
    if context.request['interpreter'] != interpreter['executable']:
        raise ValueError('managed issuer interpreter binding changed')
    launcher(interpreter)
    return trusted


def run(repository, platform, session, argv, *, existing_run=False, private_session=False):
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise ValueError("run requires the trusted isolated managed launcher")
    if not argv or argv[0].startswith("-"):
        raise ValueError("run requires an explicit AL operation")
    for token in argv[: argv.index("--") if "--" in argv else len(argv)]:
        if token.startswith("--") and "--session".startswith(token.split("=")[0]):
            raise ValueError("session is supplied only by the trusted run launcher")
    if "AL_RUNTIME_DIR" in os.environ:
        raise ValueError("new pinned runs do not accept an alternate runtime directory")
    if existing_run and (session is None or argv[0] in {"start", "clear"}):
        raise ValueError("existing-run requires explicit initialized session and non-start operation")
    if private_session and (existing_run or argv[0] != "start" or "--handoff" not in argv):
        raise ValueError("session-directory applies only to new supervisor Harness starts")
    trusted = _managed_issuer()
    progress_started = time.monotonic() if list(argv[:2]) == ["writer", "start"] else None
    _writer_progress(progress_started, "validating repository and authority")
    implicit = session is None
    if implicit:
        session = native_session(repository, platform, argv)
    from .runtime_retry_control import activate
    control = activate("launcher", repository=repository, platform=platform, session=session, argv=argv)
    value = subject(repository, platform, session)
    from .runtime_successor_store import require_ordinary_authority
    storage(readonly=existing_run)
    require_ordinary_authority(value)
    native_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if native_root and Path(native_root).resolve() != Path(value["repository_identity"]["root"]):
        raise ValueError("native project directory differs from protected repository")
    # The managed launcher verifies its immutable origin before importing this
    # helper. Target paths never select a runtime distribution or pin store.
    _writer_progress(progress_started, "acquiring protected admission lock")
    with coordinated(value, readonly=existing_run) as path:
        transaction = read(path) if path.exists() else None
        if transaction:
            validate(transaction, value)
    if private_session and transaction is not None and transaction["schema_version"] not in {2, 3}:
        raise ValueError("legacy flat AL run requires explicit migration before session-directory enrollment")
    if existing_run and (transaction is None or transaction["status"] != "ACTIVE"):
        raise ValueError("existing-run requires ACTIVE protected binding")
    if transaction is None:
        if argv[0] != "start":
            raise ValueError("no protected run binding; use the original issuer for legacy runs")
        pin = prepare_pin(Path(trusted["snapshot"]), platform, _directory(storage() / "pins"))
        harness_binding = None
        if private_session:
            from .runtime_harness_start import binding_for_start
            harness_binding = binding_for_start(value, argv)
        with publication_lock(value) as capability:
            transaction = reserve_locked(capability, pin, private_session=private_session, harness_binding=harness_binding)
    if list(argv) == ["clear"] and not implicit:
        # Clearing the active pointer reads no run state, so the current launcher does it itself: a run
        # whose pinned runtime can no longer read its own state can still be put aside.
        pointer = Path(value["repository_identity"]["root"]) / ("." + platform) / "al-runtime" / "current"
        if pointer.is_file() and pointer.read_text().strip() == session:
            pointer.unlink()
        print("AL state cleared")
        return 0
    _writer_progress(progress_started, "verifying immutable runtime resources")
    # Skips only an unchanged payload re-hash after an earlier full verification (#36); the
    # manifest checks always run and any change to the pin verifies it in full.
    verified = verify_pin_cached(storage(readonly=existing_run) / "pins", transaction["pin"],
                                 create=not existing_run and not control, write=not bool(control))
    manifest = verified["manifest"]
    validate_launch(
        manifest["interpreter_execution_identity"], manifest["interpreter"],
        manifest["interpreter"]["executable"],
    )
    # Later commands of this run go to the resident service (#27) when its pin carries one;
    # this command still runs in process below.
    from .runtime_service_boot import ensure as ensure_service
    if not control:
        ensure_service(storage(readonly=existing_run), repository, platform, None if implicit else session, transaction,
                       manifest, argv, existing_run=existing_run, private_session=private_session)
    request = {"transaction": transaction, "argv": list(argv), "storage": str(storage(readonly=existing_run)), "existing_run": existing_run,
               "native_implicit": implicit}
    executable = manifest["interpreter"]["executable"]
    _writer_progress(progress_started,
                     "delegating to pinned runtime for admission checks and repository snapshot")
    child_argv = [executable, "-I", "-S", "-B", "-c", program(request)]
    if control:control.allow_exec(executable, child_argv)
    os.execv(executable, child_argv)
