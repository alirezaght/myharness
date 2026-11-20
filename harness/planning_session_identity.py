"""Local cooperative ownership capability; provider IDs are correlation, not approval."""

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from harness.authoring_contract import canonical, digest, exact
from harness.planning_session_contract import SessionError, identifier


def resolve_caller(provider, environment, claude_session_id=None):
    try:
        if provider == "codex":
            root, current = environment.get("CODEX_SESSION_ID"), environment.get("CODEX_THREAD_ID")
        elif provider == "claude":
            root = current = claude_session_id
        else:
            raise ValueError
        identifier(root)
        identifier(current)
        return {"provider": provider, "root_session_id": root, "current_thread_id": current}
    except ValueError:
        raise SessionError("SESSION_IDENTITY_UNAVAILABLE") from None


def verifier(handle):
    return hashlib.sha256(handle["secret"].encode()).hexdigest()


def verify_capability(handle, stored_verifier):
    return isinstance(stored_verifier, str) and hmac.compare_digest(
        verifier(handle), stored_verifier
    )


def _directory(location, *, create=False):
    path = Path(location).absolute()
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SessionError("SESSION_CAPABILITY_INVALID")
        return path, descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _preparation_lock(directory, path):
    deadline = time.monotonic() + 5
    while True:
        observed_path, observed_fd = _directory(path)
        try:
            expected, observed = os.fstat(directory), os.fstat(observed_fd)
            if expected.st_nlink == 0 or (expected.st_dev, expected.st_ino) != (
                observed.st_dev,
                observed.st_ino,
            ):
                raise SessionError("SESSION_CAPABILITY_INVALID")
        finally:
            os.close(observed_fd)
        try:
            fd = os.open(
                ".prepare.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            break
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise SessionError("SESSION_CAPABILITY_INVALID")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SessionError("SESSION_CAPABILITY_INVALID") from None
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


def prepare_capability_artifact(request_identity, binding, location):
    exact(request_identity, {"project", "request_id"})
    try:
        directory, descriptor = _directory(location, create=True)
        name = digest({"request": request_identity, "caller": binding}) + ".json"
        path = directory / name
        try:
            with _preparation_lock(descriptor, directory):
                try:
                    fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=descriptor,
                    )
                except FileExistsError:
                    existing = read_capability(path, binding)
                    if any(existing[key] != expected for key, expected in request_identity.items()):
                        raise SessionError("SESSION_CAPABILITY_INVALID") from None
                    return existing
                value = {
                    "schema_version": 1,
                    **request_identity,
                    "binding": binding,
                    "attachment_id": str(uuid.uuid4()),
                    "secret": secrets.token_hex(32),
                }
                with os.fdopen(fd, "w") as stream:
                    stream.write(canonical(value))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return read_capability(path, binding)
    except OSError:
        raise SessionError("SESSION_CAPABILITY_INVALID") from None


def read_capability(path, expected_binding):
    path = Path(path).absolute()
    try:
        _, directory = _directory(path.parent)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > 8192
            ):
                raise ValueError
            value = json.loads(stream.read(8193))
        exact(
            value, {"schema_version", "project", "request_id", "binding", "attachment_id", "secret"}
        )
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["binding"] != expected_binding
        ):
            raise ValueError
        identifier(value["attachment_id"])
        if (
            not isinstance(value["secret"], str)
            or len(value["secret"]) != 64
            or any(c not in "0123456789abcdef" for c in value["secret"])
        ):
            raise ValueError
        return value | {"path": str(path)}
    except (OSError, ValueError, KeyError, TypeError):
        raise SessionError("SESSION_CAPABILITY_INVALID") from None
