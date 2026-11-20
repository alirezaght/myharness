"""Synthetic native RPC executable and no-tool synthesis; never a real provider."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from harness.investigation_policy import (
    ENDPOINT,
    REMOTE_CONTROL_ENV,
    RESTRICTIONS,
    TOOLS,
    AcquisitionCapability,
    effective_config_digests,
)

PROGRAM = r"""
import json, sys, time, os
from pathlib import Path

settings = json.loads(Path(sys.argv[0] + ".json").read_text())
thread = "owned-thread"
for line in sys.stdin:
    request = json.loads(line)
    with Path(sys.argv[0] + ".calls").open("a") as log:
        log.write(json.dumps(request) + "\n")
    if "id" not in request:
        continue
    ident, method, params = request["id"], request["method"], request.get("params", {})
    mode = settings.get("mode")
    if mode == "timeout":
        time.sleep(10)
    if mode == "oversize":
        print("x" * 300000, flush=True)
        continue
    if mode == "interactive":
        print(
            json.dumps(
                {"id": 999, "method": "item/commandExecution/requestApproval", "params": {}}
            ),
            flush=True,
        )
        continue
    if mode == "auth" and method == "mcpServerStatus/list":
        print(
            json.dumps(
                {
                    "method": "mcpServer/startupStatus/updated",
                    "params": {
                        "name": "atlassian",
                        "status": "failed",
                        "error": "authentication required secret-value",
                    },
                }
            ),
            flush=True,
        )
    if mode == "auth-before-login" and method == "thread/start":
        print(
            json.dumps(
                {
                    "method": "mcpServer/startupStatus/updated",
                    "params": {
                        "name": "atlassian",
                        "status": "failed",
                        "error": "authentication required",
                    },
                }
            ),
            flush=True,
        )
    if method == "initialize":
        Path(sys.argv[0] + ".environment").write_text(json.dumps({
            "marker": os.environ.get("CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED")
        }))
        if mode != "remote-missing":
            params = {"installationId": "fixture-install", "serverName": "fixture",
                      "status": ("unknown" if mode == "remote-unknown" else
                                 "connected" if mode == "remote-enabled" else "disabled")}
            if mode == "remote-malformed":
                del params["installationId"]
            print(
                json.dumps({"method": "remoteControl/status/changed", "params": params}),
                flush=True,
            )
        result = {}
    elif method == "config/read":
        result = {"config": settings["config"]}
    elif method == "thread/start":
        if mode == "remote-later-enabled":
            print(json.dumps({"method": "remoteControl/status/changed", "params": {
                "installationId": "fixture-install", "serverName": "fixture", "status": "connecting"
            }}), flush=True)
        result = {"thread": {"id": thread}}
    elif method == "mcpServerStatus/list":
        result = {
            "data": [
                {
                    "name": "atlassian",
                    "authStatus": "oAuth",
                    "runtimeStatus": "connected" if mode != "runtime" else None,
                    "tools": settings["tools"],
                    "toolsError": None,
                }
            ],
            "nextCursor": None,
        }
    elif method == "mcpServer/oauth/login":
        result = {
            "loginId": "owned-login",
            "authorizationUrl": "https://auth.atlassian.com/authorize?synthetic=consent",
        }
        notification = {
            "method": "mcpServer/oauthLogin/completed",
            "params": {
                "name": "atlassian",
                "threadId": thread,
                "loginId": "other-login" if mode == "foreign-login" else "owned-login",
                "success": True,
            },
        }
        print(json.dumps(notification), flush=True)
    elif method == "mcpServer/tool/call":
        if params["tool"] == "getAccessibleAtlassianResources":
            content = [
                {
                    "id": "cloud-one",
                    "url": "https://one.atlassian.net",
                    "scopes": ["read:jira:agent-interface"],
                }
            ]
        else:
            content = {
                "key": "AR-9999" if mode == "wrong-issue" else params["arguments"]["issueIdOrKey"],
                "cloudId": "cloud-one",
                "fields": {
                    "summary": "CSV export",
                    "description": settings.get(
                        "description", "Export accounts as CSV. Keep existing exports unchanged."
                    ),
                    "status": "Open",
                    "updated": "version-1",
                },
            }
        result = {"content": [], "structuredContent": content, "isError": mode == "read-denied"}
    else:
        result = {}
    print(
        json.dumps({"id": ident + 1 if mode == "wrong-id" else ident, "result": result}), flush=True
    )
    if mode == "nonzero" and method == "mcpServer/tool/call":
        sys.exit(1)
