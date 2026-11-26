"""Keep each of the 30 V1 requirements tied to a concrete test location."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _acceptance_rows() -> list[tuple[int, list[str]]]:
    """Each criterion row's number and every backticked test reference in its evidence."""
    document = (ROOT / "docs/v1-acceptance.md").read_text(encoding="utf-8")
    rows = []
    for line in document.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].isdigit():
            rows.append((int(cells[0]), re.findall(r"`(tests/[^`]+)`", cells[2])))
    return rows


def _collectable_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }


def test_v1_acceptance_map_references_resolve_to_collectable_tests():
    """Traceability only: every cited test exists as a collectable test function.

    This does not claim the tests passed or that acceptance was met; the map's Status column
    records that evidence separately.
    """
    rows = _acceptance_rows()
    assert [number for number, _ in rows] == list(range(1, 31))
    for number, references in rows:
        assert references, f"criterion {number} cites no test"
        for reference in references:
            path, _, name = reference.partition("::")
            module = ROOT / path
            assert module.name.startswith("test_") and module.suffix == ".py", reference
            assert module.is_file(), reference
            assert name in _collectable_tests(module), reference
    # Criterion 25 cites two tests; both are checked, not only the first.
    assert len(dict(rows)[25]) == 2


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
