"""The dashboard's HTTP server: 127.0.0.1 only, an OS-chosen free port, a fixed set of routes.

GET only. Every request is checked against the expected Host header (DNS rebinding guard),
then answered from the read model or the packaged static files; nothing is written.
"""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from harness.dashboard.view import dashboard_view

LOCALHOST = "127.0.0.1"
API_PATH = "/api/dashboard"
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

    def __init__(self, db_path: Path, task_id: str | None, specs: str | None = None):
        self.db_path = Path(db_path)
        self.task_id = task_id
        self.specs = specs
        super().__init__((LOCALHOST, 0), DashboardHandler)

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

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, sort_keys=True).encode("utf-8"), JSON_TYPE)

    def _text(self, status: int, text: str) -> None:
        self._send(status, f"{text}\n".encode(), TEXT_TYPE)

    def do_GET(self) -> None:
        if self.headers.get("Host") not in self.server.allowed_hosts():
            self._text(403, "forbidden: unexpected Host header")
            return
        path = urlsplit(self.path).path
        if path == API_PATH:
            server = self.server
            try:
                data = dashboard_view(server.db_path, server.task_id, server.specs)
            except READ_ERRORS as error:
                self._json(500, {"error": str(error)})
                return
            self._json(200, data)
        elif path in STATIC:
            name, content_type = STATIC[path]
            self._send(200, (STATIC_DIR / name).read_bytes(), content_type)
        else:
            self._text(404, "not found")
