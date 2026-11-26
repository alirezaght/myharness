"""Pure ordering and presentation contracts, independent of evaluation answers."""

from harness.knowledge.context_ranking import guidance_scope, presentation, rank_key
from harness.knowledge.models import ContextRequest


def row(aid, **changes):
    return {
        "assertion_id": aid,
        "subject": "module:service",
        "object": None,
        "predicate": "defines",
        "text": "service",
        "verification": "source-backed",
        "value": "service",
        "applicability": {},
        **changes,
    }


def test_exact_source_priority_and_test_objective_are_explainable():
    request = ContextRequest(
        "service",
        stage="verification",
        objective="find regression tests",
        affected_entities=("file:service.py",),
    )
    assert rank_key(row("z"), request, ["service.py"]) < rank_key(row("a"), request, ["other.py"])
    assert rank_key(row("z"), request, ["tests/test_service.py"]) < rank_key(
        row("a"), request, ["other.py"]
    )
    assert guidance_scope("AGENTS.md", request)
    assert not guidance_scope("unrelated/AGENTS.md", request)


def test_presentation_keeps_conflicts_scopes_and_original_provenance():
    request = ContextRequest(stage="implementation", affected_entities=("file:parser/api.py",))
    facts = [
        row("a", predicate="described-by", text="Run relevant checks"),
        row("b", predicate="described-by", text="Run relevant checks"),
        row("c", predicate="described-by", text="Different scoped instruction"),
    ]
    payload = {
        "assertions": facts,
        "citations": [
            {"assertion_id": "a", "locator": "AGENTS.md"},
            {"assertion_id": "b", "locator": "parser/AGENTS.md"},
            {"assertion_id": "c", "locator": "parser/CLAUDE.md"},
        ],
    }
    view = presentation(payload, request)
    assert len(view["guidance_presentation"]) == 2
    identical = next(group for group in view["guidance_presentation"] if len(group["members"]) == 2)
    assert {member["assertion_id"] for member in identical["members"]} == {"a", "b"}
    assert view["uncertainty"] and view["presentation_only"]
    assert payload["assertions"] == facts and len(payload["citations"]) == 3
    assert all(item["approval_reference"] is None for item in view["selection"])


def test_recorded_approval_remains_distinct_from_effective_or_historical_design():
    request = ContextRequest(stage="implementation")
    facts = [
        row("approved", subject="decision:DEC-0001:v1", predicate="status", value="approved"),
        row("effective", subject="design:entry-1", predicate="status", value="accepted"),
        row(
            "old",
            subject="decision:DEC-0002:v1",
            predicate="status",
            value="approved",
            historical=True,
        ),
    ]
    selected = presentation({"assertions": facts, "citations": []}, request)["selection"]
    assert selected[0]["approval_reference"] == {
        "assertion_id": "approved",
        "state": "recorded-approved",
    }
    assert selected[1]["approval_reference"] is None
    assert selected[2]["approval_reference"] is None


def test_requested_definition_and_test_support_survive_stage_ranking():
    from harness.knowledge.context_ranking import ranked_candidates

    request = ContextRequest(
        "stale citation",
        stage="verification",
        objective="find tests",
        affected_entities=("file:service.py",),
    )
    facts = {
        "test1": row("test1", text="stale citation tests"),
        "test2": row("test2", text="stale tests"),
        "test3": row("test3", text="citation tests"),
        "support": row("support", text="stale citation validation"),
        "irrelevant": row("irrelevant", text="unrelated helper"),
    }
    located = {aid: ["tests/test_service.py"] for aid in ("test1", "test2", "test3")}
    located.update(support=["service.py"], irrelevant=["service.py"])
    order = ranked_candidates(facts, request, located)
    assert order[:3] == ["test1", "test2", "support"]
    assert order.index("support") < order.index("irrelevant")
    facts["explicit"] = row(
        "explicit", text="resolve", _resolved_reference=True, _reference_definition=True
    )
    assert ranked_candidates(facts, request, located)[0] == "explicit"
