"""Step 5's approved test plan maps onto cacheable AL check recipes (#31)."""

from harness.testplan_checks import check_recipes, render


def scenario(ref, mock=None):
    return {"id": int(ref[1:]), "ref": ref, "text": "t", "covers": ["Q1"], "mock": mock}


PLAN = {
    "categories": {
        "unit": [scenario("S1"), scenario("S2")],
        "integration": [scenario("S3")],
        "e2e": [scenario("S4")],
        "regression": [],
        "edge_case": [scenario("S5")],
        "failure": [],
    }
}


def test_each_approved_category_is_one_evidence_area():
    recipes = {r["category"]: r for r in check_recipes(PLAN)}
    assert list(recipes) == ["unit", "integration", "e2e", "edge_case"]
    unit = recipes["unit"]
    assert unit["plan_argv"] == ["evidence", "plan", "--area", "testplan-unit", "--kind", "test", "--tier", "focused"]
    assert unit["scenarios"] == ["S1", "S2"] and unit["cacheable"] is True
    assert recipes["integration"]["tier"] == "broad" and recipes["integration"]["cacheable"]
    assert recipes["edge_case"]["area"] == "testplan-edge-case"


def test_end_to_end_reaching_real_services_is_not_cached():
    e2e = next(r for r in check_recipes(PLAN) if r["category"] == "e2e")
    assert e2e["hermetic"] is False and e2e["cacheable"] is False and "--non-hermetic" in e2e["plan_argv"]
    mocked = {"categories": {"e2e": [scenario("S9", mock="payment API")]}}
    assert check_recipes(mocked)[0]["cacheable"] is True


def test_render_names_every_scenario_and_its_cacheability():
    text = render(check_recipes(PLAN))
    assert "evidence plan --area testplan-unit --kind test --tier focused --cwd <dir>" in text
    assert "S1, S2; cacheable" in text and "S4; non-hermetic, always runs" in text
    assert render([]) == "No approved test scenarios map to check recipes."


def test_execution_handoff_carries_the_recipes(controller, sample_repo, monkeypatch):
    from harness.execution_handoff import read_handoff
    from harness.execution_store import get_execution
    from harness.testplan import TestPlanner
    from tests.execution_helpers import approved_execution

    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    assert envelope["check_recipes"] == check_recipes(TestPlanner(controller, tid).document().to_dict())
