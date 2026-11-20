"""The dashboard's HTTP server: 127.0.0.1 only, an OS-chosen free port, a fixed set of routes.

Every request is checked against the expected Host header (DNS rebinding guard). GET is
answered from the read model, its live stream, one stored diagram version or the packaged
static files; a GET writes only diagram versions (by the Diagram Service, when the design of
the selected task changed). `?task=<id>` selects the task; without it the task the dashboard
was launched for. `/api/diagrams?version=<n>` returns version n of the task's diagrams and
`/api/diagrams/compare?from=<n>&to=<m>` versions n < m side by side with their changes (P2-06).
POST is accepted only at the declared comment, approval, execution, planning-recovery,
specification-restoration and knowledge action routes, and only from the dashboard's own origin
with a JSON body of a declared length; every header is checked before the body is read. Knowledge
query and evidence reads remain task-scoped. Any other POST path, and every other method, is 501.
"""

import json
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from harness.authoring_actions_contract import SCHEMAS
from harness.dashboard.approve import approve_task
from harness.dashboard.comments_view import add_comment, move_comment
from harness.dashboard.compare_view import compare_view
from harness.dashboard.diagrams_view import diagram_version_view
from harness.dashboard.view import NO_ACTIVE_TASK

LOCALHOST = "127.0.0.1"
API_PATH = "/api/dashboard"
STREAM_PATH = "/api/stream"
DIAGRAMS_PATH = "/api/diagrams"
COMMENTS_PATH = "/api/comments"
RESOLVE_PATH = "/api/comments/resolve"
REOPEN_PATH = "/api/comments/reopen"
COMPARE_PATH = "/api/diagrams/compare"
APPROVE_PATH = "/api/approve"
WRITES = {
    "/api/workspace/proposals": "workspace-proposal",
    COMMENTS_PATH: None,
    RESOLVE_PATH: "resolve",
    REOPEN_PATH: "reopen",
    APPROVE_PATH: "approve",
    "/api/session/action": "session-action",
    "/api/execution/start": "start",
    "/api/execution/retry-start": "retry-start",
    "/api/execution/reconnect": "reconnect",
    "/api/execution/resume": "resume",
    "/api/execution/recover": "recover",
    "/api/execution/retry-checks": "retry-checks",
    "/api/execution/reconcile": "reconcile",
    "/api/execution/decision": "decision",
    "/api/execution/decision-retry": "decision-retry",
    "/api/knowledge/refresh": "knowledge-refresh",
    "/api/knowledge/query": "knowledge-query",
    "/api/knowledge/explain": "knowledge-explain",
    "/api/planning-recovery/start": "planning-start",
    "/api/planning-recovery/inspect": "planning-inspect",
    "/api/planning-recovery/finish": "planning-finish",
    "/api/spec-restoration/restore": "spec-restoration",
}

