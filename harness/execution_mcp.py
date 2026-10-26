"""Closed loopback MCP transport. Authentication never supplies a native role."""

import json
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.native_tool_policy import TOOLS


def _schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING = {"type": "string", "maxLength": 65536}
INTEGER = {"type": "integer", "minimum": 0, "maximum": 1048576}
SCHEMAS = {
    "read_file": _schema({"path": STRING, "offset": INTEGER, "limit": INTEGER}, ["path"]),
    "apply_patch": _schema(
        {"patch": STRING, "task_id": STRING, "expected_revision": STRING},
        ["patch", "task_id", "expected_revision"],
    ),
    "run_command": _schema(
        {
            "argv": {"type": "array", "items": STRING, "maxItems": 100},
            "cwd": STRING,
            "task_id": STRING,
            "expected_revision": STRING,
            "timeout": INTEGER,
        },
        ["argv", "cwd", "task_id", "expected_revision"],
    ),
    "poll_process": _schema({"handle": STRING, "limit": INTEGER}, ["handle"]),
    "cancel_process": _schema({"handle": STRING}, ["handle"]),
    "al_command": _schema(
        {
            "arguments": {"type": "array", "items": STRING, "maxItems": 100},
            "background": {"type": "boolean"},
            "timeout": INTEGER,
        },
        ["arguments"],
    ),
    "diagnostic": _schema({"request": {"type": "object"}}, ["request"]),
    "delivery_operation": _schema(
        {
            "operation": {"type": "string", "enum": ["status", "poll", "publish", "reconcile"]},
            "request_id": STRING,
        },
        ["operation", "request_id"],
    ),
}


def validate_arguments(name, arguments):
    schema = SCHEMAS[name]
    if (
        not isinstance(arguments, dict)
        or set(arguments) - schema["properties"].keys()
        or not set(schema["required"]) <= arguments.keys()
    ):
        raise ValueError("closed tool arguments required")
    for key, value in arguments.items():
        spec = schema["properties"][key]
        if spec["type"] == "string" and (
            not isinstance(value, str) or len(value) > spec.get("maxLength", 65536)
        ):
            raise ValueError("bounded string required")
        if spec["type"] == "integer" and (type(value) is not int or not 0 <= value <= 1048576):
            raise ValueError("bounded integer required")
        if spec["type"] == "boolean" and type(value) is not bool:
            raise ValueError("boolean required")
        if name == "al_command" and key == "artifacts":
            if (
                not isinstance(value, list)
                or len(value) > 20
                or any(
                    not isinstance(v, dict)
                    or set(v) != {"artifact_id", "sha256"}
                    or any(not isinstance(t, str) for t in v.values())
                    for v in value
                )
            ):
                raise ValueError("closed execution artifact references required")
            continue
        if spec["type"] == "array" and (
            not isinstance(value, list)
            or len(value) > 100
            or any(not isinstance(v, str) or len(v) > 65536 for v in value)
        ):
            raise ValueError("bounded string array required")
        if spec["type"] == "object" and not isinstance(value, dict):
            raise ValueError("object required")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError("unsupported operation")


SCHEMAS["write_artifact"] = _schema(
    {
        "name": STRING,
        "content": STRING,
        "expected_graph_revision": INTEGER,
        "expected_sha256": STRING,
    },
    ["name", "content", "expected_graph_revision"],
)
SCHEMAS["await_assignment"] = _schema(
    {"timeout": {"type": "integer", "minimum": 1, "maximum": 30}}, []
)
SCHEMAS["submit_output"] = _schema(
    {
        "name": STRING,
        "content": {"type": "string", "maxLength": 262144},
        "expected_graph_revision": INTEGER,
    },
    ["name", "content", "expected_graph_revision"],
)
SCHEMAS["al_command"]["properties"].update(
    artifacts={
        "type": "array",
        "maxItems": 20,
        "items": _schema({"artifact_id": STRING, "sha256": STRING}, ["artifact_id", "sha256"]),
    }
)


class MCPConnection:
    def __init__(self, resolve_principal, dispatcher, verifier=None, claim=None, channel=None):
        self.resolve_principal, self.dispatcher = resolve_principal, dispatcher
        self.session = secrets.token_urlsafe(24)
        self.initialized = False
        self.verifier, self.claim, self.channel = verifier, claim, channel

    def request(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("JSON-RPC 2.0 object required")
        method, params = message.get("method"), message.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("object params required")
        if method == "initialize":
            if self.initialized:
                raise ValueError("MCP connection already initialized")
            self.initialized = True
            return {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "harness-operations", "version": "1"},
            }
        if not self.initialized:
            raise ValueError("MCP initialization required")
        if method in {"notifications/initialized", "ping"}:
            return {}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": name,
                        "description": "Guarded Harness " + name,
                        "inputSchema": SCHEMAS[name],
                    }
                    for name in TOOLS
                ]
            }
        if method != "tools/call":
            raise ValueError("unsupported MCP method")
        try:
            name = params.get("name")
            if name not in TOOLS:
                raise ValueError("unknown controller tool")
            arguments = params.get("arguments", {})
            attestation = None
            if self.verifier:
                principal, arguments, attestation = self.verifier(self.channel, name, arguments)
            else:
                principal = self.resolve_principal(params.get("_meta"))
            validate_arguments(name, arguments)
            if attestation:
                self.claim(attestation)
            result = self.dispatcher(principal, name, arguments)
            return {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}
        except ValueError as error:
            return {"content": [{"type": "text", "text": str(error)[:2048]}], "isError": True}


@contextmanager
def serve_mcp(resolve_principal, dispatcher, *, verifier=None, claim=None, channel=None):
    token = secrets.token_urlsafe(32)
    connections = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if (
                self.path != "/mcp"
                or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin")
                or self.headers.get("Authorization") != "Bearer " + token
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"
            ):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= 1048576:
                    raise ValueError("MCP body exceeds limit")
                self.connection.settimeout(10)
                message = json.loads(self.rfile.read(length))
                with lock:
                    if message.get("method") == "initialize":
                        connection = MCPConnection(
                            resolve_principal, dispatcher, verifier, claim, channel
                        )
                        connections[connection.session] = connection
                    else:
                        connection = connections.get(self.headers.get("Mcp-Session-Id"))
                        if connection is None:
                            raise ValueError("unknown MCP session")
                result = connection.request(message)
                response = {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
                raw = json.dumps(response).encode()
                self.send_response(202 if "id" not in message else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", connection.session)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (ValueError, TypeError, AttributeError):
                self.send_error(400, "invalid MCP request")

        def do_GET(self):
            self.send_error(405)

        def do_DELETE(self):
            self.send_error(405)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{server.server_port}/mcp", "token": token}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
