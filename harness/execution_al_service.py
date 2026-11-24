"""The resident AL service (#27) as seen from the Harness supervisor (#31).

Harness never runs AL commands in the service: a contained command keeps its own sandbox
and the service refuses it. The supervisor uses the service for two things only:

* locating it, so the operation sandbox can admit exactly its socket (AL then reads the
  service's proven-current snapshot tree instead of capturing the worktree itself);
* subscribing to changes of the AL run's state file, so step 8 replays the run's outbox as
  soon as AL commits instead of on the next polling tick. Without a service, or when the
  subscription ends, step 8 polls exactly as before: both paths read the same file through
  the same replay, so they show the same states and results.

The wire format is the service's own (`al/installer/runtime_service_client.py`): a 4-byte
big-endian length, then canonical JSON; requests carry the service's token."""

import hashlib
import json
import os
import socket
import stat
import struct
import subprocess
import threading
import time
from pathlib import Path

PROTOCOL = 1
LOOKUP_SECONDS = 30.0


def route(repository, platform, session):
    value = [os.path.realpath(repository), platform, "session", session]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _private(path, kind):
    info = os.lstat(path)
    expected = (stat.S_ISDIR, 0o700) if kind == "dir" else (stat.S_ISREG, 0o600)
    return (
        expected[0](info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == expected[1]
    )


def services_root(launcher, repository, *, runner=subprocess.run):
    """The per-user service directory, from `alctl service status` (None when no service runs)."""
    try:
        result = runner(
            [str(launcher), "service", "status", "--repository", str(repository)],
            capture_output=True,
            timeout=10,
            cwd=str(repository),
        )
        if result.returncode:
            return None
        rows = json.loads(result.stdout).get("services") or []
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    for row in rows:
        directory = row.get("directory") if isinstance(row, dict) else None
        if isinstance(directory, str) and os.path.isabs(directory):
            return Path(directory).parent
    return None


def locate(root, repository, platform, session):
    """The service directory routed to this run under `root`, or None."""
    if root is None:
        return None
    try:
        if not _private(root, "dir"):
            return None
        path = Path(root) / "routes" / route(repository, platform, session)
        if not _private(path, "file"):
            return None
        name = path.read_bytes()[:128].decode().strip()
        if not name.isalnum():
            return None
        directory = Path(root) / name
        if not _private(directory, "dir") or not _private(directory / "token", "file"):
            return None
        if not stat.S_ISSOCK(os.lstat(directory / "s").st_mode):
            return None
        return directory
    except (OSError, UnicodeError):
        return None


class Locator:
    """Cached lookup of one run's service; a miss is retried after LOOKUP_SECONDS."""

    def __init__(self, launcher, repository, platform, session, *, runner=subprocess.run):
        self.args = (launcher, repository, platform, session)
        self.runner = runner
        self.checked, self.value = None, None

    def __call__(self):
        launcher, repository, platform, session = self.args
        if self.value is not None and locate(self.value.parent, repository, platform, session):
            return self.value
        now = time.monotonic()
        if self.checked is not None and now - self.checked < LOOKUP_SECONDS:
            return None
        self.checked = now
        root = services_root(launcher, repository, runner=self.runner)
        self.value = locate(root, repository, platform, session)
        return self.value


def _frame(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return struct.pack(">I", len(data)) + data


def _exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError("AL service connection closed")
        data += chunk
    return data


def receive(sock, limit=65536):
    size = struct.unpack(">I", _exact(sock, 4))[0]
    if size > limit:
        raise ValueError("AL service message too large")
    return json.loads(_exact(sock, size))


def connect(directory):
    directory = Path(directory)
    token = (directory / "token").read_bytes()[:256].decode().strip()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    here = os.open(".", os.O_RDONLY)
    try:
        os.chdir(directory)  # a relative name keeps long paths under the socket limit
        sock.connect("s")
    except OSError:
        sock.close()
        raise
    finally:
        os.fchdir(here)
        os.close(here)
    return sock, token


class Subscription:
    """Background reader of one service subscription; `on_change(path)` per pushed change.

    `active` is True from a confirmed subscription until the connection ends; afterwards the
    caller polls (the subscription is not reopened by itself: `Subscription.open` again)."""

    def __init__(self, sock, paths, on_change):
        self.sock, self.paths, self.on_change = sock, paths, on_change
        self.active = True
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    @classmethod
    def open(cls, directory, paths, on_change, timeout=5):
        if directory is None:
            return None
        try:
            sock, token = connect(directory)
            sock.settimeout(timeout)
            sock.sendall(
                _frame(
                    {
                        "protocol": PROTOCOL,
                        "op": "subscribe",
                        "token": token,
                        "paths": [str(p) for p in paths],
                    }
                )
            )
            answer = receive(sock)
        except (OSError, ValueError, EOFError, UnicodeError):
            return None
        if answer.get("event") != "subscribed":
            sock.close()
            return None
        sock.settimeout(None)
        return cls(sock, [str(p) for p in paths], on_change)

    def _read(self):
        try:
            while True:
                message = receive(self.sock)
                if message.get("event") == "changed" and message.get("path") in self.paths:
                    self.on_change(message["path"])
        except (OSError, ValueError, EOFError):
            pass
        finally:
            self.active = False
            try:
                self.sock.close()
            except OSError:
                pass

    def close(self):
        self.active = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