WRITES.update({"/api/authoring/" + action: "authoring" for action in {*SCHEMAS, "new"}})
LENGTH = re.compile(r"[0-9]{1,18}")
READ_CHUNK = 1 << 16
VERSION = re.compile(r"[1-9][0-9]*")
MAX_VERSION = 2**63 - 1
STREAM_TYPE = "text/event-stream; charset=utf-8"
RETRY_MS = 2000
KEEPALIVE_SECONDS = 15.0
STATIC_DIR = files("harness.dashboard") / "static"
STATIC = {
    **{
        f"/static/{name}": (name, "text/javascript; charset=utf-8")
        for name in (
            "interview.js",
            "spec_editor.js",
            "workspace.js",
            "workspace_plan.js",
            "workspace_diagram.js",
        )
    },
    "/static/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
    "/static/interview.css": ("interview.css", "text/css; charset=utf-8"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/start.js": ("start.js", "text/javascript; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/diagrams.js": ("diagrams.js", "text/javascript; charset=utf-8"),
    "/static/comments.js": ("comments.js", "text/javascript; charset=utf-8"),
    "/static/comment_actions.js": ("comment_actions.js", "text/javascript; charset=utf-8"),
    "/static/compare.js": ("compare.js", "text/javascript; charset=utf-8"),
    "/static/approvals.js": ("approvals.js", "text/javascript; charset=utf-8"),
    "/static/execution.js": ("execution.js", "text/javascript; charset=utf-8"),
    "/static/knowledge.js": ("knowledge.js", "text/javascript; charset=utf-8"),
    "/static/execution_diagram.js": ("execution_diagram.js", "text/javascript; charset=utf-8"),
    "/static/execution_stage_details.js": (
        "execution_stage_details.js",
        "text/javascript; charset=utf-8",
    ),
    "/static/execution_diagram.css": ("execution_diagram.css", "text/css; charset=utf-8"),
    "/static/delivery.js": ("delivery.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
}
JSON_TYPE = "application/json; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
}
READ_ERRORS = (sqlite3.Error, OSError, ValueError, LookupError)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    poll_seconds = 1.0
    stream_lifetime = 300.0

    def __init__(
        self,
        db_path: Path,
        task_id: str | None,
        specs: str | None = None,
        *,
        project_root: Path | None = None,
    ):
        self.db_path = Path(db_path)
        self.task_id = task_id
        self.specs = specs
        from harness.authoring_worker import AuthoringWorker
        from harness.db import repo_root

        self.project_root = Path(project_root or repo_root()).resolve()
        self.authoring_worker = AuthoringWorker(self.db_path, self.project_root, specs=specs)
        self.closing = threading.Event()
        self.emission_lock = threading.Lock()
        super().__init__((LOCALHOST, 0), DashboardHandler)
        self.authoring_worker.start()

    def server_close(self) -> None:
        with self.emission_lock:
            self.closing.set()
        self.authoring_worker.close()
        super().server_close()

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{LOCALHOST}:{self.port}/"

    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({f"{LOCALHOST}:{self.port}", f"localhost:{self.port}"})


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "harness-dashboard"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def end_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, sort_keys=True).encode("utf-8"), JSON_TYPE)

    def _text(self, status: int, text: str) -> None:
        self._send(status, f"{text}\n".encode(), TEXT_TYPE)

    def _requested_task(self, query: str) -> str | None:
        requested = parse_qs(query).get("task", [""])[0]
        return requested or self.server.task_id

    def _view(self, task_id: str | None) -> dict[str, Any]:
        server = self.server
        from harness.dashboard.view import combined_view

        data = combined_view(server.db_path, task_id, server.specs, root=server.project_root)
        authoring = data["authoring"]
        from harness.authoring import project_identity

        data["project"] = {
            "id": project_identity(server.project_root),
            "repository": str(server.project_root),
        }
        data["authoring_worker"] = server.authoring_worker.status
        if data.get("task"):
            data["task"]["authoring"] = authoring
        return data

    def do_GET(self) -> None:
        if self.headers.get("Host") not in self.server.allowed_hosts():
            self._text(403, "forbidden: unexpected Host header")
            return
        url = urlsplit(self.path)
        if url.path == API_PATH:
            try:
                data = self._view(self._requested_task(url.query))
            except READ_ERRORS as error:
                self._json(500, {"error": str(error)})
                return
            self._json(200, data)
        elif url.path in {"/api/authoring/history", "/api/authoring/revision"}:
            from harness.authoring import AuthoringService
            from harness.dashboard.view import open_state

            try:
                query = parse_qs(url.query, keep_blank_values=True)
                keys = (
                    {"task", "before", "limit"}
                    if url.path.endswith("history")
                    else {"task", "revision"}
                )
                if (
                    not set(query) <= keys
                    or "task" not in query
                    or any(len(v) != 1 for v in query.values())
                ):
                    raise ValueError("invalid authoring query")
                if not self.server.db_path.is_file():
                    raise LookupError("missing database")
                with open_state(self.server.db_path) as controller:
                    service = AuthoringService(
                        controller,
                        self.server.project_root,
                        query["task"][0],
                        specs=self.server.specs,
                    )
                    data = (
                        service.history(
                            before=int(query.get("before", ["9223372036854775807"])[0]),
                            limit=int(query.get("limit", ["100"])[0]),
                        )
                        if url.path.endswith("history")
                        else service.store.revision(query["revision"][0])
                    )
                    if data is None:
                        raise LookupError("missing revision")
                self._json(200, data)
            except (LookupError, KeyError):
                self._json(404, {"error": "Unknown task-owned record"})
            except (ValueError, OSError, sqlite3.Error):
                self._json(400, {"error": "Invalid authoring read"})
        elif url.path == "/api/session/events":
            from harness.dashboard.session_actions import session_history

            try:
                query = parse_qs(url.query, keep_blank_values=True)
                if (
                    not set(query) <= {"task", "before", "limit"}
                    or "task" not in query
                    or any(len(v) != 1 for v in query.values())
                ):
                    raise ValueError("invalid session query")
                data = session_history(
                    self.server.db_path,
                    self.server.project_root,
                    query["task"][0],
                    before=int(query.get("before", ["9223372036854775807"])[0]),
                    limit=int(query.get("limit", ["50"])[0]),
                )
                self._json(200, data)
            except LookupError:
                self._json(404, {"error": "Unknown task in configured project"})
            except (ValueError, OSError, sqlite3.Error):
                self._json(400, {"error": "Invalid session history request"})
        elif url.path == "/api/authoring":
            from harness.dashboard.authoring_view import read_authoring

            try:
                query = parse_qs(url.query, keep_blank_values=True)
                if not set(query) <= {"task", "hosts"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("invalid authoring query")
                data = read_authoring(
                    self.server.db_path,
                    self.server.project_root,
                    self._requested_task(url.query),
                    specs=self.server.specs,
                    hosts=query.get("hosts", ["0"])[0] == "1",
                )
                data["worker"] = self.server.authoring_worker.status
                self._json(200, data)
            except LookupError:
                self._json(404, {"error": "Unknown task in configured project"})
            except (ValueError, OSError, sqlite3.Error):
                self._json(503, {"error": "Authoring state unavailable"})
        elif url.path == STREAM_PATH:
            self._stream(self._requested_task(url.query))
        elif url.path == DIAGRAMS_PATH:
            self._diagrams(url.query)
        elif url.path == COMPARE_PATH:
            self._compare(url.query)
        elif url.path in {"/api/execution", "/api/execution/events", "/api/execution/logs"}:
            from harness.dashboard.execution_view import read_execution

            try:
                query = parse_qs(url.query, keep_blank_values=True)
                allowed = {"task", "execution", "after", "limit", "log"}
                if not set(query) <= allowed or any(len(v) != 1 for v in query.values()):
                    raise ValueError("invalid execution query fields")
                task = self._requested_task(url.query)
                execution = query.get("execution", [""])[0]
                if not execution or not task:
                    raise ValueError("explicit task and execution required")
                action = (
                    "events"
                    if url.path.endswith("/events")
                    else ("logs" if url.path.endswith("/logs") else "status")
                )
                data = read_execution(
                    self.server.db_path,
                    task,
                    execution,
                    action=action,
                    after=int(query.get("after", ["0"])[0]),
                    limit=int(query.get("limit", ["100"])[0]),
                    log_id=query.get("log", [None])[0],
                )
                self._json(200, data)
            except LookupError as error:
                self._json(404, {"error": str(error)})
            except ValueError as error:
                self._json(400, {"error": str(error)})
            except (sqlite3.Error, OSError) as error:
                self._json(503, {"error": str(error)})
        elif url.path in {
            "/api/knowledge",
            "/api/knowledge/evidence",
            "/api/planning-recovery",
            "/api/spec-restoration",
        }:
            try:
                query = parse_qs(url.query, keep_blank_values=True)
                expected = (
                    {"task"}
                    if url.path == "/api/knowledge"
                    else (
                        {"task", "bundle_hash", "assertion_id"}
                        if url.path == "/api/knowledge/evidence"
                        else {"task", "execution", "epoch_id", "authority_generation"}
                    )
                )
                if set(query) != expected or any(len(values) != 1 for values in query.values()):
                    raise ValueError("invalid scoped query fields")
                if url.path == "/api/knowledge":
                    from harness.dashboard.knowledge_view import read_knowledge

                    data = read_knowledge(self.server.db_path, query["task"][0])
                elif url.path == "/api/knowledge/evidence":
                    from harness.dashboard.knowledge_view import read_knowledge_evidence

                    data = read_knowledge_evidence(
                        self.server.db_path,
                        query["task"][0],
                        query["bundle_hash"][0],
                        query["assertion_id"][0],
                    )
                elif url.path == "/api/spec-restoration":
                    from harness.dashboard.knowledge_view import spec_restoration_preview

                    generation = int(query["authority_generation"][0])
                    if generation < 0:
                        raise ValueError("invalid authority generation")
                    data = spec_restoration_preview(
                        self.server.db_path,
                        query["task"][0],
                        query["execution"][0],
                        query["epoch_id"][0],
                        generation,
                    )
                else:
                    from harness.dashboard.knowledge_view import planning_preview

                    generation = int(query["authority_generation"][0])
                    if generation < 0:
                        raise ValueError("invalid authority generation")
                    data = planning_preview(
                        self.server.db_path,
                        query["task"][0],
                        query["execution"][0],
                        query["epoch_id"][0],
                        generation,
                    )
                self._json(200, data)
            except LookupError as error:
                self._json(404, {"error": str(error)})
            except (ValueError, TypeError) as error:
                self._json(409, {"error": str(error)})
            except (sqlite3.Error, OSError) as error:
                self._json(503, {"error": str(error)})
        elif url.path in STATIC:
            name, content_type = STATIC[url.path]
            self._send(200, (STATIC_DIR / name).read_bytes(), content_type)
        else:
            self._text(404, "not found")

    def do_POST(self) -> None:
        from harness.planning_session_events import BROWSER_TASK

        token = BROWSER_TASK.set(None)
        try:
            self._post()
        finally:
            BROWSER_TASK.reset(token)

    def _post(self) -> None:
        self.close_connection = True
        if self.headers.get("Host") not in self.server.allowed_hosts():
            self._text(403, "forbidden: unexpected Host header")
            return
        path = urlsplit(self.path).path
        if path not in WRITES:
            self.send_error(501, "Unsupported method ('POST')")
            return
        refused = self._refusal()
        if refused is not None:
            self._json(*refused)
            return
        if path.startswith("/api/execution/") and int(self.headers["Content-Length"]) > 65536:
            self._json(413, {"error": "execution request exceeds 65536 bytes"})
            return
        if (
            path.startswith("/api/spec-restoration/")
            and int(self.headers["Content-Length"]) > 16384
        ):
            self._json(413, {"error": "specification restoration request exceeds 16384 bytes"})
            return
        if path == "/api/workspace/proposals" and int(self.headers["Content-Length"]) > 131072:
            self._json(413, {"error": "proposal request exceeds 131072 bytes"})
            return
        if path.startswith("/api/authoring/") and int(self.headers["Content-Length"]) > 131072:
            self._json(413, {"error": "authoring request exceeds 131072 bytes"})
            return
        body = self._body(int(self.headers["Content-Length"]))
        if body is None:
            self._json(400, {"error": "request body is incomplete"})
            return
        action, db_path = WRITES[path], self.server.db_path
        from harness.planning_session_events import BROWSER_TASK

        try:
            task = json.loads(body).get("task")
        except (ValueError, AttributeError):
            task = None
        compatible = action in {
            "authoring",
            "session-action",
            "workspace-proposal",
            "approve",
            "start",
            None,
            "resolve",
            "reopen",
        } or path.startswith("/api/execution/")
        if isinstance(task, str):
            if compatible:
                BROWSER_TASK.set(task)
            else:
                from harness.dashboard.view import open_state
                from harness.planning_session_contract import SessionError
                from harness.planning_session_store import PlanningSessionStore

                with open_state(db_path) as controller:
                    owner = PlanningSessionStore(controller.conn, task).owner()
                if owner and owner["mode"] == "attached":
                    self._json(409, SessionError("SESSION_OPERATION_INCOMPATIBLE").public())
                    return
        if action == "session-action":
            from harness.dashboard.session_actions import session_action

            self._json(*session_action(db_path, self.server.project_root, body))
        elif action == "authoring":
            from harness.dashboard.authoring_actions import authoring_action

            result = authoring_action(
                db_path,
                self.server.project_root,
                body,
                path.removeprefix("/api/authoring/"),
                specs=self.server.specs,
            )
            self._json(*result)
            if result[0] < 300:
                self.server.authoring_worker.start()
        elif action is None:
            self._json(*add_comment(db_path, body))
        elif action == "workspace-proposal":
            from harness.dashboard.workspace_actions import add_proposal

            self._json(*add_proposal(db_path, body, self.server.specs))
        elif action == "approve":
            self._json(*approve_task(db_path, body, self.server.specs))
        elif action == "start":
            from harness.dashboard.execution_start import start_task

            self._json(*start_task(db_path, body, self.server.specs))
        elif path.startswith("/api/execution/"):
            from harness.dashboard.execution_view import execution_action

            self._json(*execution_action(db_path, body, action))
        elif path.startswith("/api/knowledge/"):
            from harness.dashboard.knowledge_view import knowledge_action

            self._json(*knowledge_action(db_path, body, action.removeprefix("knowledge-")))
        elif path.startswith("/api/planning-recovery/"):
            from harness.dashboard.knowledge_view import planning_action

            self._json(*planning_action(db_path, body, action))
        elif path.startswith("/api/spec-restoration/"):
            from harness.dashboard.knowledge_view import spec_restoration_action

            self._json(*spec_restoration_action(db_path, body))
        else:
            self._json(*move_comment(db_path, body, action))

    def _body(self, length: int) -> bytes | None:
        """Exactly length bytes, read in chunks (no size cap); None when the client stops early."""
        chunks, remaining = [], length
        while remaining:
            chunk = self.rfile.read(min(remaining, READ_CHUNK))
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _refusal(self) -> tuple[int, dict[str, str]] | None:
        """Why the POST headers are refused (before any body byte is read), or None."""
        origins = {f"http://{host}" for host in self.server.allowed_hosts()}
        site = self.headers.get("Sec-Fetch-Site")
        if self.headers.get("Origin") not in origins or site not in (None, "same-origin"):
            return 403, {"error": "forbidden: cross-origin request"}
        if self.headers.get_content_type() != "application/json":
            return 415, {"error": "content type must be application/json"}
        lengths = self.headers.get_all("Content-Length") or []
        if "Transfer-Encoding" in self.headers or not lengths:
            return 411, {"error": "a Content-Length is required"}
        if len(lengths) > 1 or not LENGTH.fullmatch(lengths[0]):
            return 400, {"error": "Content-Length must be one non-negative integer"}
        return None

    def _diagrams(self, query: str) -> None:
        raw = parse_qs(query).get("version", [""])[0]
        if not VERSION.fullmatch(raw):
            self._json(400, {"error": "version must be a positive integer"})
            return
        task_id = self._requested_task(query)
        if task_id is None:
            self._json(404, {"error": NO_ACTIVE_TASK})
            return
        number = int(raw)
        if number > MAX_VERSION:
            self._json(404, {"error": f"unknown diagram version {raw} for task {task_id}"})
            return
        try:
            data = diagram_version_view(self.server.db_path, task_id, number)
        except LookupError as error:
            self._json(404, {"error": str(error)})
            return
        except (sqlite3.Error, OSError, ValueError) as error:
            self._json(500, {"error": str(error)})
            return
        self._json(200, data)

    def _compare(self, query: str) -> None:
        params = parse_qs(query)
        raw_a, raw_b = params.get("from", [""])[0], params.get("to", [""])[0]
        if not (VERSION.fullmatch(raw_a) and VERSION.fullmatch(raw_b)):
            self._json(400, {"error": "from and to must be positive integers"})
            return
        older, newer = int(raw_a), int(raw_b)
        if older >= newer:
            self._json(400, {"error": "from must be an earlier version than to"})
            return
        task_id = self._requested_task(query)
        if task_id is None:
            self._json(404, {"error": NO_ACTIVE_TASK})
            return
        if newer > MAX_VERSION:
            self._json(404, {"error": f"unknown diagram version {raw_b} for task {task_id}"})
            return
        try:
            data = compare_view(self.server.db_path, task_id, older, newer)
        except LookupError as error:
            self._json(404, {"error": str(error)})
            return
        except (sqlite3.Error, OSError, ValueError) as error:
            self._json(500, {"error": str(error)})
            return
        self._json(200, data)

    def _frame(self, task_id: str | None) -> bytes:
        try:
            data = json.dumps(self._view(task_id), sort_keys=True)
        except READ_ERRORS as error:
            failure = json.dumps({"error": str(error)}, sort_keys=True)
            return f"event: failure\ndata: {failure}\n\n".encode()
        return f"data: {data}\n\n".encode()

    def _write(self, chunk: bytes) -> None:
        self.wfile.write(chunk)
        self.wfile.flush()

    def _emit(self, chunk):
        with self.server.emission_lock:
            if self.server.closing.is_set():
                return False
            self._write(chunk)
            return True

    def _stream(self, task_id: str | None) -> None:
        """Server-sent events: the view whenever it changes, until closed or recycled."""
        server = self.server
        self.close_connection = True
        self.connection.settimeout(5)
        self.send_response(200)
        self.send_header("Content-Type", STREAM_TYPE)
        self.end_headers()
        deadline = time.monotonic() + server.stream_lifetime
        try:
            if not self._emit(f"retry: {RETRY_MS}\n\n".encode()):
                return
            last, written = b"", time.monotonic()
            while not server.closing.is_set():
                frame = self._frame(task_id)
                now = time.monotonic()
                if frame != last:
                    if not self._emit(frame):
                        break
                    last, written = frame, now
                elif now - written >= KEEPALIVE_SECONDS:
                    if not self._emit(b": keep-alive\n\n"):
                        break
                    written = now
                remaining = deadline - now
                if remaining <= 0 or server.closing.wait(min(server.poll_seconds, remaining)):
                    break
        except OSError:
            return
