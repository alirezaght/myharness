"""Owned, bounded operation subprocesses; native credentials never enter them."""

import os
import secrets
import selectors
import signal
import subprocess
import threading
import time

from harness.command_safety import safe_environment
from harness.execution_isolation import operation_popen
from harness.execution_logs import StreamRedactor


class ProcessManager:
    def __init__(self):
        self.records = {}
        self.lock = threading.RLock()

    def start(
        self,
        owner,
        profile,
        argv,
        cwd,
        *,
        task,
        revision,
        timeout=30,
        environment=None,
        max_timeout=300,
        pass_fds=(),
    ):
        if type(timeout) is not int or not 1 <= timeout <= max_timeout:
            raise ValueError("operation timeout exceeds its capability limit")
        with self.lock:
            if (
                len(self.records) >= 128
                or sum(r["status"] == "Running" for r in self.records.values()) >= 4
            ):
                raise ValueError("operation process capacity reached")
            environment = environment or safe_environment() | {
                "TMPDIR": profile.temporary,
                "UV_CACHE_DIR": profile.temporary + "/uv-cache",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            proc = operation_popen(
                profile,
                argv,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
                pass_fds=pass_fds,
            )
            handle = "process-" + secrets.token_hex(24)
            record = {
                "owner": owner,
                "task": task,
                "revision": revision,
                "process": proc,
                "status": "Running",
                "output": "",
                "cursor": 0,
                "returncode": None,
                "error": None,
                "cancel": threading.Event(),
            }
            self.records[handle] = record
            threading.Thread(target=self._drain, args=(record, timeout), daemon=True).start()
            return {"handle": handle, "status": "Running"}

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _drain(self, record, timeout):
        proc, size = record["process"], 0
        deadline = time.monotonic() + timeout
        redactors = {stream: StreamRedactor() for stream in (proc.stdout, proc.stderr)}
        try:
            with selectors.DefaultSelector() as selector:
                for stream in redactors:
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map() or proc.poll() is None:
                    if record["cancel"].is_set() or time.monotonic() > deadline:
                        raise ValueError("operation cancelled or deadline exceeded")
                    for key, _ in selector.select(0.1):
                        chunk = os.read(key.fd, 16384)
                        size += len(chunk)
                        if size > 1048576:
                            raise ValueError("operation output limit exceeded")
                        text = redactors[key.fileobj].feed(
                            chunk.decode(errors="replace"), final=not chunk
                        )
                        with self.lock:
                            record["output"] += text
                        if not chunk:
                            selector.unregister(key.fileobj)
            proc.wait(timeout=1)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            with self.lock:
                record["error"] = str(error)
        finally:
            # Kill remaining descendants in the owned process group even if the parent exited.
            self._kill(proc)
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
            with self.lock:
                record["returncode"] = proc.returncode
                record["status"] = "Failed" if record["error"] else "Exited"

    def _owned(self, owner, handle):
        record = self.records.get(handle)
        if record is None or record["owner"] != owner:
            raise ValueError("process handle is not owned by this native principal")
        return record

    def poll(self, owner, handle, *, limit=16384):
        if type(limit) is not int or not 1 <= limit <= 65536:
            raise ValueError("bounded process output cursor required")
        with self.lock:
            record = self._owned(owner, handle)
            cursor = record["cursor"]
            output = record["output"][cursor : cursor + limit]
            record["cursor"] += len(output)
            return {
                "handle": handle,
                "status": record["status"],
                "output": output,
                "more": record["cursor"] < len(record["output"]),
                "returncode": record["returncode"],
                "error": record["error"],
            }

    def cancel(self, owner, handle):
        with self.lock:
            record = self._owned(owner, handle)
            record["cancel"].set()
            if record["status"] == "Running":
                self._kill(record["process"])
        return {"handle": handle, "status": "CancellationRequested"}

    def revalidate(self, validator):
        with self.lock:
            for record in self.records.values():
                if record["status"] != "Running":
                    continue
                try:
                    validator(record)
                except (OSError, ValueError, LookupError):
                    record["cancel"].set()
                    self._kill(record["process"])

    def close(self):
        with self.lock:
            records = list(self.records.values())
            for record in records:
                if record["status"] == "Running":
                    record["cancel"].set()
                    self._kill(record["process"])
        for record in records:
            record["process"].wait(timeout=5)


def bounded_capture(proc, *, timeout=60, max_bytes=16 * 1024 * 1024):
    """Limit capture while reading, including children keeping pipes open after parent exit."""
    deadline, size = time.monotonic() + timeout, 0
    outputs = {"stdout": [], "stderr": []}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map() or proc.poll() is None:
                if time.monotonic() >= deadline:
                    raise ValueError("AL command deadline exceeded; reconcile its recorded state")
                for key, _ in selector.select(0.1):
                    raw = os.read(key.fd, 65536)
                    size += len(raw)
                    if size > max_bytes:
                        raise ValueError("AL output limit exceeded; reconcile its recorded state")
                    if raw:
                        outputs[key.data].append(raw)
                    else:
                        selector.unregister(key.fileobj)
        proc.wait(timeout=1)
        return b"".join(outputs["stdout"]), b"".join(outputs["stderr"])
    finally:
        ProcessManager._kill(proc)
        proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
