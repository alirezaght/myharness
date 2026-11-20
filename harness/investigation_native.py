"""Owned bounded App Server process. No model turns or arbitrary RPC forwarding."""

import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from harness.authoring_contract import digest
from harness.investigation_policy import (
    HANDOFFS as HANDOFFS,
)
from harness.investigation_policy import (
    REMOTE_CONTROL_ENV,
    TOOLS,
    AcquisitionError,
    decode_message,
    launch_argv,
    validate_capability,
    validate_effective_config,
    validate_request,
)
from harness.investigation_repository import source_record
from harness.native_runtime import native_auth_environment


class _ConsentCompleted(Exception):
    def __init__(self, result):
        self.result = result


class NativeInvestigator:
    def __init__(self, qualified=None, *, timeout=45, rpc_timeout=10):
        self.qualified = qualified
        self.timeout, self.rpc_timeout = min(timeout, 60), min(rpc_timeout, 15)
        self.process = None
        self.settlement = "settled"
        self.diagnostics = []

    def reconnect(self, request, *, cancelled=lambda: False, publish_handoff=None):
        if not self.qualified or not self.qualified.reconnect_qualified:
            raise AcquisitionError("reconnect_unavailable")
        return self._run(
            request, cancelled=cancelled, reconnect=True, publish_handoff=publish_handoff
        )

    def acquire(self, request, *, cancelled=lambda: False, progress=lambda stage: None):
        return self._run(request, cancelled=cancelled, progress=progress)

    def _run(
        self,
        request,
        *,
        cancelled,
        progress=lambda stage: None,
        reconnect=False,
        publish_handoff=None,
    ):
        qualified = validate_capability(
            self.qualified, purpose="reconnect" if reconnect else "read"
        )
        validate_request(request, qualified, reconnect=reconnect)
        self.cancelled = cancelled
        self.deadline = time.monotonic() + self.timeout
        self.buffer, self.total, self.stderr_bytes, self.serial = b"", 0, 0, 0
        self.thread, self.notifications = None, 0
        self.remote_disabled = False
        self.login_id, self.login_completed = None, False
        self.recovering = reconnect
        self.login_pending = []
        self.starting_thread, self.pending_thread = False, []
        failure, result = None, None
        try:
            with tempfile.TemporaryDirectory(prefix="harness-investigation-") as directory:
                argv = launch_argv(qualified)
                self.process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=directory,
                    env=dict(native_auth_environment(), **{REMOTE_CONTROL_ENV: "1"}),
                    start_new_session=True,
                )
                self.settlement = "unknown"
                self.selector = selectors.DefaultSelector()
                for stream in (self.process.stdout, self.process.stderr):
                    os.set_blocking(stream.fileno(), False)
                    self.selector.register(stream, selectors.EVENT_READ)
                self._rpc(
                    "initialize",
                    {
                        "clientInfo": {"name": "harness_investigation", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                self._send({"method": "initialized"})
                self._config()
                self._require_remote_disabled()
                started = self._rpc(
                    "thread/start",
                    {
                        "ephemeral": True,
                        "cwd": directory,
                        "approvalPolicy": "never",
                        "sandbox": "read-only",
                    },
                )
                self.thread = started["thread"]["id"]
                if not isinstance(self.thread, str) or not self.thread:
                    raise AcquisitionError("protocol_error")
                self.starting_thread = False
                for message in self.pending_thread:
                    self._notification(message)
                if reconnect:
                    self._login(publish_handoff)
                    if (
                        not qualified.read_qualified
                        or request["site"] is None
                        or not request["site"].get("verified")
                    ):
                        metadata = self._rpc(
                            "mcpServerStatus/list",
                            {
                                "serverName": "atlassian",
                                "threadId": self.thread,
                                "detail": "toolsAndAuthOnly",
                                "limit": 10,
                            },
                        )
                        rows = metadata.get("data", [])
                        if (
                            len(rows) != 1
                            or rows[0].get("name") != "atlassian"
                            or rows[0].get("runtimeStatus") != "connected"
                            or rows[0].get("toolsError")
                        ):
                            raise AcquisitionError("issue_unavailable")
                        raise _ConsentCompleted(
                            {
                                "reconnected": True,
                                "read_qualified": False,
                                "settlement": "settled",
                                "tool_schema": digest(rows[0].get("tools", {})),
                                "observed_tools": rows[0].get("tools", {}),
                            }
                        )
                self._status()
                progress("acquiring_ticket")
                resources = self._tool(TOOLS[0], {})
                if not isinstance(resources, list) or len(resources) > 50:
                    raise AcquisitionError("protocol_error")
                site = request["site"]
                resource = next(
                    (
                        r
                        for r in resources
                        if r.get("id") == site["id"] and r.get("url") == site["url"]
                    ),
                    None,
                )
                if not resource or "read:jira:agent-interface" not in resource.get("scopes", []):
                    raise AcquisitionError("scope_denied")
                names = qualified.arguments
                value = self._tool(
                    TOOLS[1],
                    {
                        names["site"]: site["id"],
                        names["issue"]: request["issue"],
                        names["fields"]: request["fields"],
                    },
                )
                if (
                    not isinstance(value, dict)
                    or value.get("key") != request["issue"]
                    or value.get("cloudId", site["id"]) != site["id"]
                    or not isinstance(value.get("fields"), dict)
                ):
                    raise AcquisitionError("scope_denied")
                fields = {k: value["fields"][k] for k in request["fields"] if k in value["fields"]}
                if not fields.get("summary") or "description" not in fields:
                    raise AcquisitionError("issue_unavailable")
                content = json.dumps(
                    {"key": request["issue"], "fields": fields}, ensure_ascii=False, sort_keys=True
                )
                source = source_record(
                    "ticket",
                    site["url"] + "/browse/" + request["issue"],
                    content,
                    {
                        "issue": request["issue"],
                        "site": site["id"],
                        "updated": fields.get("updated"),
                    },
                )
                result = {
                    "sources": [source],
                    "issue": request["issue"],
                    "site": site,
                    "receipt": {
                        "policy": qualified.identity,
                        "executable": qualified.executable["sha256"],
                        "tool_schema": digest(qualified.tools),
                        "model_turns": 0,
                    },
                    "settlement": "settled",
                }
        except _ConsentCompleted as completed:
            result = completed.result
        except AcquisitionError as error:
            failure = error
        except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
            failure = AcquisitionError("protocol_error")
        finally:
            self._close()
        if self.settlement != "settled":
            raise AcquisitionError("cleanup_unknown", settlement="unknown")
        if failure:
            raise failure
        if self.process.returncode != 0 or self.buffer.strip():
            raise AcquisitionError("protocol_error")
        return result

    def _send(self, message):
        self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        self.process.stdin.flush()

    def _check(self):
        if self.cancelled():
            raise AcquisitionError("cancelled", settlement="unknown")
        if time.monotonic() >= self.deadline:
            raise AcquisitionError("timeout", settlement="unknown")

    def _notification(self, message):
        self.notifications += 1
        if self.notifications > 200:
            raise AcquisitionError("output_limit")
        if "id" in message:
            self._send(
                {
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Interactive requests are forbidden"},
                }
            )
            raise AcquisitionError("scope_denied")
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise AcquisitionError("protocol_error")
        if message.get("method") == "remoteControl/status/changed":
            required = {"installationId", "serverName", "status"}
            if (
                not required <= set(params) <= required | {"environmentId"}
                or any(not isinstance(params[key], str) for key in required)
                or (
                    params.get("environmentId") is not None
                    and not isinstance(params["environmentId"], str)
                )
                or params["status"] != "disabled"
            ):
                raise AcquisitionError("scope_denied")
            self.remote_disabled = True
            return
        if (
            self.thread is None
            and self.starting_thread
            and params.get("threadId")
            and message.get("method") == "mcpServer/startupStatus/updated"
        ):
            self.pending_thread.append(message)
            return
        if params.get("threadId") not in (None, self.thread):
            raise AcquisitionError("scope_denied")
        if message.get("method") == "mcpServer/oauthLogin/completed":
            if not self.login_id:
                self.login_pending.append(params)
                return
            if (params.get("name"), params.get("threadId"), params.get("loginId")) != (
                "atlassian",
                self.thread,
                self.login_id,
            ):
                raise AcquisitionError("scope_denied")
            if params.get("success") is not True:
                raise AcquisitionError("authentication_required")
            self.login_completed = True
        if message.get("method") == "mcpServer/startupStatus/updated":
            if params.get("name") != "atlassian":
                raise AcquisitionError("scope_denied")
            if params.get("status") == "failed":
                # Raw native diagnostic remains private and is never stored.
                raw = str(params.get("error", ""))[:8192].lower()
                auth = any(word in raw for word in ("auth", "credential", "401", "403", "login"))
                if auth and self.recovering and not self.login_completed:
                    return
                raise AcquisitionError("authentication_required" if auth else "issue_unavailable")
        if message.get("method", "").startswith(("item/", "turn/")):
            raise AcquisitionError("scope_denied")

    def _rpc(self, method, params):
        self._check()
        self.serial += 1
        ident = self.serial
        if method == "thread/start":
            self.starting_thread = True
        self._send({"id": ident, "method": method, "params": params})
        deadline = min(self.deadline, time.monotonic() + self.rpc_timeout)
        while True:
            self._check()
            if time.monotonic() >= deadline:
                raise AcquisitionError("timeout")
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line:
                    raise AcquisitionError("protocol_error")
                message = decode_message(line)
                if not isinstance(message, dict):
                    raise AcquisitionError("protocol_error")
                if "method" in message:
                    self._notification(message)
                    continue
                if message.get("id") != ident or "error" in message or "result" not in message:
                    raise AcquisitionError("protocol_error")
                # Drain already-buffered notifications before accepting a response.
                result = message["result"]
                while b"\n" in self.buffer:
                    other, self.buffer = self.buffer.split(b"\n", 1)
                    note = decode_message(other)
                    if "method" not in note:
                        raise AcquisitionError("protocol_error")
                    self._notification(note)
                return result
            self._receive(deadline)

    def _receive(self, deadline):
        events = self.selector.select(min(0.05, deadline - time.monotonic()))
        for key, _ in events:
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                self.selector.unregister(key.fileobj)
                if key.fileobj is self.process.stdout:
                    raise AcquisitionError("protocol_error")
                continue
            self.total += len(chunk)
            if self.total > 2097152:
                raise AcquisitionError("output_limit")
            if key.fileobj is self.process.stderr:
                self.stderr_bytes += len(chunk)
                if self.stderr_bytes > 65536:
                    raise AcquisitionError("output_limit")
            else:
                self.buffer += chunk
                if len(self.buffer.split(b"\n", 1)[0]) > 262144:
                    raise AcquisitionError("output_limit")
        if self.process.poll() is not None and not self.buffer:
            raise AcquisitionError("protocol_error")

    def _require_remote_disabled(self):
        deadline = min(self.deadline, time.monotonic() + self.rpc_timeout)
        while not self.remote_disabled:
            self._check()
            if time.monotonic() >= deadline:
                raise AcquisitionError("schema_changed")
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                note = decode_message(line)
                if not isinstance(note, dict) or "method" not in note:
                    raise AcquisitionError("protocol_error")
                self._notification(note)
            if not self.remote_disabled:
                self._receive(deadline)

    def _config(self):
        value = self._rpc("config/read", {"includeLayers": False})
        validate_effective_config(value.get("config"), self.qualified)

    def _status(self):
        cursor, seen = None, set()
        for _ in range(4):
            self._check()
            value = self._rpc(
                "mcpServerStatus/list",
                {
                    "serverName": "atlassian",
                    "threadId": self.thread,
                    "detail": "toolsAndAuthOnly",
                    "limit": 10,
                    "cursor": cursor,
                },
            )
            rows = value.get("data", [])
            if not isinstance(rows, list) or len(rows) > 10:
                raise AcquisitionError("protocol_error")
            for row in rows:
                if (
                    row.get("name") != "atlassian"
                    or row.get("error")
                    or row.get("discoveryError")
                    or row.get("toolsError")
                ):
                    raise AcquisitionError("scope_denied")
                if row.get("runtimeStatus") == "authenticationRequired":
                    raise AcquisitionError("authentication_required")
                if row.get("tools"):
                    if row.get("runtimeStatus") != "connected":
                        raise AcquisitionError("issue_unavailable")
                    if row.get("tools") != self.qualified.tools or row.get("authStatus") != "oAuth":
                        raise AcquisitionError("schema_changed")
                    return
            cursor = value.get("nextCursor")
            if cursor:
                if cursor in seen:
                    raise AcquisitionError("protocol_error")
                seen.add(cursor)
            # Empty inventory is pending startup, never success.
        raise AcquisitionError("issue_unavailable")

    def _tool(self, tool, arguments):
        self._config()
        self._status()
        result = self._rpc(
            "mcpServer/tool/call",
            {"server": "atlassian", "threadId": self.thread, "tool": tool, "arguments": arguments},
        )
        if result.get("isError"):
            raise AcquisitionError("issue_unavailable")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        content = result.get("content")
        if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
            raise AcquisitionError("protocol_error")
        return decode_message(content[0]["text"])

    def _login(self, publish_handoff):
        if not callable(publish_handoff):
            raise AcquisitionError("reconnect_unavailable")
        result = self._rpc(
            "mcpServer/oauth/login",
            {
                "name": "atlassian",
                "threadId": self.thread,
                "timeoutSecs": min(60, max(1, int(self.deadline - time.monotonic()))),
                "scopes": ["read:jira:agent-interface"],
            },
        )
        self.login_id = result.get("loginId")
        url = result.get("authorizationUrl")
        if (
            not isinstance(self.login_id, str)
            or not self.login_id
            or not isinstance(url, str)
            or len(url) > 8192
        ):
            raise AcquisitionError("protocol_error")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"auth.atlassian.com", "mcp.atlassian.com"}
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.fragment
        ):
            raise AcquisitionError("scope_denied")
        publish_handoff({"authorization_url": url, "login_id": self.login_id})
        for params in self.login_pending:
            self._notification({"method": "mcpServer/oauthLogin/completed", "params": params})
        while not self.login_completed:
            self._check()
            # A harmless owned-thread status poll also drains correlated completion notifications.
            self._rpc(
                "mcpServerStatus/list",
                {
                    "serverName": "atlassian",
                    "threadId": self.thread,
                    "detail": "toolsAndAuthOnly",
                    "limit": 10,
                },
            )
            time.sleep(min(0.05, max(0, self.deadline - time.monotonic())))
        self._config()

    def _close(self):
        process = self.process
        if not process:
            return
        try:
            process.stdin.close()
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=1)
            self.settlement = "settled"
        except (OSError, subprocess.SubprocessError):
            self.settlement = "unknown"
        finally:
            if hasattr(self, "selector"):
                self.selector.close()
            for stream in (process.stdout, process.stderr):
                stream.close()
