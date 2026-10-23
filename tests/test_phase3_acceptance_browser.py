"""Browser proof that approval success and native launch failure stay distinct."""

import json

import pytest

from harness.dashboard.server import DashboardServer
from tests.complete_task import settle
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _driven_dom

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
]


@pytest.fixture(autouse=True)
def short_streams(monkeypatch):
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)


DRIVER = r"""
function waitFor(predicate) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const poll = () => {
      const value = predicate();
      if (value) resolve(value);
      else if (Date.now() - start > 8000) reject(new Error("approval result timed out"));
      else setTimeout(poll, 40);
    };
    poll();
  });
}
document.addEventListener("DOMContentLoaded", async () => {
  const button = await waitFor(() => document.getElementById("approve-button"));
  button.click();
  const status = await waitFor(() => document.getElementById("approval-launch-status"));
  const retry = [...document.querySelectorAll("#approvals button")]
    .find((button) => button.textContent === "Retry AL launch");
  const node = document.createElement("pre");
  node.id = "phase3-browser-result";
  node.textContent = JSON.stringify({
    approvalRecorded: status?.textContent.includes("Approval recorded."),
    launchUnavailable: status?.textContent.includes("AL launch: Unavailable"),
    retryVisible: Boolean(retry),
    output: document.querySelector("#approve-output")?.textContent || ""
  });
  document.body.append(node);
});
"""


def test_approval_recorded_remains_visible_when_launch_fails(
    controller, sample_repo, monkeypatch, db_path, tmp_path
):
    import html
    import re

    monkeypatch.chdir(sample_repo)
    task_id = controller.create_task(
        "Approve a bounded isolated Phase 3 browser fixture", "trivial"
    ).id
    settle(controller, task_id)
    controller.advance(task_id)
    import shutil
    from importlib.resources import as_file

    from harness.dashboard import server as server_module

    copied = tmp_path / "static"
    with as_file(server_module.STATIC_DIR) as real:
        shutil.copytree(real, copied)
    driver = copied / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    page = copied / "index.html"
    source = page.read_text(encoding="utf-8")
    tag = '<script src="/static/app.js" defer></script>'
    assert source.count(tag) == 1
    page.write_text(
        source.replace(tag, f'{tag}\n    <script src="/static/driver.js" defer></script>'),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    monkeypatch.setattr(
        server_module,
        "STATIC",
        server_module.STATIC
        | {"/static/driver.js": ("driver.js", "text/javascript; charset=utf-8")},
    )
    monkeypatch.setattr(
        "harness.execution.start_execution",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("native CLI unavailable in controlled browser fixture")
        ),
    )

    with serve(db_path, task_id) as server:
        dom = _driven_dom(server, tmp_path / "profile", 1280, 800)
    match = re.search(r'<pre id="phase3-browser-result">(.*?)</pre>', dom, re.S)
    assert match, dom[-2000:]
    observed = json.loads(html.unescape(match.group(1)))
    assert observed["approvalRecorded"] is True
    assert observed["launchUnavailable"] is True
    assert observed["retryVisible"] is True
    assert "approval #" in observed["output"].lower()
