"""Keep each of the 30 V1 requirements tied to a concrete test location."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _acceptance_rows(document: str) -> list[tuple[int, list[str]]]:
    """Each criterion row's number and every backticked test reference in its evidence."""
    rows = []
    for line in document.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].isdigit():
            rows.append((int(cells[0]), re.findall(r"`(tests/[^`]+)`", cells[2])))
    return rows


def _collectable_tests(path: Path) -> set[str]:
    """Module-level test functions pytest collects: final binding, not a fixture."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    final = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            fixture = any("fixture" in ast.unparse(d) for d in node.decorator_list)
            final[node.name] = not fixture
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    final[target.id] = False
    return {name for name, collectable in final.items() if collectable and name.startswith("test_")}


def test_acceptance_rows_read_every_reference_in_the_evidence_cell():
    document = (
        "| # | V1 criterion | Specific evidence | Status |\n"
        "|---:|---|---|---|\n"
        "| 1 | One | `tests/test_a.py::test_x` | passed |\n"
        "| 2 | Two | `tests/test_a.py::test_x` and `tests/test_b.py::test_y` | `tests/x` |\n"
    )
    assert _acceptance_rows(document) == [
        (1, ["tests/test_a.py::test_x"]),
        (2, ["tests/test_a.py::test_x", "tests/test_b.py::test_y"]),
    ]


def test_collectable_tests_follow_pytest_binding_rules(tmp_path):
    module = tmp_path / "test_sample.py"
    module.write_text(
        "import pytest\n"
        "def test_kept(): pass\n"
        "@pytest.fixture\ndef test_fixture(): pass\n"
        "def test_rebound(): pass\n"
        "test_rebound = None\n"
        "def helper(): pass\n",
        encoding="utf-8",
    )
    assert _collectable_tests(module) == {"test_kept"}


def test_v1_acceptance_map_references_resolve_to_collectable_tests():
    """Traceability only: every cited test exists as a collectable test function.

    This does not claim the tests passed or that acceptance was met; the map's Status column
    records that evidence separately.
    """
    rows = _acceptance_rows((ROOT / "docs/v1-acceptance.md").read_text(encoding="utf-8"))
    assert [number for number, _ in rows] == list(range(1, 31))
    for number, references in rows:
        assert references, f"criterion {number} cites no test"
        for reference in references:
            path, _, name = reference.partition("::")
            module = ROOT / path
            assert module.name.startswith("test_") and module.suffix == ".py", reference
            assert module.is_file(), reference
            assert name in _collectable_tests(module), reference


def test_delivery_completion_summary_keeps_browser_status_and_currency(
    controller, sample_repo, monkeypatch
):
    from harness.browser_validation import approve_browser, run_browser
    from harness.delivery_models import DeliveryConfig
    from harness.delivery_store import configure_delivery, delivery_view
    from tests.execution_helpers import approved_execution
    from tests.test_browser_validation import CountingDriver, prepared
    from tests.test_delivery_monitor import GitLab

    _, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    configure_delivery(
        controller,
        execution_id,
        DeliveryConfig(
            "https://gitlab.example",
            "group/project",
            "candidate",
            "main",
            "staging",
            publication_authorized=True,
            retry_limit=1,
        ),
    )
    monkeypatch.setattr("harness.delivery.publication_candidate", lambda *args: "a" * 40)
    monkeypatch.setattr("harness.delivery_monitor.publication_candidate", lambda *args: "a" * 40)
    api = GitLab()
    api.environment["external_url"] = "https://staging.example/"
    delivery = controller, execution_id, api
    controller, execution_id, api, plan = prepared(delivery, monkeypatch)
    approval = approve_browser(
        controller,
        execution_id,
        plan["plan_id"],
        request_id="completion-summary-browser-0001",
        attempt=1,
        client=api,
    )
    run_browser(
        controller,
        execution_id,
        approval["id"],
        client=api,
        driver_factory=CountingDriver,
    )
    view = delivery_view(controller.conn, execution_id)
    assert view["completion_summary"].get("browser") is not None
    assert view["completion_summary"]["browser"]["status"] == view["browser_validation"]["status"]
    assert (
        view["completion_summary"]["browser"]["currency"] == view["browser_validation"]["currency"]
    )
