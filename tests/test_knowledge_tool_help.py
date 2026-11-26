"""Shared product help describes contracts without corpus recipes or authority changes."""

from harness.execution_mcp import SCHEMAS, TOOL_HELP, MCPConnection


def test_product_tool_help_and_schemas_are_truthful_and_generic():
    connection = MCPConnection(lambda _: {}, lambda *a: {})
    connection.request({"jsonrpc": "2.0", "method": "initialize"})
    tools = connection.request({"jsonrpc": "2.0", "method": "tools/list"})["tools"]
    for tool in tools:
        assert tool["inputSchema"] == SCHEMAS[tool["name"]]
        assert tool["description"] == TOOL_HELP[tool["name"]]
    assert "No result is not proof" in TOOL_HELP["knowledge_query"]
    assert "bundle_hash" in TOOL_HELP["knowledge_explain"]
    assert "expected_graph_revision" in TOOL_HELP["submit_output"]
    assert not any(word in str(TOOL_HELP) for word in ("demo/export", "preferred terms", "gold"))


def test_additive_context_help_keeps_request_schemas_closed():
    assert "registered_assignment" in TOOL_HELP["await_assignment"]
    assert "task_id" not in SCHEMAS["await_assignment"]["properties"]
    assert "receipt_id" in TOOL_HELP["knowledge_query"]
    assert "derivation" in TOOL_HELP["knowledge_explain"]
    assert "server-assigned" in TOOL_HELP["submit_output"]
    assert "source locators" in TOOL_HELP["submit_output"]
    assert set(SCHEMAS["knowledge_query"]["properties"]) == {"query", "entity_refs"}


def _connection():
    dispatched = []

    def dispatcher(principal, name, arguments):
        dispatched.append((principal, name, arguments))
        return {"dispatched": name}

    connection = MCPConnection(lambda meta: "operator-principal", dispatcher)
    connection.request({"jsonrpc": "2.0", "method": "initialize"})
    return connection, dispatched


def _call(connection, name, arguments):
    reply = connection.request(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    return reply["isError"], reply["content"][0]["text"]


def test_closed_tool_arguments_are_enforced_on_real_requests():
    connection, dispatched = _connection()
    accepted = {"query": "where are exports written?", "entity_refs": ["module:export"]}
    assert _call(connection, "knowledge_query", accepted) == (
        False,
        '{"dispatched": "knowledge_query"}',
    )
    assert _call(connection, "knowledge_query", {}) == (False, '{"dispatched": "knowledge_query"}')
    longest = {"query": "q" * 4096}
    assert _call(connection, "knowledge_query", longest) == (
        False,
        '{"dispatched": "knowledge_query"}',
    )
    # Authority is server-assigned: a caller cannot select the task, and no field outside the
    # published schema reaches the dispatcher.
    for name, arguments in (
        ("knowledge_query", {"query": "exports", "task_id": "t-other"}),
        ("knowledge_query", {"query": "exports", "receipt_id": "forged"}),
        ("await_assignment", {"task_id": "t-other"}),
        ("await_assignment", {"timeout": 5, "registered_assignment": "forged"}),
    ):
        assert _call(connection, name, arguments) == (True, "closed tool arguments required")
    for arguments, error in (
        ({"query": 7}, "bounded string required"),
        ({"query": "x" * 4097}, "bounded string required"),
        ({"entity_refs": "module:export"}, "bounded string array required"),
        ({"entity_refs": [7]}, "bounded string array required"),
    ):
        assert _call(connection, "knowledge_query", arguments) == (True, error)
    assert dispatched == [
        ("operator-principal", "knowledge_query", accepted),
        ("operator-principal", "knowledge_query", {}),
        ("operator-principal", "knowledge_query", longest),
    ]
