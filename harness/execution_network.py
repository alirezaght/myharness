"""Destination-pinned provider-only HTTP transport owned by the supervisor."""

import http.client
import ipaddress
import secrets
import socket
import ssl
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address, port):
        super().__init__(hostname, port=port, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


@contextmanager
def provider_transport(config):
    url = urlsplit(config["endpoint"])
    fixture = config.get("test_loopback", False)
    if (
        url.username
        or url.password
        or url.query
        or url.fragment
        or not url.hostname
        or url.path not in {"", "/"}
        or (url.scheme != "https" and not fixture)
    ):
        raise ValueError("provider endpoint requires explicit credential-free HTTPS origin")
    addresses = {
        v[4][0] for v in socket.getaddrinfo(url.hostname, url.port or 443, type=socket.SOCK_STREAM)
    }
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        if not (
            fixture and addresses and all(ipaddress.ip_address(a).is_loopback for a in addresses)
        ):
            raise ValueError("provider endpoint resolved to private/reserved address")
    address, token = sorted(addresses)[0], secrets.token_urlsafe(32)
    credential = config.get("credential", "")
    provider = config["provider"]
    paths = {
        "claude": {"/v1/messages", "/v1/messages/count_tokens"},
        "codex": {"/v1/responses"},
    }.get(provider)
    if paths is None:
        raise ValueError("unsupported provider transport")
    semaphore = threading.BoundedSemaphore(8)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        def do_POST(self):
            if (
                self.path not in paths
                or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Authorization") != "Bearer " + token
                or self.headers.get("Transfer-Encoding")
                or not semaphore.acquire(blocking=False)
            ):
                self.send_error(403)
                return
            connection = None
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= 4 * 1024 * 1024:
                    self.send_error(413)
                    return
                self.connection.settimeout(30)
                body = self.rfile.read(length)
                if len(body) != length:
                    self.send_error(400)
                    return
                connection = (
                    http.client.HTTPConnection(address, url.port or 80, timeout=30)
                    if fixture and url.scheme == "http"
                    else PinnedHTTPS(url.hostname, address, url.port or 443)
                )
                headers = {"Content-Type": "application/json", "Host": url.netloc}
                if provider == "claude":
                    headers.update({"x-api-key": credential, "anthropic-version": "2023-06-01"})
                else:
                    headers["Authorization"] = "Bearer " + credential
                connection.request("POST", self.path, body=body, headers=headers)
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    self.send_error(502, "provider redirects forbidden")
                    return
                if response.status >= 400:
                    self.send_error(response.status, "provider request failed")
                    return
                self.send_response(response.status)
                self.send_header(
                    "Content-Type", response.getheader("Content-Type", "application/json")
                )
                self.end_headers()
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > 32 * 1024 * 1024:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, ValueError, http.client.HTTPException):
                self.close_connection = True
            finally:
                if connection:
                    connection.close()
                semaphore.release()

        def do_CONNECT(self):
            self.send_error(403)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{server.server_port}",
            "port": server.server_port,
            "token": token,
            "provider": provider,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
