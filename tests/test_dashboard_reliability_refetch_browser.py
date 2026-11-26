"""#110: live repaints do not refetch the reliability lists unless their data changed.

The dashboard re-renders the run panels on every stream frame and explicit refresh. The
operation-incident, incident-intelligence, timeline and policy lists must load once per
query, again when the execution's reliability data changes (so a live run still shows new
incidents), and again on an explicit refresh, but not on repaints that change nothing they
show. Covers empty lists first, then non-empty ones.
"""

from urllib.parse import quote

import pytest

from harness.reliability import store
from tests.chrome_cdp import chrome_page
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_reliability_store import observed  # noqa: F401

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
]

LISTS = (
    "/api/execution/incidents?",
    "/api/reliability/incidents?",
    "/api/execution/timeline?",
    "/api/execution/policy?",
)
READY = (
    "Boolean(document.querySelector('#operation-incidents .incident-content'))"
    " && Boolean(document.querySelector('#execution-timeline'))"
    " && Boolean(document.querySelector('#execution-policy'))"
)
INCIDENT_ROWS = "document.querySelectorAll('#operation-incidents [data-incident-id]').length"


def _reads(page) -> dict[str, int]:
    return {route: sum(route in r["url"] for r in page.requests) for route in LISTS}


def _repaint(page, task_id: str, times: int = 6) -> None:
    """Re-render the dashboard as stream frames do, with the current snapshot."""
    page.evaluate(
        f"""(async () => {{
            const data = await (await fetch('/api/dashboard?task={task_id}',
                {{cache: 'no-store'}})).json();
            for (let i = 0; i < {times}; i += 1) {{
                render(structuredClone(data));
                await new Promise((resolve) => setTimeout(resolve, 60));
            }}
            return true;
        }})()"""
    )
    page.settle(1.0)


def _record_incident(operation, principal) -> None:
    observation = store.begin(operation, principal, "read_file")
    store.finish(operation, observation, "DISPATCH_REJECTED")


def test_repaints_reuse_reliability_lists_until_their_data_or_a_refresh_changes(
    observed,  # noqa: F811
    tmp_path,
):
    operation, principal, _ = observed
    task_id = operation.envelope["task_id"]
    url_tail = f"?task={quote(task_id)}&workspace=run&tab=implementation"
    with serve(operation.database, task_id) as server, chrome_page(tmp_path / "chrome") as page:
        page.open(server.url + url_tail, READY)
        page.settle(2.0)
        loaded = _reads(page)
        assert all(1 <= count <= 2 for count in loaded.values()), loaded
        assert page.evaluate(INCIDENT_ROWS) == 0  # empty lists

        # Repaints that change nothing the panels show issue no list reads (empty lists).
        _repaint(page, task_id)
        assert _reads(page) == loaded

        # New reliability data reaches the open page through the stream and refetches the
        # lists, so a live run still shows the new incident.
        _record_incident(operation, principal)
        page.wait_for(f"{INCIDENT_ROWS} === 1", timeout=15)
        page.settle(1.5)
        changed = _reads(page)
        assert all(1 <= changed[r] - loaded[r] <= 2 for r in LISTS), (loaded, changed)

        # Repaints with non-empty lists also reuse them.
        _repaint(page, task_id)
        assert _reads(page) == changed

        # An explicit refresh refetches every list once.
        page.evaluate("refreshDashboard().then(() => true)")
        page.settle(1.0)
        refreshed = _reads(page)
        assert all(refreshed[r] - changed[r] == 1 for r in LISTS), (changed, refreshed)
        assert page.evaluate(INCIDENT_ROWS) == 1
