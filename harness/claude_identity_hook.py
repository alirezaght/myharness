"""Pinned native hook: identity attestation only, never operation approval."""

import argparse
import hashlib
import hmac
import json
import secrets
import sys
import time
from pathlib import Path

from harness.execution_contract import canonical, digest, identity
from harness.execution_handoff import durable_file
from harness.native_tool_policy import ROLES

DOMAIN = b"harness-claude-identity-v1\0"


def signature(context, value):
    return hmac.new(
        bytes.fromhex(context["signing_key"]), DOMAIN + canonical(value).encode(), hashlib.sha256
    ).hexdigest()


def handle_hook(event, context):
    kind = event.get("hook_event_name")
    if kind == "Stop":
        return stop_hook(event, context)
    if kind in {"WorktreeCreate", "WorktreeRemove"}:
        raise ValueError("native worktree lifecycle is unavailable")
    if kind == "PreToolUse" and event.get("tool_name") in {"Agent", "Task"}:
        from harness.claude_agent_guard import guard_agent

        return guard_agent(event, context)
    if kind == "PreToolUse" and event.get("tool_name") in {"SendMessage", "TaskStop"}:
        from harness.claude_agent_guard import guard_message

        return guard_message(event, context)
    session = event.get("session_id")
    if session != context["session_id"]:
        raise ValueError("native Claude session does not match supervised launch")
    directory = Path(context["registry"])
    agent = event.get("agent_id")
    role = event.get("agent_type")
    observed = {
        "event": kind,
        "session_id": session,
        "agent_id": agent,
        "agent_type": role,
        "mcp_server": event.get("mcp_server"),
        "tool_name": event.get("tool_name"),
    }
    # Only native identity/provenance fields are retained; no input/transcript/auth payload.
    durable_file(
        directory / "observations" / (secrets.token_hex(12) + ".json"), canonical(observed).encode()
    )
    if kind == "SessionStart":
        durable_file(
            directory / "claude-root-staged.json", canonical({"session_id": session}).encode()
        )
        return {}
    if kind == "SubagentStart":
        identity(agent)
        if role not in ROLES:
            raise ValueError("unapproved Claude subagent role")
        durable_file(
            directory / ("claude-agent-" + agent + ".json"),
            canonical({"session_id": session, "agent_id": agent, "role": role}).encode(),
        )
        return {}
    if kind != "PreToolUse":
        raise ValueError("unsupported identity hook event")
    tool = event.get("tool_name")
    server = event.get("mcp_server")
    if not isinstance(server, dict) or not isinstance(tool, str):
        raise ValueError("native MCP provenance required")
    channel = context["channels"].get(server.get("name"))
    if not channel or server.get("source") != channel["source"]:
        raise ValueError("unapproved native MCP channel provenance")
    if not tool.startswith("mcp__" + server["name"] + "__"):
        raise ValueError("native MCP tool/channel mismatch")
    if agent:
        identity(agent)
        record = json.loads((directory / ("claude-agent-" + agent + ".json")).read_bytes())
        if record != {"session_id": session, "agent_id": agent, "role": role}:
            raise ValueError("native Claude child enrollment mismatch")
    else:
        agent, role = session, "orchestrator"
    if channel["role"] != role:
        raise ValueError("native Claude role/channel mismatch")
    arguments = event.get("tool_input")
    if not isinstance(arguments, dict):
        raise ValueError("object controller tool input required")
    public = {key: value for key, value in arguments.items() if key != "__harness_attestation"}
    now = time.time()
    envelope = {
        "version": 1,
        "execution_id": context["execution_id"],
        "epoch": context["epoch"],
        "session_id": session,
        "agent_id": agent,
        "role": role,
        "channel_id": server["name"],
        "tool": tool,
        "policy_digest": context["policy_digest"],
        "args_digest": digest(public),
        "nonce": secrets.token_hex(32),
        "issued_at": now,
        "expires_at": now + 30,
    }
    attestation = envelope | {"signature": signature(context, envelope)}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": public | {"__harness_attestation": attestation},
        }
    }


def stop_hook(event, context):
    """Use the existing attested read channel, never model prose, to decide to wait."""
    from urllib.parse import urlparse
    from urllib.request import ProxyHandler, Request, build_opener

    from harness.claude_agent_guard import require_root

    require_root(event, context)
    transport = context["stop_transport"]
    url = urlparse(transport["url"])
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.path != "/mcp"
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("stop observation requires the supervised loopback channel")
    headers = {
        "Authorization": "Bearer " + transport["token"],
        "Content-Type": "application/json",
    }
    opener = build_opener(ProxyHandler({}))

    def call(message):
        request = Request(transport["url"], canonical(message).encode(), headers=headers)
        with opener.open(request, timeout=3) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("stop observation exceeds bounded response")
            headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
        value = json.loads(raw)
        if value.get("error") or "result" not in value:
            raise ValueError("stop observation failed")
        return value["result"]

    call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    attested = handle_hook(
        {
            "hook_event_name": "PreToolUse",
            "session_id": context["session_id"],
            "tool_name": "mcp__harness__read_file",
            "mcp_server": {"name": "harness", "source": "dynamic"},
            "tool_input": {"path": "harness:pending-work"},
        },
        context,
    )["hookSpecificOutput"]["updatedInput"]
    result = call(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": attested},
        }
    )
    if result.get("isError"):
        raise ValueError("pending work could not be verified")
    work = json.loads(result["content"][0]["text"])
    pending = work["pending"]
    if not pending and not work["queued_setup"]:
        return {}
    own = [row["handle"] for row in pending if row["owner"] == context["session_id"]]
    reason = (
        "Harness still owns pending command work or uncollected results. "
        "Keep supervising this run; do not return a final response while work is pending. "
        "Poll your handles with poll_process until terminal results are fully collected; "
        "wait for child owners to collect theirs. Cancel only work that is no longer needed, "
        "then poll its terminal result. No additional execution authority is granted."
    )
    if own:
        reason += " Your handles: " + ", ".join(own)
    return {"decision": "block", "reason": reason}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    arguments = parser.parse_args()
    raw = sys.stdin.buffer.read(262145)
    if len(raw) > 262144:
        raise ValueError("native hook input exceeds limit")
    event = json.loads(raw)
    if event.get("hook_event_name") in {"WorktreeCreate", "WorktreeRemove"}:
        raise ValueError("native worktree lifecycle is unavailable")
    context = json.loads(Path(arguments.context).read_bytes())
    print(canonical(handle_hook(event, context)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError):
        print("Harness native identity attestation refused", file=sys.stderr)
        raise SystemExit(2) from None
