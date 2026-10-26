"""Closed native Agent lifecycle input; no worktree/model/role override surface."""

import json
from pathlib import Path

from harness.native_tool_policy import ROLES


def require_root(event, context):
    if event.get("agent_id") or event.get("agent_type"):
        raise ValueError("only the supervised native root may schedule approved AL agents")
    if event.get("session_id") != context["session_id"]:
        raise ValueError("native Agent belongs to another session")
    root = json.loads((Path(context["registry"]) / "root.json").read_bytes())
    if (
        root.get("thread_id") != context["session_id"]
        or root.get("policy") != context["policy_digest"]
    ):
        raise ValueError("supervised native root has not been corroborated")


def guard_agent(event, context):
    require_root(event, context)
    if event.get("tool_name") != "Agent":
        raise ValueError("unsupported native scheduling tool")
    value = event.get("tool_input")
    import secrets

    from harness.execution_contract import canonical
    from harness.execution_handoff import durable_file

    durable_file(
        Path(context["registry"])
        / "observations"
        / ("agent-input-" + secrets.token_hex(12) + ".json"),
        canonical(
            {"event": "Agent-input", "keys": sorted(value) if isinstance(value, dict) else None}
        ).encode(),
    )
    required = {"description", "prompt", "subagent_type"}
    if (
        not isinstance(value, dict)
        or set(value) - required - {"run_in_background"}
        or not required <= value.keys()
    ):
        raise ValueError("native Agent has unsupported or missing arguments")
    if value["subagent_type"] not in ROLES:
        raise ValueError("only exact protected AL roles are admitted")
    for key, limit in (("description", 256), ("prompt", 65536)):
        if (
            not isinstance(value[key], str)
            or not value[key].strip()
            or len(value[key].encode()) > limit
        ):
            raise ValueError("native Agent text exceeds its closed bound")
    if "run_in_background" in value and value["run_in_background"] is not True:
        raise ValueError("native AL agents must use local background scheduling")
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {**value, "run_in_background": True},
        }
    }


def guard_message(event, context):
    """Only captured local child IDs are addresses; names and cross-session sends are refused."""
    require_root(event, context)
    value, tool = event.get("tool_input"), event.get("tool_name")
    if not isinstance(value, dict):
        raise ValueError("native lifecycle object required")
    if tool == "SendMessage":
        if set(value) - {"to", "message", "summary"} or not {"to", "message"} <= value.keys():
            raise ValueError("closed owned-child message required")
        agent = value["to"]
        for key, limit in (("message", 65536), ("summary", 200)):
            if key in value and (not isinstance(value[key], str) or len(value[key]) > limit):
                raise ValueError("bounded native lifecycle message required")
    elif tool == "TaskStop":
        if set(value) != {"task_id"}:
            raise ValueError("only an owned native child task may be stopped")
        agent = value["task_id"]
    else:
        raise ValueError("unsupported native lifecycle tool")
    from harness.execution_contract import identity

    identity(agent)
    row = json.loads((Path(context["registry"]) / ("claude-agent-" + agent + ".json")).read_bytes())
    if row["session_id"] != context["session_id"] or row["agent_id"] != agent:
        raise ValueError("native lifecycle target is outside this execution")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": value}}


def required_hooks(command, arguments):
    hook = {
        "type": "command",
        "command": command,
        "args": list(arguments),
        "timeout": 10,
        "onFailure": "block",
    }
    return {
        "SessionStart": [{"hooks": [dict(hook)]}],
        "SubagentStart": [{"hooks": [dict(hook)]}],
        "PreToolUse": [
            {"matcher": "^(Agent|Task|SendMessage|TaskStop)$", "hooks": [dict(hook)]},
            {"matcher": "^mcp__harness(?:_[A-Za-z0-9_]+)?__.*$", "hooks": [dict(hook)]},
        ],
        "WorktreeCreate": [{"hooks": [dict(hook)]}],
        "WorktreeRemove": [{"hooks": [dict(hook)]}],
    }
