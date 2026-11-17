"""Only the observed complete native compatibility quartet corroborates canonical messages."""

import json

import pytest

from harness.claude_agent_guard import guard_message


@pytest.fixture
def channel(tmp_path):
    context = {"session_id": "root", "registry": str(tmp_path), "policy_digest": "policy"}
    (tmp_path / "root.json").write_text(json.dumps({"thread_id": "root", "policy": "policy"}))
    (tmp_path / "claude-agent-child.json").write_text(
        json.dumps({"session_id": "root", "agent_id": "child", "role": "al-engineer"})
    )
    return context


def event(value, tool="SendMessage"):
    return {"session_id": "root", "tool_name": tool, "tool_input": value}


def quartet(message):
    return {
        "to": "child",
        "message": message,
        "summary": "handoff",
        "type": "message",
        "recipient_kind": "agent",
        "recipient": "child",
        "content": message[:49] + "…",
    }


def test_canonical_and_exact_quartet_preserve_full_message(channel):
    message = "x" * 6935 + "TAIL"
    for value in ({"to": "child", "message": message}, quartet(message)):
        output = guard_message(event(value), channel)["hookSpecificOutput"]["updatedInput"]
        assert output["message"] == message


@pytest.mark.parametrize(
    "change",
    [
        {"recipient": "other"},
        {"recipient_kind": "team"},
        {"type": "broadcast"},
        {"content": "wrong"},
        {"content": False},
        {"recipient": None},
        {"unknown": "field"},
        {"message": "x" * 65537},
        {"summary": "x" * 201},
        {"to": 1},
        {"message": 1},
    ],
)
def test_conflicting_wrong_type_unknown_and_oversized_rejected(channel, change):
    with pytest.raises((ValueError, TypeError)):
        guard_message(event(quartet("x" * 1400) | change), channel)


@pytest.mark.parametrize("key", ["to", "message", "type", "recipient_kind", "recipient", "content"])
def test_partial_quartet_and_alias_only_rejected(channel, key):
    value = quartet("x" * 1400)
    del value[key]
    with pytest.raises(ValueError):
        guard_message(event(value), channel)


def test_short_aliases_foreign_child_and_child_sender_refused(channel, tmp_path):
    for value in (
        quartet("short"),
        {"to": "foreign", "message": "x"},
        {"to": "../child", "message": "x"},
    ):
        with pytest.raises((ValueError, OSError)):
            guard_message(event(value), channel)
    with pytest.raises(ValueError):
        guard_message(event({"to": "child", "message": "x"}) | {"agent_id": "child"}, channel)
    assert guard_message(event({"task_id": "child"}, "TaskStop"), channel)
    with pytest.raises(ValueError):
        guard_message(event({"task_id": "child", "message": "x"}, "TaskStop"), channel)
