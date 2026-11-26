"""Owned, bounded operation subprocesses; native credentials never enter them."""

import codecs
import errno
import os
import secrets
import selectors
import signal
import subprocess
import threading
import time

from harness.command_safety import safe_environment
from harness.execution_isolation import (
    operation_popen,
    process_temporary_environment,
    release_process_temporary,
)
from harness.execution_logs import MAX_TEXT, StreamRedactor

_CENSUS_ARGV = ("/bin/ps", "-axo", "pid=,pgid=,stat=")


def _capture_census():
    inspection = subprocess.Popen(
        _CENSUS_ARGV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=safe_environment(),
    )
    deadline, output = time.monotonic() + 1, bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(inspection.stdout, selectors.EVENT_READ)
            selector.register(inspection.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired("process census", 1)
                for key, _ in selector.select(remaining):
                    raw = os.read(key.fd, min(65536, 1048577 - len(output)))
                    if not raw:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is inspection.stderr or len(output) + len(raw) > 1048576:
                        return None
                    else:
                        output.extend(raw)
        if inspection.wait(timeout=max(0, deadline - time.monotonic())) != 0:
            return None
        return bytes(output)
    finally:
        try:
            if inspection.poll() is None:
                try:
                    inspection.kill()
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Inspection still refuses; only actual completion settles its child.
                    inspection.wait(timeout=1)
            inspection.wait(timeout=1)
        finally:
            inspection.stdout.close()
            inspection.stderr.close()


def _group_has_no_live_members(group):
    output = _capture_census()
    if not output or len(output) > 1048576 or not output.endswith(b"\n"):
        return False
    rows = output.split(b"\n")[:-1]
    if len(rows) > 10000:
        return False
    seen, live = set(), False
    for row in rows:
        fields = row.split()
        if len(fields) != 3 or any(len(value) > 10 or not value.isdigit() for value in fields[:2]):
            return False
        pid, member_group = map(int, fields[:2])
        status = fields[2]
        if (
            pid in seen
            or max(pid, member_group) > 2147483647
            or status[0] not in b"DRSTtUWIXxZ"
            or any(flag not in b"<NLSsl+AEVXW" for flag in status[1:])
        ):
            return False
        seen.add(pid)
        live |= member_group == group and not status.startswith(b"Z")
    return not live


def _passively_settled(proc):
    if (
        not isinstance(proc, subprocess.Popen)
        or not getattr(proc, "_child_created", False)
        or type(proc.pid) is not int
        or proc.pid <= 1
    ):
        return False
    try:
        completed = proc.wait(timeout=1)
        return (
            type(completed) is int
            and proc.returncode == completed
            and _group_has_no_live_members(proc.pid)
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


class ProcessManager:
    def __init__(self):
        self.records = {}
        self.lock = threading.RLock()
        self.launching = 0

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
        completion=None,
        report=None,
        lifecycle=None,
        correlation=None,
    ):
        if type(timeout) is not int or not 1 <= timeout <= max_timeout:
            raise ValueError("operation timeout exceeds its capability limit")
        from harness.template_classification import validate_reference

        validate_reference(getattr(profile, "classification", None), root=profile.repository)
        with self.lock:
            if (
                len(self.records) + self.launching >= 128
                or sum(r["status"] == "Running" for r in self.records.values()) + self.launching
                >= 4
            ):
                raise ValueError("operation process capacity reached")
            self.launching += 1
        observer = lifecycle
        from harness.policy.context import process_hooks

        policy = process_hooks()
        handle = "process-" + secrets.token_hex(24)
        policy_deadline = None
        try:
            from harness.reliability.process_store import pending_process

            if observer:
                observer.begin()
            else:
                observer = pending_process(owner, task, revision, context=correlation)
            environment = environment or safe_environment() | {
                "TMPDIR": profile.temporary,
                "UV_CACHE_DIR": profile.temporary + "/uv-cache",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            # Short private TMPDIR when the profile has one (AF_UNIX socket paths).
            environment = process_temporary_environment(profile, dict(environment))
            started = time.monotonic()
            if policy:
                policy_deadline = policy.prepare(handle, observer)
            try:
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
            except BaseException as error:
                if not policy or not policy.launched:
                    release_process_temporary(getattr(profile, "process_temporary", ""))
                if policy:
                    policy.finish(closed=False, outcome="failed")
                if observer:
                    observer.notify(
                        "process.launch_failed",
                        duration_ms=(time.monotonic() - started) * 1000,
                        stop_reason="io_error" if isinstance(error, OSError) else "internal_error",
                    )
                raise
        except BaseException:
            with self.lock:
                self.launching -= 1
            raise
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
            "classification": getattr(profile, "classification", None),
            "classification_root": profile.repository,
            "completion": completion,
            "report": report,
            "result": None,
            "exit_observed": False,
            "process_temporary": getattr(profile, "process_temporary", ""),
            "observer": observer,
            "monotonic_start": started,
            "absolute_deadline": min(started + timeout, policy_deadline)
            if policy_deadline
            else started + timeout,
            "policy": policy,
            "stop_reason": None,
            "cleanup_status": "unobserved",
        }
        with self.lock:
            self.launching -= 1
            self.records[handle] = record
        try:
            if policy:
                policy.spawned(proc)
            self._notify(record, "process.started")
        except BaseException:
            # Ownership is already retained even when durable launch publication failed.
            self._kill(proc)
            try:
                proc.wait(timeout=5)
            finally:
                if policy:
                    policy.finish(closed=False, outcome="failed")
                record["status"] = "Failed"
                record["error"] = "process launch publication unavailable"
            raise
        thread = threading.Thread(target=self._drain, args=(record, timeout), daemon=True)
        record["thread"] = thread
        thread.start()
        result = {"handle": handle, "status": "Running"}
        if observer and not observer.available:
            result["telemetry_status"] = "unavailable"
        return result

    def _notify(self, record, kind, **snapshot):
        observer = record.get("observer")
        if observer:
            observer.notify(kind, **snapshot)

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as error:
            if error.errno != errno.EPERM or not _passively_settled(proc):
                raise

    def _drain(self, record, timeout):
        proc, size = record["process"], 0
        deadline = record["absolute_deadline"]
        redactors = {stream: StreamRedactor() for stream in (proc.stdout, proc.stderr)}
        decoders = {
            stream: codecs.getincrementaldecoder("utf-8")("replace") for stream in redactors
        }
        try:
            with selectors.DefaultSelector() as selector:
                for stream in redactors:
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map() or proc.poll() is None:
                    if record["cancel"].is_set():
                        record["stop_reason"] = "cancel_requested"
                        raise ValueError("operation cancelled or deadline exceeded")
                    if time.monotonic() > deadline:
                        record["stop_reason"] = "deadline_exceeded"
                        self._notify(record, "process.timeout_observed")
                        raise ValueError("operation cancelled or deadline exceeded")
                    for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fd, 16384)
                        size += len(chunk)
                        if record.get("report"):
                            record["report"].consume(
                                "stdout" if key.fileobj is proc.stdout else "stderr", chunk
                            )
                            text = ""
                        elif size > 1048576:
                            record["stop_reason"] = "output_limit"
                            raise ValueError("operation output limit exceeded")
                        else:
                            text = redactors[key.fileobj].feed(
                                decoders[key.fileobj].decode(chunk, final=not chunk),
                                final=not chunk,
                            )
                        with self.lock:
                            record["output"] += text
                        if not chunk:
                            selector.unregister(key.fileobj)
            proc.wait(timeout=1)
        except Exception as error:
            if record["stop_reason"] is None:
                record["stop_reason"] = (
                    "io_error" if isinstance(error, OSError) else "internal_error"
                )
            with self.lock:
                record["error"] = str(error)
        finally:
            # Kill remaining descendants in the owned process group even if the parent exited.
            try:
                self._kill(proc)
                proc.wait(timeout=5)
                release_process_temporary(record.get("process_temporary"))
                record["cleanup_status"] = "observed"
            except (OSError, subprocess.SubprocessError) as error:
                record["cleanup_status"] = "unconfirmed"
                record["error"] = "operation cleanup failed: " + str(error)
            finally:
                proc.stdout.close()
                proc.stderr.close()
            elapsed = (time.monotonic() - record["monotonic_start"]) * 1000
            if record["cancel"].is_set():
                record["stop_reason"] = record["stop_reason"] or "cancel_requested"
                record["error"] = "operation cancelled or deadline exceeded"
            try:
                self._publish(record, proc)
            except Exception as error:
                with self.lock:
                    record["output"] = ""
                    record["cursor"] = 0
                    record["error"] = (
                        str(error)
                        if record.get("report")
                        else ("template classification changed during operation; result invalid")
                    )
            finally:
                if record.get("report"):
                    record["report"].close()
            with self.lock:
                record["returncode"] = proc.returncode
                record["status"] = "Failed" if record["error"] else "Exited"

            if record.get("policy"):
                record["policy"].finish(
                    closed=record["cleanup_status"] == "observed",
                    outcome="deadline"
                    if record["stop_reason"] == "deadline_exceeded"
                    else "failed"
                    if record["error"] or proc.returncode
                    else "returned",
                )
            snapshot = {
                "returncode": proc.returncode,
                "duration_ms": elapsed,
                "stop_reason": record["stop_reason"],
                "cleanup_status": record["cleanup_status"],
            }
            if proc.returncode is not None:
                self._notify(record, "process.exited", **snapshot)
            if record["cleanup_status"] == "unconfirmed":
                self._notify(record, "process.cleanup_unconfirmed", **snapshot)

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
            # limit is an upper bound; drain `more` until the public-sized pages are exhausted.
            withheld = record.get("classification") and record["status"] == "Running"
            from harness.execution_artifacts import presentation_page

            consumed, output = (
                (0, "")
                if withheld
                else presentation_page(record["output"][cursor : cursor + min(limit, MAX_TEXT)])
            )
            record["cursor"] += consumed
            more = False if withheld else record["cursor"] < len(record["output"])
            if record["status"] != "Running" and not more:
                record["exit_observed"] = True
            return {
                "handle": handle,
                "status": record["status"],
                "output": output,
                "more": more,
                "returncode": record["returncode"],
                "error": record["error"],
                **({"result": record["result"]} if record.get("report") else {}),
                **(
                    {"telemetry_status": "unavailable"}
                    if record.get("observer") and not record["observer"].available
                    else {}
                ),
            }

    def _publish(self, record, process):
        """Keep the original live source claim through completion-time source reads."""
        from harness.policy.context import check_deadline, scope
        from harness.policy.contracts import require
        from harness.template_classification import validate_reference

        hooks = record.get("policy")
        owner = hooks.attempt if hooks is not None else None
        with scope(owner):
            if owner is not None:
                require(hooks.process is process, "AUTHORITY_STALE")
                with owner.authority() as (conn, execution):
                    approvals, _ = owner.current(conn, execution)
                    from harness.policy import capabilities, claims

                    capabilities.validate(conn, owner.intent, owner.digest, approvals)
                    row = conn.execute(
                        "SELECT a.state,p.launch_state,p.pid FROM policy_attempts a "
                        "JOIN policy_processes p ON p.attempt_id=a.id "
                        "WHERE a.id=? AND p.id=? AND p.instance_ref=?",
                        (owner.intent.id, hooks.id, hooks.instance_ref),
                    ).fetchone()
                    require(
                        row is not None
                        and row["state"] == "started"
                        and row["launch_state"] == "launched"
                        and row["pid"] == process.pid,
                        "ATTEMPT_NOT_EXECUTABLE",
                    )
                    if not owner.legacy:
                        claims.revalidate(conn, owner.intent.id, owner.intent.claims)
                        require(
                            not conn.execute(
                                "SELECT 1 FROM policy_resource_claims "
                                "WHERE attempt_id=? AND state!='active' LIMIT 1",
                                (owner.intent.id,),
                            ).fetchone(),
                            "ATTEMPT_NOT_EXECUTABLE",
                        )
                check_deadline()
            if record.get("completion"):
                record["completion"]()
            validate_reference(record.get("classification"), root=record.get("classification_root"))
            if record.get("report") and not record["error"]:
                record["result"] = record["report"].publish(process.returncode)

    def pending(self):
        """Observation only: include terminal results until their owner collects them."""
        with self.lock:
            return [
                {
                    "handle": handle,
                    "owner": record["owner"],
                    "status": record["status"],
                }
                for handle, record in self.records.items()
                if record["status"] == "Running" or not record.get("exit_observed", False)
            ]

    def cancel(self, owner, handle):
        with self.lock:
            record = self._owned(owner, handle)
            first = not record["cancel"].is_set()
            record["cancel"].set()
            running = record["status"] == "Running"
        if first:
            self._notify(record, "process.cancel_requested")
        if running:
            self._kill(record["process"])
        return {"handle": handle, "status": "CancellationRequested"}

    def revalidate(self, validator):
        with self.lock:
            records = list(self.records.items())
        for handle, record in records:
            if record["status"] != "Running":
                continue
            try:
                validator(record)
            except (OSError, ValueError, LookupError):
                self.cancel(record["owner"], handle)

    def close(self):
        with self.lock:
            records = list(self.records.items())
        for handle, record in records:
            if record["status"] == "Running":
                self.cancel(record["owner"], handle)
        for _, record in records:
            record["process"].wait(timeout=5)
            if record.get("thread"):
                record["thread"].join(timeout=20)
                if record["thread"].is_alive():
                    raise ValueError("operation completion is unsettled; reconciliation required")


