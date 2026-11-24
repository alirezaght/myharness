"""Step 5's approved test plan as cacheable AL check recipes (#31).

Each test category with approved scenarios becomes one AL evidence area. Its recipe is the
exact `evidence plan` invocation without the repository-specific part (cwd, scope and the
command), which the orchestrator fills in from the repository it inspected. Hermetic
categories are planned without `--non-hermetic`, so AL's action cache (#26) keys them by
their inputs: a check that already ran on identical inputs is reused, here and in any other
run or worktree of the repository. End-to-end scenarios reach real services unless every
one of them is mocked, so they are planned non-hermetic and always run.
"""

from harness.testplan_models import TestCategory

AREA_PREFIX = "testplan-"
RECIPES = {
    TestCategory.UNIT: ("test", "focused"),
    TestCategory.REGRESSION: ("test", "focused"),
    TestCategory.EDGE_CASE: ("test", "focused"),
    TestCategory.FAILURE: ("test", "focused"),
    TestCategory.INTEGRATION: ("test", "broad"),
    TestCategory.E2E: ("test", "broad"),
}


def check_recipes(plan):
    """Recipes for a plan document dict (`TestPlanDocument.to_dict()`), in category order."""
    recipes = []
    for category in TestCategory:
        scenarios = (plan.get("categories") or {}).get(str(category)) or []
        if not scenarios:
            continue
        kind, tier = RECIPES[category]
        hermetic = category is not TestCategory.E2E or all(s.get("mock") for s in scenarios)
        area = AREA_PREFIX + str(category).replace("_", "-")
        argv = ["evidence", "plan", "--area", area, "--kind", kind, "--tier", tier]
        if not hermetic:
            argv.append("--non-hermetic")
        recipes.append(
            {
                "area": area,
                "category": str(category),
                "kind": kind,
                "tier": tier,
                "hermetic": hermetic,
                "cacheable": hermetic,
                "scenarios": [s["ref"] for s in scenarios],
                "plan_argv": argv,
                "fill": ["--cwd", "--scope", "--", "<command>"],
            }
        )
    return recipes


def render(recipes):
    if not recipes:
        return "No approved test scenarios map to check recipes."
    lines = ["Approved test plan as AL check recipes (plan each with its cwd, scope and command):"]
    for r in recipes:
        lines.append(
            "- "
            + " ".join(r["plan_argv"])
            + " --cwd <dir> --scope <paths> -- <command>  # "
            + ", ".join(r["scenarios"])
            + ("; cacheable" if r["cacheable"] else "; non-hermetic, always runs")
        )
    return "\n".join(lines)
