"""#34: the `knowledge_consumers` tool of an execution asks that execution's own AL run."""

import subprocess
from pathlib import Path

from harness.knowledge.policy import operator_scope
from harness.knowledge.refresh import refresh
from tests.al_facts_support import runner
from tests.test_execution_operations import operations as operations


def test_consumers_tool_queries_the_execution_al_run(operations, tmp_path):
    operation, supervisor, controller, _, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "service.py").write_text("def run():\n    return 1\n")
    (root / "planning.py").write_text("import service\n")
    subprocess.run(["git", "-C", str(root), "add", "service.py", "planning.py"], check=True)
    refresh(controller.conn, operator_scope(root))
    calls, built = [], []
    original = operation.al_facts

    def al_facts():
        facts = original()
        built.append(facts)
        facts.runner = runner(tmp_path / "al-index", calls)  # this checkout's AL, no launcher
        return facts

    operation.al_facts = al_facts
    planner = supervisor | {"thread_id": "planning-scout", "role": "al-scout", "writable": False}
    result = operation.dispatch(planner, "knowledge_consumers", {"target": "service.py"})
    assert result["source"] == "al-facts" and result["status"] == "complete"
    assert [(c["locator"], c["relation"]) for c in result["consumers"]] == [
        ("planning.py", "imports")
    ]
    (facts,) = built
    assert (facts.repository, facts.platform, facts.session) == (
        str(root),
        operation.envelope["host"],
        operation.envelope["al_session_id"],
    )
    # The plain `run` form the run's resident AL service serves warm.
    assert calls == [
        [
            str(operation.package["launcher"]),
            "run",
            "--repository",
            str(root),
            "--platform",
            operation.envelope["host"],
            "--session",
            operation.envelope["al_session_id"],
            "--",
            "facts",
            "consumers",
            "service.py",
            "--limit",
            "1000",
        ]
    ]
