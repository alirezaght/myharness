"""#113: real MCP tools/call requests honour every bound each tool publishes in SCHEMAS.

The cases are generated from SCHEMAS itself, so a field added or tightened later with a bound
keyword the generator handles is covered without editing this file: each published minimum,
maximum, maxItems and maxLength (on strings, string items and artifact-reference properties)
is accepted at its edge and refused one step beyond it, before the request reaches dispatch.
A guard test fails if SCHEMAS starts using a keyword nobody enforces or generates.
"""

import json

import pytest

from harness.execution_mcp import SCHEMAS, MCPConnection

REFUSALS = {
    "integer": "bounded integer required",
    "string": "bounded string required",
    "array": "bounded string array required",
}


def _placeholder(spec):
    """A valid value for a required field, so a case varies only the field under test."""
    if "enum" in spec:
        return spec["enum"][0]
    kind = spec["type"]
    if kind == "integer":
        return spec.get("minimum", 0)
    return {"string": "x", "boolean": False, "array": [], "object": {}}[kind]


def _base(tool, field):
    schema = SCHEMAS[tool]
    return {
        name: _placeholder(schema["properties"][name])
        for name in schema["required"]
        if name != field
    }


def _cases():
    for tool, schema in sorted(SCHEMAS.items()):
        for field, spec in sorted(schema["properties"].items()):
            if spec["type"] == "integer":
                for bound, step in (("minimum", -1), ("maximum", 1)):
                    if bound in spec:
                        edge = spec[bound]
                        yield tool, field, f"{bound}={edge}", edge, edge + step
            elif spec["type"] == "array" and spec.get("items", {}).get("type") == "string":
                if "maxItems" in spec:
                    limit = spec["maxItems"]
                    yield tool, field, f"maxItems={limit}", ["v"] * limit, ["v"] * (limit + 1)
                if "maxLength" in spec["items"]:
                    limit = spec["items"]["maxLength"]
                    yield (
                        tool,
                        field,
                        f"items.maxLength={limit}",
                        ["v" * limit],
                        ["v" * (limit + 1)],
                    )
            elif spec["type"] == "string" and "maxLength" in spec:
                limit = spec["maxLength"]
                yield tool, field, f"maxLength={limit}", "v" * limit, "v" * (limit + 1)


CASES = list(_cases())


def _call(name, arguments):
    dispatched = []
    connection = MCPConnection(
        lambda meta: "principal",
        lambda principal, tool, args: dispatched.append((tool, args)) or {"ok": True},
    )
    connection.request({"jsonrpc": "2.0", "method": "initialize"})
    reply = connection.request(
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    return reply["isError"], reply["content"][0]["text"], dispatched


def test_the_generated_cases_cover_the_bounds_issue_113_reported():
    labels = {(tool, field, label) for tool, field, label, _, _ in CASES}
    assert ("knowledge_query", "entity_refs", "maxItems=40") in labels
    assert ("await_assignment", "timeout", "minimum=1") in labels
    assert ("await_assignment", "timeout", "maximum=30") in labels
    assert ("knowledge_consumers", "limit", "maximum=1000") in labels


@pytest.mark.parametrize(
    "tool,field,label,edge,beyond",
    CASES,
    ids=[f"{tool}.{field}:{label}" for tool, field, label, _, _ in CASES],
)
def test_published_bound_is_accepted_at_its_edge_and_refused_beyond_it(
    tool, field, label, edge, beyond
):
    arguments = _base(tool, field) | {field: edge}
    is_error, text, dispatched = _call(tool, arguments)
    assert (is_error, json.loads(text)) == (False, {"ok": True})
    assert dispatched == [(tool, arguments)]
    kind = SCHEMAS[tool]["properties"][field]["type"]
    is_error, text, dispatched = _call(tool, _base(tool, field) | {field: beyond})
    assert (is_error, text, dispatched) == (True, REFUSALS[kind], [])


def test_closed_fields_and_artifact_references_keep_their_rules():
    base = _base("al_command", "artifacts")
    reference = {"artifact_id": "artifact-1", "sha256": "sha256:" + "a" * 64}
    limit = SCHEMAS["al_command"]["properties"]["artifacts"]["maxItems"]
    assert _call("al_command", base | {"artifacts": [reference] * limit})[0] is False
    refused = (True, "closed execution artifact references required", [])
    assert _call("al_command", base | {"artifacts": [reference] * (limit + 1)}) == refused
    assert _call("al_command", base | {"artifacts": [{"artifact_id": "a"}]}) == refused
    assert _call("knowledge_query", {"task_id": "t-other"}) == (
        True,
        "closed tool arguments required",
        [],
    )


# Every keyword SCHEMAS uses, with how this module covers it. A new bound keyword (pattern,
# exclusiveMinimum, minItems, ...) fails the guard until the validator enforces it and a case
# generator above exercises it.
STRUCTURE = {"type", "description", "properties", "required", "additionalProperties", "items"}
GENERATED = {"minimum", "maximum", "maxItems", "maxLength", "enum"}


def _keywords(spec):
    found = set(spec) - STRUCTURE
    for child in (spec.get("properties") or {}).values():
        found |= _keywords(child)
    if isinstance(spec.get("items"), dict):
        found |= _keywords(spec["items"])
    return found


def test_every_bound_keyword_in_schemas_is_enforced_and_generated():
    used = set().union(*(_keywords(schema) for schema in SCHEMAS.values()))
    assert used <= GENERATED, sorted(used - GENERATED)


@pytest.mark.parametrize("field", ["artifact_id", "sha256"])
def test_artifact_reference_properties_honour_their_published_length(field):
    base = _base("al_command", "artifacts")
    limit = SCHEMAS["al_command"]["properties"]["artifacts"]["items"]["properties"][field][
        "maxLength"
    ]
    reference = {"artifact_id": "artifact-1", "sha256": "sha256:" + "a" * 64}
    assert _call("al_command", base | {"artifacts": [reference | {field: "v" * limit}]})[0] is False
    assert _call("al_command", base | {"artifacts": [reference | {field: "v" * (limit + 1)}]}) == (
        True,
        "closed execution artifact references required",
        [],
    )


def test_minimum_length_and_item_keywords_are_enforced_when_published(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 10, "minLength": 2},
            "entity_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "string", "minLength": 3, "maxLength": 5},
            },
        },
        "required": [],
        "additionalProperties": False,
    }
    monkeypatch.setitem(SCHEMAS, "knowledge_query", schema)
    assert _call("knowledge_query", {"query": "ab", "entity_refs": ["abc"]})[0] is False
    assert _call("knowledge_query", {"query": "a"})[1] == "bounded string required"
    assert _call("knowledge_query", {"entity_refs": []})[1] == "bounded string array required"
    assert _call("knowledge_query", {"entity_refs": ["ab"]})[1] == "bounded string array required"
