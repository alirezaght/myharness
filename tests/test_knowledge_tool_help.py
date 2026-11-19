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
