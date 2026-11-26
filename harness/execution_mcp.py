"""Closed loopback MCP transport. Authentication never supplies a native role."""

import json
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.native_tool_policy import TOOLS

TOOL_HELP = {
    "knowledge_query": (
        "Query authorized engineering knowledge using text and optional entity_refs. Returns "
        "bounded sourced assertions, relationships, integrity, coverage and truncation metadata. "
        "No result is not proof of no dependency. Context grants no execution authority."
    ),
    "knowledge_explain": (
        "Explain an assertion using its bundle_hash and assertion_id. Rechecks current "
        "authority and source evidence; stale or revoked access can refuse explanation."
    ),
    "knowledge_consumers": (
        "Who consumes a target: a repository path, directory, dotted Python module or "
        "endpoint (endpoint:/path). Returns static importers, including transitive consumers "
        "through re-exporting modules such as index.ts barrels (relation "
        "imports-via-reexport with the through chain), and runtime HTTP callers, each with "
        "its evidence span; unresolvable layout is reported as a gap. No result is not proof "
        "of no consumer."
    ),
    "read_file": (
        "Read an authorized confined source or issued resource using path and optional offset "
        "and limit. Ordinary source pages include stable full-source SHA256/version up to the "
        "reported hash budget; larger sources retain paging with sha256=null and a reason. "
        "Use byte next_offset cursors; access remains guarded."
    ),
    "await_assignment": (
        "Obtain current assignment and graph_revision. Root orchestrator receives "
        "Supervisor with guarded AL control authority and writable=false; product edits "
        "require admitted scoped writers. ReadOnly, Pending and unavailable "
        "handoff/context do not grant writer authority. registered_assignment observes only your "
        "registered AL task and distinct Harness execution binding, not writer admission. "
        "knowledge_contribution_contract describes advisory report formatting. "
        "Refresh before submitting output."
    ),
    "al_command": (
        "Run the bounded Harness subset of guarded AL commands, e.g. "
        "arguments=['show', '--panel']. "
        "Standalone AL commands such as isolate are unavailable through this tool; hand those "
        "operations back to the supervising host operator. Never include alctl, "
        "launcher paths, run or repository/session wrapper flags; Harness supplies the origin. "
        "Supervisor can plan/control without product writer authority; read-only children may "
        "only inspect AL. Long checks use background=true and poll_process until collected."
    ),
    "diagram_operation": (
        "Inspect current source binding, graph_revision and complete publication_request. "
        "Only orchestrator may publish that closed request with JSON-string content and exact "
        "expected_graph_revision; inspect again after stale revision or source errors. "
        "Diagrams are reference views and never grant specification or implementation approval."
    ),
    "write_artifact": (
        "Store a named execution draft using current expected_graph_revision; use returned "
        "artifact:<artifact_id>:<sha256> for AL file inputs. This grants no product write"
        " authority."
    ),
    "apply_patch": (
        "Apply a product patch only for the caller's admitted writable task_id and exact "
        "expected_revision. Supervisor control authority alone does not permit product edits."
    ),
    "run_command": (
        "Run bounded argv in confined cwd. Product writes require an admitted task_id and "
        "expected_revision; guarded read commands grant no writer authority. Poll the handle."
    ),
    "poll_process": (
        "Collect output/status for a process handle owned by this principal. Continue polling "
        "until terminal output is collected before ending the native turn."
    ),
    "cancel_process": (
        "Cancel an owned process handle; collect its terminal result with poll_process."
    ),
    "diagnostic": (
        "Inspect a closed guarded diagnostic request; this grants no execution authority."
    ),
    "delivery_operation": (
        "Inspect or poll delivery state; publish/reconcile require current supervisor delivery "
        "authority and the exact request_id. This does not bypass AL approval."
    ),
    "environment_operation": (
        "Inspect environment readiness; Supervisor may prepare locked dependencies for the "
        "registered task_id and expected_revision. Inspect supplies closed prepare_requests. "
        "cwd must be '.' or a repository-relative unit, never an absolute path. "
        "Poll setup handles; setup is not evidence."
    ),
    "submit_output": (
        "Submit an execution-owned named report with content and the current "
        "expected_graph_revision. Returns an immutable artifact reference; storing a report "
        "does not complete an AL task or verify its conclusions. knowledge_contribution gives "
        "advisory format feedback for the exact preserved bytes. Assertion keys: subject, "
        "predicate, object, value, text, parents. Parents must be full assertion IDs, not "
        "source locators. Origin and verification are server-assigned; narrative classifications "
        "belong in text. Format readiness does not prove completion, parent eligibility, "
        "visibility or ingestion. Limits: 40 assertions, 40 parents, 8192 text characters."
    ),
}