def bounded_capture(
    proc,
    *,
    timeout=60,
    max_bytes=16 * 1024 * 1024,
    report=None,
    lifecycle=None,
    monotonic_start=None,
    absolute_deadline=None,
    policy=None,
):
    """Limit capture while reading, including children keeping pipes open after parent exit."""
    started = time.monotonic() if monotonic_start is None else monotonic_start
    deadline, size = (
        min(started + timeout, absolute_deadline) if absolute_deadline else started + timeout,
        0,
    )
    stop_reason = None
    outputs = {"stdout": [], "stderr": []}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map() or proc.poll() is None:
                if time.monotonic() >= deadline:
                    stop_reason = "deadline_exceeded"
                    if lifecycle:
                        lifecycle.notify("process.timeout_observed")
                    raise ValueError("AL command deadline exceeded; reconcile its recorded state")
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    raw = os.read(key.fd, 65536)
                    size += len(raw)
                    if report:
                        report.consume(key.data, raw)
                    elif size > max_bytes:
                        stop_reason = "output_limit"
                        raise ValueError("AL output limit exceeded; reconcile its recorded state")
                    if raw and not report:
                        outputs[key.data].append(raw)
                    if not raw:
                        selector.unregister(key.fileobj)
        proc.wait(timeout=1)
        return b"".join(outputs["stdout"]), b"".join(outputs["stderr"])
    except BaseException as error:
        stop_reason = stop_reason or (
            "io_error" if isinstance(error, OSError) else "internal_error"
        )
        raise
    finally:
        cleanup = "unconfirmed"
        try:
            ProcessManager._kill(proc)
            proc.wait(timeout=5)
            cleanup = "observed"
        finally:
            proc.stdout.close()
            proc.stderr.close()
            if policy:
                policy.finish(
                    closed=cleanup == "observed",
                    outcome="deadline"
                    if stop_reason == "deadline_exceeded"
                    else "failed"
                    if stop_reason or proc.returncode
                    else "returned",
                )
            if lifecycle:
                snapshot = {
                    "returncode": proc.returncode,
                    "duration_ms": (time.monotonic() - started) * 1000,
                    "stop_reason": stop_reason,
                    "cleanup_status": cleanup,
                }
                if proc.returncode is not None:
                    lifecycle.notify("process.exited", **snapshot)
                if cleanup == "unconfirmed":
                    lifecycle.notify("process.cleanup_unconfirmed", **snapshot)
