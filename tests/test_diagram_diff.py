"""P2-06: diff_models — added, removed and changed elements between two stored diagram models."""

import pytest

from harness.diagram_diff import diff_models
from harness.diagram_model import build_model
from tests.test_diagram_model import entry


def _model(*texts: str) -> dict:
    """The model of one effective element entry per text."""
    return build_model([entry(n, text) for n, text in enumerate(texts, 1)]).data


def _changes(diff: dict) -> list[tuple[str, str, str]]:
    return [(c["change"], c["kind"], c["id"]) for c in diff["changes"]]


BASE = """component A: first
component B
connect A -> B: calls
flow A -> B: rows
state M: idle -> busy: start
deploy A @ host
message A -> B: hello"""


def test_identical_models_have_no_changes():
    diff = diff_models(_model(BASE), _model(BASE))
    assert diff == {
        "changes": [],
        "marks": {"from": {}, "to": {}},
        "counts": {"added": 0, "removed": 0, "changed": 0},
    }


def test_a_sources_only_change_is_no_change():
    old, new = _model(BASE), _model("component Z", BASE)
    new["components"] = [c for c in new["components"] if c["id"] != "component:z"]
    assert old != new and diff_models(old, new)["changes"] == []


@pytest.mark.parametrize(
    ("extra", "kind", "eid"),
    [
        ("component C", "component", "component:c"),
        ("connect B -> A", "connection", "connection:b->a"),
        ("flow B -> A", "flow", "flow:b->a"),
        ("state N: off -> on", "machine", "machine:n"),
        ("state M: busy -> idle", "transition", "transition:m/busy->idle"),
        ("deploy B @ cloud", "node", "node:cloud"),
        ("deploy B @ host", "deployment", "deployment:b@host"),
    ],
)
def test_every_kind_can_be_added_and_removed(extra, kind, eid):
    small, big = _model(BASE), _model(BASE + "\n" + extra)
    added = diff_models(small, big)
    assert ("added", kind, eid) in _changes(added) and added["marks"]["to"][eid] == "added"
    assert eid not in added["marks"]["from"]
    removed = diff_models(big, small)
    assert ("removed", kind, eid) in _changes(removed)
    assert removed["marks"]["from"][eid] == "removed" and eid not in removed["marks"]["to"]


@pytest.mark.parametrize(
    ("old", "new", "kind", "eid"),
    [
        ("component A: first", "component A: second", "component", "component:a"),
        ("connect A -> B: calls", "connect A -> B: sends", "connection", "connection:a->b"),
        ("flow A -> B: rows", "flow A -> B: cells", "flow", "flow:a->b"),
        (
            "state M: idle -> busy: start",
            "state M: idle -> busy: go",
            "transition",
            "transition:m/idle->busy",
        ),  # fmt: skip
        ("state M: idle -> busy: start", "state m: Idle -> busy: start", "state", "state:m/idle"),
    ],
)
def test_a_shown_attribute_change_is_changed_on_both_sides(old, new, kind, eid):
    diff = diff_models(_model(BASE), _model(BASE.replace(old, new)))
    assert ("changed", kind, eid) in _changes(diff)
    assert diff["marks"]["from"][eid] == diff["marks"]["to"][eid] == "changed"


def test_a_machine_and_node_rename_is_changed():
    old = _model("state M: a -> b\ndeploy A @ host")
    new = _model("state m: a -> b\ndeploy A @ Host")
    assert {("changed", "machine", "machine:m"), ("changed", "node", "node:host")} <= set(
        _changes(diff_models(old, new))
    )


def test_an_undeclared_component_becoming_declared_is_changed():
    old, new = _model("connect A -> B"), _model("connect A -> B\ncomponent B")
    assert _changes(diff_models(old, new)) == [("changed", "component", "component:b")]


def test_a_shifted_message_ordinal_is_not_marked():
    old = _model("message A -> B: one\nmessage A -> B: two")
    new = _model("message A -> B: two")
    diff = diff_models(old, new)
    assert _changes(diff) == [("removed", "message", "message:a->b:1")]
    assert diff["marks"] == {"from": {"message:a->b:1": "removed"}, "to": {}}


def test_a_message_text_change_is_removed_and_added_on_the_same_id():
    old, new = _model("message A -> B: hello"), _model("message A -> B: hi")
    diff = diff_models(old, new, {"message:a->b:1": "A -> B"}, {"message:a->b:1": "A -> B"})
    mid = "message:a->b:1"
    assert _changes(diff) == [("removed", "message", mid), ("added", "message", mid)]
    assert diff["marks"] == {"from": {mid: "removed"}, "to": {mid: "added"}}
    assert diff["counts"] == {"added": 1, "removed": 1, "changed": 0}


def test_repeated_messages_pair_as_a_multiset():
    old = _model("message A -> B: x\nmessage A -> B: x")
    new = _model("message A -> B: x\nmessage A -> B: y\nmessage A -> B: x")
    assert _changes(diff_models(old, new)) == [("added", "message", "message:a->b:2")]


def test_order_counts_and_labels():
    old = _model("component A\ncomponent B: one\ncomponent D")
    new = _model("component B: two\ncomponent C\ncomponent E")
    labels_old = {"component:a": "A", "component:b": "B old"}
    labels_new = {"component:b": "B", "component:c": "C"}
    diff = diff_models(old, new, labels_old, labels_new)
    assert [(c["change"], c["id"], c["label"]) for c in diff["changes"]] == [
        ("removed", "component:a", "A"),
        ("removed", "component:d", "component:d"),
        ("changed", "component:b", "B"),
        ("added", "component:c", "C"),
        ("added", "component:e", "component:e"),
    ]
    assert diff["counts"] == {"added": 2, "removed": 2, "changed": 1}


def test_missing_lists_count_as_empty():
    diff = diff_models({}, _model("component A"))
    assert _changes(diff) == [("added", "component", "component:a")]
    assert diff_models({}, {})["changes"] == []
