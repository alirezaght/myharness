"""P2-04: diagram notation parsing, stable ids, merging, relevance and the content hash."""

import pytest

from harness.design_models import (
    Binding,
    DesignEntry,
    DesignSection,
    EntryKind,
    EntryOrigin,
    EntryStatus,
)
from harness.diagram_model import DIAGRAM_TYPES, USAGE, build_model, slug


def entry(
    entry_id: int,
    text: str,
    status: EntryStatus = EntryStatus.STATED,
    response: str | None = None,
    kind: EntryKind = EntryKind.ELEMENT,
) -> DesignEntry:
    return DesignEntry(
        id=entry_id,
        task_id="t-00000001",
        section=DesignSection.COMPONENTS,
        kind=kind,
        origin=EntryOrigin.DEVELOPER,
        binding=Binding.FIXED,
        statement=text,
        rationale=None,
        status=status,
        response=response,
        created_at="2026-01-01T00:00:00+00:00",
        resolved_at=None,
    )


def ids(items: list[dict]) -> list[str]:
    return [item["id"] for item in items]


def test_every_form_with_and_without_a_bullet():
    for bullet in ("", "- ", "* ", "  -\t"):
        text = "\n".join(
            bullet + line
            for line in (
                "component Api: entry point",
                "connect Api -> Store: writes",
                "message Api -> Store: save row",
                "flow Api -> Store: rows",
                "state Job: queued -> running: start",
                "deploy Store @ Db Host",
            )
        )
        model = build_model([entry(1, text)])
        data = model.data
        assert model.issues == ()
        assert ids(data["components"]) == ["component:api", "component:store"]
        assert data["components"][0]["descriptions"] == ["entry point"]
        assert data["components"][0]["declared"] and not data["components"][1]["declared"]
        assert ids(data["connections"]) == ["connection:api->store"]
        assert data["connections"][0]["labels"] == ["writes"]
        assert data["connections"][0]["from"] == "component:api"
        assert ids(data["messages"]) == ["message:api->store:1"]
        assert data["messages"][0]["text"] == "save row"
        assert ids(data["flows"]) == ["flow:api->store"]
        machine = data["machines"][0]
        assert machine["id"] == "machine:job"
        assert ids(machine["states"]) == ["state:job/queued", "state:job/running"]
        assert ids(machine["transitions"]) == ["transition:job/queued->running"]
        assert machine["transitions"][0]["events"] == ["start"]
        assert ids(data["nodes"]) == ["node:db-host"]
        assert data["deployments"] == [
            {
                "id": "deployment:store@db-host",
                "component": "component:store",
                "node": "node:db-host",
                "sources": ["E1"],
            }
        ]
        assert model.types == list(DIAGRAM_TYPES)


@pytest.mark.parametrize(
    ("line", "keyword"),
    [
        ("connect A -> B -> C", "connect"),
        ("connect A: x -> B", "connect"),
        ("connect A → B", "connect"),
        ("connect A", "connect"),
        ("message A -> B", "message"),
        ("message A -> B:   ", "message"),
        ("flow -> B", "flow"),
        ("state A -> B", "state"),
        ("state X -> Y: A -> B", "state"),
        ("state", "state"),
        ("deploy A", "deploy"),
        ("deploy A @ B @ C", "deploy"),
        ("component", "component"),
        ("component A -> B", "component"),
        ("component ---", "component"),
        ("  - connect the dots later  ", "connect"),
    ],
)
def test_a_malformed_notation_line_is_one_issue_and_adds_nothing(line, keyword):
    model = build_model([entry(7, f"prose first\n{line}\nprose after")])
    assert len(model.issues) == 1
    issue = model.issues[0]
    assert (issue.ref, issue.line, issue.text) == ("E7", 2, line.strip())
    assert issue.problem == f"{keyword}: expected {USAGE[keyword]}"
    assert model.empty and model.types == []
    assert all(model.data[key] == [] for key in model.data if key != "schema")


def test_prose_lines_are_ignored_without_issues():
    text = "\n".join(
        [
            "components are listed below",
            "stateful service",
            "state-machine driven",
            "Component X",
            "component: X",
            "Connect A -> B",
            "deployment @ host",
            "messages flow freely",
            "",
        ]
    )
    model = build_model([entry(1, text)])
    assert model.issues == () and model.empty


def test_edge_cases_of_the_forms():
    model = build_model(
        [
            entry(
                1,
                "component   X   Y  :\nconnect A -> B: retries: 3\nmessage A -> A: ping\n"
                "message A -> A: ping",
            )
        ]
    )
    data = model.data
    x = next(c for c in data["components"] if c["id"] == "component:x-y")
    assert (x["name"], x["descriptions"], x["declared"]) == ("X Y", [], True)
    assert data["connections"][0]["labels"] == ["retries: 3"]
    assert ids(data["messages"]) == ["message:a->a:1", "message:a->a:2"]
    assert data["messages"][0]["sources"] == ["E1"]
    assert model.issues == ()


