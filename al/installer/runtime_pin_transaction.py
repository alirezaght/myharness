"""Coordinate new-run pins outside repository-controlled runtime state."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import sys

from .registry import write_json
from .runtime_pin import _directory
from .validator_origin import canonical, capture, decode, digest, repository_identity, safe


def storage(*, readonly=False):
    pinned = sys.modules.get("_al_run_pin_context")
    if pinned is not None:
        return _directory(Path(pinned.storage), create=not (readonly or getattr(pinned, "readonly", False) or getattr(pinned, "initialization_client", None) is not None))
    trusted = sys.modules.get("_al_validator_context")
    if trusted is None or trusted.request.get("mode") != "snapshot":
        raise ValueError("runtime storage requires the protected managed launcher")
    root = safe(trusted.request["root"]).resolve(strict=True)
    if (
        root.parent.name != "snapshots"
        or root.parent.parent.name != "cli"
        or root.parent.parent.parent.name != "installer"
    ):
        raise ValueError("unrecognized managed runtime registry layout")
    return _directory(root.parents[3] / "al-run-pins", create=not readonly)


def subject(repository, platform, session):
    if platform not in {"codex", "claude"} or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", session
    ):
        raise ValueError("explicit safe platform and session required")
    root = safe(repository).resolve(strict=True)
    if root != Path.cwd().resolve():
        raise ValueError("run launcher repository and current directory differ")
    return {
        "repository_identity": repository_identity(root),
        "platform": platform,
        "session_id": session,
    }


def key(value):
    return digest(canonical(value))


def read(path):
    raw, state = capture(path)
    if state["mode"] != 0o600:
        raise ValueError("runtime pin transaction metadata mode changed")
    return decode(raw)


class PublicationLock:
    def __init__(self, value, path, descriptor):
        self.value, self.path, self.descriptor = value, path, descriptor
        state = os.fstat(descriptor)
        self.identity = (state.st_dev, state.st_ino)
        self.active = True

    def validate(self, value):
        if not self.active or self.value != value:
            raise ValueError('publication lock capability differs')
        state = os.fstat(self.descriptor)
        disk = self.path.with_suffix('.lock').lstat()
        if (not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid()
                or stat.S_IMODE(state.st_mode) != 0o600 or state.st_nlink != 1
                or (state.st_dev, state.st_ino) != self.identity
                or (disk.st_dev, disk.st_ino) != self.identity):
            raise ValueError('publication lock identity changed')
        fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def publication_lock(value, *, readonly=False):
    context = sys.modules.get('_al_run_pin_context')
    readonly = readonly or bool(getattr(context, 'readonly', False))
    directory = _directory(storage(readonly=readonly) / 'runs', create=not readonly)
    lock = directory / (key(value) + '.lock')
    safe(lock)
    descriptor = os.open(lock, (0 if readonly else os.O_CREAT) | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    capability = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        capability = PublicationLock(value, directory / (key(value) + '.json'), descriptor)
        capability.validate(value)
        yield capability
    finally:
        if capability is not None:
            capability.active = False
        os.close(descriptor)


@contextmanager
def coordinated(value, *, readonly=False):
    with publication_lock(value, readonly=readonly) as capability:
        yield capability.path


LAYOUT = "harness-session-directory-v1"
LAYOUT_FIELDS = {"runtime_layout", "runtime_directory"}


def runtime_directory(value):
    base = Path(value["repository_identity"]["root"]) / ("." + value["platform"]) / "al-runtime"
    if value.get("schema_version") == 2:
        expected = base / "sessions" / value["session_id"]
        if value.get("runtime_layout") != LAYOUT or value.get("runtime_directory") != str(expected):
            raise ValueError("protected session runtime layout differs")
        return safe(expected)
    return safe(base)


def state_path(value):
    return runtime_directory(value) / (value["session_id"] + ".json")


def _legacy_state_path(value):
    return (
        Path(value["repository_identity"]["root"])
        / ("." + value["platform"])
        / "al-runtime"
        / (value["session_id"] + ".json")
    )


def reserve(value, pin, *, private_session=False):
    with publication_lock(value) as capability:
        return reserve_locked(capability, pin, private_session=private_session)


def reserve_locked(capability, pin, *, private_session=False):
    value, path = capability.value, capability.path
    capability.validate(value)
    if path.exists():
        transaction = read(path)
        validate(transaction, value)
        return transaction
    if state_path(value).exists():
        raise ValueError("existing run requires its original issuing runtime")
    transaction = {
        "schema_version": 1,
        "kind": "run-pin-binding-v1",
        **value,
        "run_id": secrets.token_hex(4),
        "publication_transaction": secrets.token_hex(32),
        "pin": pin,
        "status": "RESERVED",
        "initialization": None,
    }
    if private_session:
        directory = Path(value["repository_identity"]["root"]) / ("." + value["platform"]) / "al-runtime" / "sessions" / value["session_id"]
        transaction.update(schema_version=2, kind="run-pin-binding-v2",
                           runtime_layout=LAYOUT, runtime_directory=str(safe(directory)))
        _directory(directory)
    write_json(path, transaction)
    return transaction


def validate(transaction, value):
    fields = {
        "schema_version",
        "kind",
        "repository_identity",
        "platform",
        "session_id",
        "run_id",
        "publication_transaction",
        "pin",
        "status",
        "initialization",
    }
    version = transaction.get("schema_version") if isinstance(transaction, dict) else None
    expected_fields = fields | LAYOUT_FIELDS if version == 2 else fields
    if (
        not isinstance(transaction, dict)
        or set(transaction) != expected_fields
        or version not in {1, 2}
        or transaction["kind"] != "run-pin-binding-v" + str(version)
        or transaction["status"] not in {"RESERVED", "ACTIVE"}
        or any(transaction.get(k) != v for k, v in value.items())
    ):
        raise ValueError("protected runtime run binding differs")
    runtime_directory(transaction)
    if not re.fullmatch(r"[0-9a-f]{8}", transaction["run_id"]) or not re.fullmatch(
        r"[0-9a-f]{64}", transaction["publication_transaction"]
    ):
        raise ValueError("invalid reserved runtime run identity")


def commit_run_binding(transaction):
    value = {k: transaction[k] for k in ('repository_identity', 'platform', 'session_id')}
    with publication_lock(value) as capability:
        return commit_run_binding_locked(capability, transaction)


def commit_run_binding_locked(capability, transaction):
    value, path = capability.value, capability.path
    capability.validate(value)
    current = read(path)
    validate(current, value)
    if current != transaction:
        raise ValueError("runtime transaction moved before state publication")
    state = read_state(transaction)
    if state is None:
        raise ValueError("reserved runtime did not publish matching state")
    if current["status"] == "RESERVED":
        if not _published(transaction):
            raise ValueError("runtime start pointer publication is incomplete")
        write_json(path, dict(current, status="ACTIVE"))


def _published(transaction):
    """The start published its active pointer: `current`, or a Claude conversation's pointer."""
    runtime = state_path(transaction).parent
    session = transaction["session_id"]
    pointer = runtime / "current"
    if pointer.exists():
        raw, _ = capture(pointer)
        if raw == (session + "\n").encode():
            return True
    native = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if transaction["platform"] != "claude" or not native:
        return False
    import hashlib
    conversation = runtime / "conversations" / (hashlib.sha256(native.encode()).hexdigest() + ".json")
    if not conversation.exists():
        return False
    raw, _ = capture(conversation)
    return decode(raw) == {"conversation_id": native, "session_id": session}


def read_state(transaction):
    path = state_path(transaction)
    if not path.exists():
        return None
    state = read(path)
    expected = {
        k: transaction[k]
        for k in (
            "kind",
            "schema_version",
            "run_id",
            "publication_transaction",
            "pin",
            "initialization",
        )
    }
    if transaction["schema_version"] == 2:
        expected.update({k: transaction[k] for k in LAYOUT_FIELDS})
    if (
        state.get("session_id") != transaction["session_id"]
        or state.get("run_id") != transaction["run_id"]
        or state.get("runtime_origin") != expected
        or state.get("identity", {}).get("root") != transaction["repository_identity"]["root"]
    ):
        raise ValueError("state and independently protected runtime binding differ")
    return state