"""


@pytest.fixture
def native_fixture(tmp_path, monkeypatch):
    home = tmp_path / "native-home"
    (home / ".codex").mkdir(parents=True)
    config_file = home / ".codex/config.toml"
    config_file.write_text("fixture = true\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    config = {}
    for name, value in RESTRICTIONS.items():
        target = config
        parts = name.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    config["mcp_servers"] = {
        "atlassian": {
            "url": ENDPOINT,
            "enabled": True,
            "enabled_tools": list(TOOLS),
            "disabled_tools": [],
        },
        "foreign": {"command": "/usr/bin/false", "enabled": False},
    }
    tools = {
        TOOLS[0]: {
            "name": TOOLS[0],
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        TOOLS[1]: {
            "name": TOOLS[1],
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cloudId": {"type": "string"},
                    "issueIdOrKey": {"type": "string"},
                    "fields": {"type": "array"},
                },
                "required": ["cloudId", "issueIdOrKey"],
            },
        },
    }
    executable = tmp_path / "native-fixture"
    executable.write_text("#!" + sys.executable + "\n" + PROGRAM)
    executable.chmod(0o700)
    settings = Path(str(executable) + ".json")
    settings.write_text(json.dumps({"config": config, "tools": tools}))
    qualified = AcquisitionCapability(
        {
            "path": str(executable),
            "version": "0.170.0",
            "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        },
        config,
        tools,
        {"site": "cloudId", "issue": "issueIdOrKey", "fields": "fields"},
        (
            {
                "id": "cloud-one",
                "url": "https://one.atlassian.net",
                "verified": True,
                "scopes": ["read:jira:agent-interface"],
            },
        ),
        True,
        True,
        True,
        ("read:jira:agent-interface",),
        {
            "native_version": "0.170.0",
            "child_environment": {REMOTE_CONTROL_ENV: "1"},
            "native_home": str(home / ".codex"),
            "config_inputs": [
                {
                    "path": str(config_file),
                    "sha256": hashlib.sha256(config_file.read_bytes()).hexdigest(),
                }
            ],
        },
        effective_config_digests(config),
    )

    def mode(value):
        data = json.loads(settings.read_text())
        data["mode"] = value
        settings.write_text(json.dumps(data))

    def calls():
        path = Path(str(executable) + ".calls")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    monkeypatch.setattr("harness.investigation_policy.capability", lambda: qualified)
    monkeypatch.setattr(
        "harness.authoring_policy.availability",
        lambda host: {"host": host, "available": True, "status": "fixture"},
    )
    return qualified, mode, calls


def synthesis(inputs, *, decisions=False):
    source = inputs["investigation"]["sources"][0]
    quote = source["content"]
    citation = {
        "source": source["id"],
        "sha256": source["sha256"],
        "start": 0,
        "end": len(quote),
        "quote": quote,
    }
    coverage = [
        {
            "key": e["key"],
            "disposition": "fact",
            "statement": "Preserve CSV exports",
            "rationale": "Proposed interpretation of supplied source; review required",
            "citations": [citation],
        }
        for e in inputs["investigation"]["catalogue"]
    ]
    decision_rows = []
    if decisions:
        coverage[0]["disposition"] = "decision"
        decision_rows = [
            {
                "concern": coverage[0]["key"],
                "text": "Should archived accounts be exported?",
                "citations": [citation],
                "supersedes": None,
                "reason": "Product decision not settled by source",
            }
        ]
    ref = f"investigation:{inputs['base']['task']}:{source['id']}"

    def node(ident, kind):
        return {
            "id": ident,
            "kind": kind,
            "label": ident,
            "status": "proposed",
            "sources": [ref],
            "assumption": "Proposed from cited source",
        }

    return {
        "assistant": (
            "Investigated the ticket and repository. Review the proposed CSV export system."
        ),
        "questions": [],
        "recommendations": [],
        "planning": [],
        "body": (
            "## CSV export\n\nPreserve existing exports.\n## Acceptance "
            "criteria\nAC-01: export accounts as CSV.\n"
        ),
        "graph": {
            "concrete": {
                "nodes": [node("user", "actor"), node("export", "component")],
                "edges": [
                    dict(node("requests", "relationship"), **{"from": "user", "to": "export"})
                ],
            }
        },
        "investigation": {"coverage": coverage, "decisions": decision_rows, "reads": []},
    }


class InvestigationModelFixture:
    """Builder browser fixture: deterministic no-tool synthesis with one genuine decision."""

    def __init__(self, host):
        self.host = host

    def run(self, inputs, **kwargs):
        return synthesis(inputs, decisions=True)