TOOL_HELP["knowledge_query"] += (
    " receipt_id identifies this operation's persisted receipt; receipt_id and bundle_hash "
    "are transport metadata outside the hashed payload. derivation_parents are full evidence "
    "assertion IDs, separate from traversal paths."
)
TOOL_HELP["knowledge_explain"] += (
    " Returns this explanation's receipt_id, bundle_hash, and bounded derivation parents, "
    "edges and supporting bundle evidence with explicit completeness."
)


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
    "knowledge_query": _schema(
        {
            "query": {"type": "string", "maxLength": 4096},
            "entity_refs": {"type": "array", "items": STRING, "maxItems": 40},
        },
        [],
    ),
    "knowledge_explain": _schema(
        {"bundle_hash": STRING, "assertion_id": STRING}, ["bundle_hash", "assertion_id"]
    ),
    "knowledge_consumers": _schema(
        {
            "target": {"type": "string", "maxLength": 1024},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        ["target"],
    ),
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
            "arguments": {
                "type": "array",
                "items": STRING,
                "maxItems": 100,
                "description": (
                    "Bounded Harness command subset, e.g. ['show', '--panel']; "
                    "standalone-only commands such as isolate are unavailable. "
                    "No launcher or origin flags."
                ),
            },
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


def _within(spec, value, low, high):
    """The field's own published bounds (#113), inside the generic outer bounds."""
    return spec.get(low, float("-inf")) <= value <= spec.get(high, float("inf"))


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
            not isinstance(value, str)
            or len(value) > spec.get("maxLength", 65536)
            or len(value) < spec.get("minLength", 0)
        ):
            raise ValueError("bounded string required")
        if spec["type"] == "integer" and (
            type(value) is not int
            or not 0 <= value <= 1048576
            or not _within(spec, value, "minimum", "maximum")
        ):
            raise ValueError("bounded integer required")
        if spec["type"] == "boolean" and type(value) is not bool:
            raise ValueError("boolean required")
        if name == "al_command" and key == "artifacts":
            if (
                not isinstance(value, list)
                or len(value) > 20
                or not _within(spec, len(value), "minItems", "maxItems")
                or any(
                    not isinstance(v, dict)
                    or set(v) != {"artifact_id", "sha256"}
                    or any(
                        not isinstance(t, str)
                        or not _within(
                            spec["items"]["properties"][k], len(t), "minLength", "maxLength"
                        )
                        for k, t in v.items()
                    )
                    for v in value
                )
            ):
                raise ValueError("closed execution artifact references required")
            continue
        item = spec.get("items", {})
        if spec["type"] == "array" and (
            not isinstance(value, list)
            or len(value) > 100
            or not _within(spec, len(value), "minItems", "maxItems")
            or any(
                not isinstance(v, str)
                or len(v) > 65536
                or not _within(item, len(v), "minLength", "maxLength")
                for v in value
            )
        ):
            raise ValueError("bounded string array required")
        if spec["type"] == "object" and not isinstance(value, dict):
            raise ValueError("object required")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError("unsupported operation")


SCHEMAS["environment_operation"] = _schema(
    {
        "operation": {"type": "string", "enum": ["inspect", "prepare"]},
        "cwd": {
            **STRING,
            "description": "Use '.' or a repository-relative unit; absolute paths are refused.",
        },
        "task_id": STRING,
        "expected_revision": STRING,
    },
    ["operation"],
)
SCHEMAS["diagram_operation"] = _schema(
    {
        "operation": {"type": "string", "enum": ["inspect", "publish"]},
        "content": {
            **STRING,
            "description": "JSON-string content from inspect publication_request.",
        },
        "expected_graph_revision": {
            **INTEGER,
            "description": "Exact graph_revision returned by inspect; required for publish.",
        },
    },
    ["operation"],
)

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
        "contract": {"type": "object"},
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
                        "description": TOOL_HELP.get(name, "Guarded Harness " + name),
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
