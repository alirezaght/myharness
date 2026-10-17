"""The dashboard's HTTP server: 127.0.0.1 only, an OS-chosen free port, a fixed set of routes.

GET only. Every request is checked against the expected Host header (DNS rebinding guard),
then answered from the read model, its live stream or the packaged static files; nothing is
written. `?task=<id>` selects the task; without it the task the dashboard was launched for.
"""

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from harness.dashboard.view import dashboard_view

LOCALHOST = "127.0.0.1"
API_PATH = "/api/dashboard"
STREAM_PATH = "/api/stream"
STREAM_TYPE = "text/event-stream; charset=utf-8"
RETRY_MS = 2000
KEEPALIVE_SECONDS = 15.0
STATIC_DIR = files("harness.dashboard") / "static"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
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

    def __init__(self, db_path: Path, task_id: str | None, specs: str | None = None):
        self.db_path = Path(db_path)
        self.task_id = task_id
        self.specs = specs
        self.closing = threading.Event()
        super().__init__((LOCALHOST, 0), DashboardHandler)

    def server_close(self) -> None:
        self.closing.set()
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
        return dashboard_view(server.db_path, task_id, server.specs)

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
        elif url.path == STREAM_PATH:
            self._stream(self._requested_task(url.query))
        elif url.path in STATIC:
            name, content_type = STATIC[url.path]
            self._send(200, (STATIC_DIR / name).read_bytes(), content_type)
        else:
            self._text(404, "not found")

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

    def _stream(self, task_id: str | None) -> None:
        """Server-sent events: the view whenever it changes, until closed or recycled."""
        server = self.server
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", STREAM_TYPE)
        self.end_headers()
        deadline = time.monotonic() + server.stream_lifetime
        try:
            self._write(f"retry: {RETRY_MS}\n\n".encode())
            last, written = b"", time.monotonic()
            while not server.closing.is_set():
                frame = self._frame(task_id)
                now = time.monotonic()
                if frame != last:
                    self._write(frame)
                    last, written = frame, now
                elif now - written >= KEEPALIVE_SECONDS:
                    self._write(b": keep-alive\n\n")
                    written = now
                remaining = deadline - now
                if remaining <= 0 or server.closing.wait(min(server.poll_seconds, remaining)):
                    break
        except OSError:
            return