def test_only_effective_design_elements_count_and_modified_uses_the_response():
    entries = [
        entry(1, "component Pending", EntryStatus.PENDING),
        entry(2, "component Rejected", EntryStatus.REJECTED),
        entry(3, "component Withdrawn", EntryStatus.WITHDRAWN),
        entry(4, "component NotApplicable", kind=EntryKind.NOT_APPLICABLE),
        entry(5, "component Original", EntryStatus.MODIFIED, "component Changed"),
        entry(6, "component Accepted", EntryStatus.ACCEPTED),
    ]
    data = build_model(entries).data
    assert ids(data["components"]) == ["component:accepted", "component:changed"]
    assert data["components"][1]["sources"] == ["E5"]


def test_undeclared_then_declared_takes_the_declared_spelling():
    model = build_model(
        [entry(2, "connect api gateway -> store"), entry(10, "component API_Gateway: entry")]
    )
    gateway = model.data["components"][0]
    assert gateway["id"] == "component:api-gateway"
    assert (gateway["name"], gateway["declared"], gateway["sources"]) == (
        "API_Gateway",
        True,
        ["E2", "E10"],
    )
    later = build_model([entry(1, "component API Gateway\ncomponent api-gateway")]).data
    assert later["components"][0]["name"] == "API Gateway"


def test_labels_and_sources_merge_first_seen_in_entry_order():
    entries = [
        entry(10, "connect A -> B: second\nconnect A -> B: first"),
        entry(2, "connect A -> B: first\nconnect A -> B: first"),
        entry(3, "component A: one\ncomponent A: one\ncomponent A: two"),
    ]
    data = build_model(entries).data
    connection = data["connections"][0]
    assert connection["labels"] == ["first", "second"]
    assert connection["sources"] == ["E2", "E10"]
    assert data["components"][0]["descriptions"] == ["one", "two"]
    assert data["components"][0]["sources"] == ["E2", "E3", "E10"]


def test_message_ordinals_count_per_pair_in_statement_order():
    text = "message A -> B: one\nmessage B -> A: back\nmessage A -> B: two\nmessage a -> b: three"
    data = build_model([entry(1, text)]).data
    assert ids(data["messages"]) == [
        "message:a->b:1",
        "message:b->a:1",
        "message:a->b:2",
        "message:a->b:3",
    ]
    assert [m["text"] for m in data["messages"]] == ["one", "back", "two", "three"]


def test_unicode_names_slug_and_merge():
    assert slug("API_Gateway") == slug("api gateway") == slug("API-Gateway") == "api-gateway"
    assert slug("Überprüfung Dienst") == "überprüfung-dienst"
    assert slug("数据 服务") == "数据-服务"
    data = build_model([entry(1, "connect Überprüfung -> 数据\ncomponent ÜBERPRÜFUNG")]).data
    assert ids(data["components"]) == ["component:überprüfung", "component:数据"]
    assert data["components"][0]["name"] == "ÜBERPRÜFUNG"
    for id_ in ids(data["components"]) + ids(data["connections"]):
        assert all(ch not in id_.split(":", 1)[1].replace("->", "") for ch in ":/@>")


def test_ids_and_hash_do_not_depend_on_line_order_or_prose():
    first = build_model(
        [
            entry(1, "component A\nconnect A -> B: x\nflow B -> C"),
            entry(2, "component B\nstate S: a -> b\ndeploy A @ N"),
        ]
    )
    second = build_model(
        [
            entry(2, "deploy A @ N\nprose\nstate S: a -> b\ncomponent B"),
            entry(1, "flow B -> C\nconnect A -> B: x\nsome prose\ncomponent A"),
        ]
    )
    assert first.canonical_json == second.canonical_json
    assert first.content_hash == second.content_hash
    assert first.content_hash.startswith("sha256:") and len(first.content_hash) == 71
    with_issue = build_model(
        [entry(1, "component A\nconnect A -> B: x\nflow B -> C\nconnect broken"), entry(2,
         "component B\nstate S: a -> b\ndeploy A @ N")]
    )  # fmt: skip
    assert len(with_issue.issues) == 1
    assert with_issue.content_hash == first.content_hash
    changed = build_model([entry(1, "component A\nconnect A -> B: y")])
    assert changed.content_hash != first.content_hash


def test_relevance_per_type_and_emptiness():
    cases = {
        "component A": ["component"],
        "connect A -> B": ["component"],
        "message A -> B: hi": ["sequence"],
        "flow A -> B": ["data_flow"],
        "state S: a -> b": ["state"],
        "deploy A @ N": ["deployment"],
        "just prose": [],
    }
    for text, types in cases.items():
        model = build_model([entry(1, text)])
        assert model.types == types, text
        assert model.empty is (types == [])
    assert build_model([]).empty and build_model([]).data["schema"] == 1
