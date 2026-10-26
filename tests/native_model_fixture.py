"""Synthetic model service drives actual native tool dispatch; retains no auth data."""

import functools
import json
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def native_executable(host):
    """Skip optional installed-binary checks when the exact supported CLI is absent."""
    import pytest

    version = {"claude": "2.1.310", "codex": "0.164.0"}[host]
    clean_path = os.pathsep.join(
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if "native-provider-bin" not in p and "provider-bin" not in p
    )
    candidates = [
        os.environ.get("HARNESS_TEST_" + host.upper()),
        shutil.which(host, path=clean_path),
        str(Path.home() / ".local/bin" / host),
    ]
    for path in candidates:
        if not path or not Path(path).is_file():
            continue
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and version in result.stdout:
            return path
    message = f"installed {host} {version} unavailable; native integration check not run"
    if os.environ.get("HARNESS_REQUIRE_NATIVE_TESTS") == "1":
        pytest.fail(message)
    pytest.skip(message)


@contextmanager
def responses_fixture(calls):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(
                {
                    "keys": sorted(value),
                    "path": self.path,
                    "model": value.get("model"),
                    "tools": value.get("tools", []),
                    "input": value.get("input", []),
                }
            )
            index = len(observed) - 1
            selected = (
                calls(value, index)
                if callable(calls)
                else (calls[index] if index < len(calls) else None)
            )
            output = (
                [
                    {
                        "id": "fc_" + str(index),
                        "type": "function_call",
                        "call_id": "call_" + str(index),
                        "name": selected[0].rsplit(".", 1)[-1],
                        **(
                            {"namespace": selected[0].rsplit(".", 1)[0]}
                            if "." in selected[0]
                            else {}
                        ),
                        "arguments": json.dumps(selected[1]),
                    }
                ]
                if selected is not None
                else [
                    {
                        "id": "msg_final",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Native fixture done",
                                "annotations": [],
                            }
                        ],
                    }
                ]
            )
            response = {
                "id": "resp_" + str(index),
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "output": output,
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
            events = [
                ("response.created", {"response": dict(response, status="in_progress", output=[])})
            ]
            for item in output:
                events.extend(
                    [
                        ("response.output_item.added", {"output_index": 0, "item": item}),
                        ("response.output_item.done", {"output_index": 0, "item": item}),
                    ]
                )
            events.append(("response.completed", {"response": response}))
            raw = b"".join(
                (
                    "event: " + kind + "\ndata: " + json.dumps({"type": kind, **data}) + "\n\n"
                ).encode()
                for kind, data in events
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def codex_fixture_argv(policy, url, repository, *, model="gpt-6.1-sol", resume=None):
    return [
        policy["executable"]["path"],
        "exec",
        *policy["options"],
        "-c",
        'model_provider="synthetic"',
        "-c",
        'model_providers.synthetic.name="Synthetic fixture"',
        "-c",
        "model_providers.synthetic.base_url=" + json.dumps(url),
        "-c",
        'model_providers.synthetic.wire_api="responses"',
        "-c",
        "model_providers.synthetic.requires_openai_auth=false",
        "-c",
        "features.enable_request_compression=false",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--json",
        "--cd",
        str(repository),
        "--model",
        model,
        *(["resume", resume] if resume else []),
        "Native boundary fixture",
    ]


def tool_inventory(request):
    raw = list(request.get("tools", []))
    for item in request.get("input", []):
        if item.get("type") == "additional_tools":
            raw.extend(item["tools"])

    def flatten(tools, prefix=""):
        result = {}
        for tool in tools:
            if tool.get("type") == "namespace":
                result.update(flatten(tool["tools"], prefix + tool["name"] + "."))
            else:
                result[prefix + tool.get("name", tool.get("type", "unknown"))] = tool
        return result

    return flatten(raw)


@contextmanager
def anthropic_fixture(calls):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(
                {
                    "model": value.get("model"),
                    "tools": value.get("tools", []),
                    "messages": value.get("messages", []),
                }
            )
            index = len(observed) - 1
            selected = (
                calls(value, index)
                if callable(calls)
                else (calls[index] if index < len(calls) else None)
            )
            message = {
                "id": "msg_" + str(index),
                "type": "message",
                "role": "assistant",
                "model": value.get("model", "claude-sonnet-4-6"),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            events = [("message_start", {"message": message})]
            blocks = selected if isinstance(selected, list) else [selected]
            for position, call in enumerate(blocks):
                block = (
                    {
                        "type": "tool_use",
                        "id": call[2]
                        if len(call) > 2
                        else "toolu_" + str(index) + "_" + str(position),
                        "name": call[0],
                        "input": {},
                    }
                    if call
                    else {"type": "text", "text": ""}
                )
                delta = (
                    {"type": "input_json_delta", "partial_json": json.dumps(call[1])}
                    if call
                    else {"type": "text_delta", "text": "Native fixture done"}
                )
                events.extend(
                    [
                        ("content_block_start", {"index": position, "content_block": block}),
                        ("content_block_delta", {"index": position, "delta": delta}),
                        ("content_block_stop", {"index": position}),
                    ]
                )
            events.extend(
                [
                    (
                        "message_delta",
                        {
                            "delta": {
                                "stop_reason": "tool_use" if selected else "end_turn",
                                "stop_sequence": None,
                            },
                            "usage": {"output_tokens": 1},
                        },
                    ),
                    ("message_stop", {}),
                ]
            )
            raw = b"".join(
                (
                    "event: " + kind + "\ndata: " + json.dumps({"type": kind, **data}) + "\n\n"
                ).encode()
                for kind, data in events
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@functools.lru_cache
def operation_sandbox_unavailable():
    """Optional host capability; the dedicated containment job requires it explicitly."""
    import os
    import shutil
    import subprocess
    import sys

    if sys.platform != "linux" or os.environ.get("HARNESS_REQUIRE_OPERATION_TESTS") == "1":
        return False
    binary = shutil.which("bwrap")
    if not binary:
        return True
    try:
        result = subprocess.run(
            [binary, "--unshare-user", "--unshare-net", "--ro-bind", "/", "/", "--", "/bin/true"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode != 0
    except (OSError, subprocess.SubprocessError):
        return True
