"""#110: live repaints do not refetch the reliability lists unless their data changed.

The dashboard re-renders the run panels on every stream frame and explicit refresh. The
operation-incident, incident-intelligence, timeline and policy lists must load once per
query, again when the execution's reliability data changes (so a live run still shows new
incidents), and again on an explicit refresh, but not on repaints that change nothing they
show. Covers empty lists first, then non-empty ones.
"""

from contextlib import closing
from urllib.parse import quote

import pytest

from harness.db import connect
from harness.reliability import store
from harness.reliability.revision import reliability_revision
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


OPERATION_ROWS = "document.querySelectorAll('#operation-incidents .incident-item').length"


def test_a_result_dropped_by_a_tab_switch_is_loaded_again_on_return(
    observed,  # noqa: F811
    tmp_path,
):
    operation, principal, _ = observed
    task_id = operation.envelope["task_id"]
    url_tail = f"?task={quote(task_id)}&workspace=run&tab=implementation"
    with serve(operation.database, task_id) as server, chrome_page(tmp_path / "chrome") as page:
        page.open(server.url + url_tail, READY)
        page.settle(1.5)
        assert page.evaluate(OPERATION_ROWS) == 0
        # Hold operation-incident responses so the refetch is in flight during the switch.
        page.evaluate(
            """(() => {
                const original = window.fetch;
                window.fetch = async (input, options) => {
                    const response = await original(input, options);
                    if (String(input).includes('/api/execution/incidents?')) {
                        await new Promise((resolve) => setTimeout(resolve, 2500));
                    }
                    return response;
                };
                return true;
            })()"""
        )
        _record_incident(operation, principal)
        with closing(connect(operation.database)) as conn:
            final = reliability_revision(conn, operation.execution_id)
        # Recording takes two commits; wait until the page holds the final marker and its
        # (slowed) refetch is in flight, so no later frame can rescue a dropped result.
        page.wait_for(
            f"executionSnapshot?.reliability_revision === {final!r}"
            " && [...INCIDENT_VIEWS.values()].some((view) => view.loading)",
            timeout=15,
        )
        page.evaluate("document.getElementById('workspace-tab-checks').click(); true")
        page.settle(3.5)  # the slowed result arrives while the panel is unmounted
        page.evaluate("document.getElementById('workspace-tab-implementation').click(); true")
        page.wait_for(f"{OPERATION_ROWS} === 1", timeout=10)


def test_refreshes_keep_older_intelligence_pages_and_the_selection(
    observed,  # noqa: F811
    tmp_path,
):
    operation, principal, _ = observed
    task_id = operation.envelope["task_id"]
    for _ in range(30):
        _record_incident(operation, principal)
    url_tail = f"?task={quote(task_id)}&workspace=run&tab=implementation"
    items = "document.querySelectorAll('.ii-item').length"
    selected = "document.querySelectorAll('.ii-item [aria-pressed=\"true\"]').length"
    with serve(operation.database, task_id) as server, chrome_page(tmp_path / "chrome") as page:
        page.open(server.url + url_tail, f"{items} === 25")
        page.evaluate("document.getElementById('ii-list-older').click(); true")
        page.wait_for(f"{items} === 30")
        oldest = page.evaluate(
            "[...document.querySelectorAll('.ii-item')].at(-1).dataset.incidentId"
        )
        page.evaluate(f"document.getElementById('ii-select-{oldest}').click(); true")
        page.wait_for(f"{selected} === 1")

        # A new incident arrives: the window grows, older pages and the selection stay.
        _record_incident(operation, principal)
        page.wait_for(f"{items} === 31", timeout=15)
        page.settle(1.0)
        assert page.evaluate(selected) == 1
        assert page.evaluate(
            f"document.querySelector('.ii-item [aria-pressed=\"true\"]').closest('.ii-item')"
            f".dataset.incidentId === {oldest!r}"
        )

        # An explicit refresh keeps them too.
        page.evaluate("refreshDashboard().then(() => true)")
        page.settle(1.0)
        assert page.evaluate(items) == 31 and page.evaluate(selected) == 1


def test_a_refresh_of_a_full_window_skips_no_incident(
    observed,  # noqa: F811
    tmp_path,
):
    operation, principal, _ = observed
    task_id = operation.envelope["task_id"]
    for _ in range(101):
        _record_incident(operation, principal)
    url_tail = f"?task={quote(task_id)}&workspace=run&tab=implementation"
    items = "document.querySelectorAll('.ii-item').length"
    shown = "[...document.querySelectorAll('.ii-item')].map((item) => item.dataset.incidentId)"
    with serve(operation.database, task_id) as server, chrome_page(tmp_path / "chrome") as page:
        page.open(server.url + url_tail, f"{items} === 25")
        for expected in (50, 75, 100):
            page.evaluate("document.getElementById('ii-list-older').click(); true")
            page.wait_for(f"{items} === {expected}")
        seen = set(page.evaluate(shown))
        # A new incident arrives while the window is full: the window is kept whole and the
        # newer incident is announced, rather than trimming the oldest row behind the cursor.
        _record_incident(operation, principal)
        page.wait_for(
            "[...document.querySelectorAll('.ii-results p')].some((p) =>"
            " p.textContent.includes('Newer incidents are available'))",
            timeout=15,
        )
        assert set(page.evaluate(shown)) == seen
        page.evaluate("document.getElementById('ii-list-older').click(); true")
        page.wait_for(f"!document.getElementById('ii-list-older') && {items} === 100", timeout=15)
        seen |= set(page.evaluate(shown))
        assert len(seen) == 101  # every incident that existed before the refresh was shown
