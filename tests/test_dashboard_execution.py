import http.client
import json
import threading

from harness.dashboard.server import DashboardServer
from harness.execution_logs import append_log
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution


def test_execution_get_does_not_launch_and_logs_are_scoped(
    controller, sample_repo, monkeypatch, db_path
):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    log = append_log(controller.conn, eid, "worker", "api_key=private diagnostics")
    server = DashboardServer(db_path, tid)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = http.client.HTTPConnection("127.0.0.1", server.port)
        client.request("GET", f"/api/execution?task={tid}&execution={eid}")
        response = client.getresponse()
        data = json.loads(response.read())
        assert response.status == 200 and data["id"] == eid
        client.request("GET", f"/api/execution/logs?task={tid}&execution={eid}&log={log}")
        response = client.getresponse()
        body = response.read()
        assert response.status == 200 and b"private" not in body and b"redacted" in body
        client.request(
            "POST",
            "/api/execution/reconnect",
            body="{}",
            headers={"Content-Type": "application/json", "Origin": "http://foreign.invalid"},
        )
        response = client.getresponse()
        response.read()
        assert response.status == 403
        client.request(
            "POST",
            "/api/execution/reconnect",
            body="{bad json",
            headers={"Content-Type": "application/json", "Origin": server.url.rstrip("/")},
        )
        response = client.getresponse()
        response.read()
        assert response.status == 400
        client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert get_execution(controller.conn, eid)["status"] == "Reserved"
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 0


def test_explicit_recovery_routes_require_epoch_and_reject_prompt_injection(
    controller, sample_repo, monkeypatch, db_path
):
    from tests.test_dashboard import serve

    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    current = get_execution(controller.conn, eid)
    requested = []
    monkeypatch.setattr("harness.dashboard.execution_view.require_ready", lambda *a: None)

    def recover(controller, execution_id, **kwargs):
        requested.append((execution_id, kwargs))
        return {"status": "WorkerOwned"}

    monkeypatch.setattr("harness.execution_controls.recover_and_continue", recover)
    with serve(db_path, tid) as server:
        for action in ("recover", "retry-checks"):
            body = {"task": tid, "execution": eid}
            epoch = {
                "epoch_id": current["epoch_id"],
                "authority_generation": current["authority_generation"],
            }
            for value, status in (
                (body, 400),
                (body | epoch | {"prompt": "waive proof"}, 400),
                (body | epoch, 200),
            ):
                client = http.client.HTTPConnection("127.0.0.1", server.port)
                client.request(
                    "POST",
                    f"/api/execution/{action}",
                    json.dumps(value),
                    {"Content-Type": "application/json", "Origin": server.url.rstrip("/")},
                )
                response = client.getresponse()
                assert response.status == status, response.read()
                response.read()
                client.close()
    assert requested == [
        (
            eid,
            {
                "action": action,
                "expected_epoch_id": current["epoch_id"],
                "expected_authority_generation": current["authority_generation"],
            },
        )
        for action in ("recover", "retry-checks")
    ]


def test_implementation_label_never_promotes_unknown_readiness():
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("local Node unavailable")
    source = (
        Path(__file__).resolve().parents[1] / "harness/dashboard/static/execution.js"
    ).read_text()
    function = source[
        source.index("function implementationLabel(") : source.index("function executionDraftKey(")
    ]
    script = (
        function
        + """
const assert = require('node:assert/strict');
for (const execution of [{}, {implementation:{}}, {implementation:{status:'unknown'}},
                         {implementation:{status:'unverified'}}]) {
  assert.equal(implementationLabel(execution), 'Implementation unverified');
}
assert.equal(implementationLabel({implementation:{status:'ready'}}), 'Implementation ready');
assert.equal(implementationLabel({implementation:{status:'technically_unavailable'}}),
             'Implementation unavailable');
assert.equal(implementationLabel({implementation:{status:'complete'}}), 'Implementation complete');
"""
    )
    subprocess.run([node, "-e", script], check=True, capture_output=True, timeout=10)


def test_verification_renderer_text_tracks_observed_admission():
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("local Node unavailable")
    source = (
        Path(__file__).resolve().parents[1] / "harness/dashboard/static/execution.js"
    ).read_text()
    functions = source[
        source.index("function implementationLabel(") : source.index("function executionDraftKey(")
    ]
    functions += source[
        source.index("function renderVerificationState(") : source.index("function renderProgress(")
    ]
    script = (
        functions
        + """
const assert = require('node:assert/strict');
function el(tag, text='') {
  return {tag, textContent:text, children:[], dataset:{},
          append(...nodes) { this.children.push(...nodes); }};
}
const document = {createElement: el};
function text(node) {
  return [node.textContent, ...node.children.map(text)].join(' ');
}
function render(status) {
  return text(renderVerificationState({implementation:{status},
    verification:{status:'incomplete', obligation_refs:['coverage remains pending']}}));
}
for (const status of ['ready', 'continuing']) {
  const output = render(status);
  assert.ok(output.includes('Implementation can continue within its authorized scope.'));
  assert.ok(output.includes('Verification incomplete'));
  assert.ok(output.includes('coverage remains pending'));
}
for (const status of ['unverified', 'unknown', undefined]) {
  const output = render(status);
  assert.ok(output.includes('Implementation admission has not yet been verified.'));
  assert.ok(!output.includes('Implementation can continue'));
}
const unavailable = render('technically_unavailable');
assert.ok(unavailable.includes('Reported tooling repair is needed before affected implementation'));
assert.ok(!unavailable.includes('Implementation can continue'));
for (const status of ['ready', 'continuing', 'unverified', 'technically_unavailable']) {
  assert.ok(render(status).includes('Final approval still requires the remaining checks.'));
  assert.ok(!render(status).includes('Required verification complete'));
}
"""
    )
    subprocess.run([node, "-e", script], check=True, capture_output=True, timeout=10)


def test_dashboard_answer_reaches_the_waiting_agent_through_the_supervisor(
    controller, sample_repo, monkeypatch, db_path, tmp_path
):
    """#31: the developer answers a blocking question in the dashboard; the supervisor
    records it with AL's `ask --answer`, whose output routes it to the same warm agent."""
    import sys

    from harness import execution_al_live as Live
    from harness import execution_worker as Worker
    from harness.dashboard.execution_view import execution_action

    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    Live.publish(
        execution,
        {
            "run_id": "r",
            "agent_questions": [
                {
                    "id": "Q1",
                    "kind": "blocking",
                    "state": "open",
                    "question": "Port?",
                    "agent": "eng-1",
                }
            ],
        },
        "service",
    )
    body = json.dumps({"task": tid, "execution": eid, "question": "Q1", "text": "8080"}).encode()
    status, data = execution_action(db_path, body, "question-answer")
    assert status == 409 and "supervised" in data["error"]  # Reserved: no supervisor to deliver it
    controller.conn.execute("UPDATE executions SET status='Running' WHERE id=?", (eid,))
    controller.conn.commit()
    status, data = execution_action(db_path, body, "question-answer")
    assert status == 200 and data["state"] == "Queued"
    status, data = execution_action(
        db_path,
        json.dumps({"task": tid, "execution": eid, "question": "Q9", "text": "x"}).encode(),
        "question-answer",
    )
    assert status == 409 and "not open" in data["error"]
    # The supervisor delivers it through the protected launcher (a recording stand-in here).
    launcher = tmp_path / "alctl"
    record = tmp_path / "calls.json"
    launcher.write_text(
        "#!" + sys.executable + "\nimport json,sys\n"
        f"open({str(record)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
        "print('AL · answered Q1')\n"
        "print('AL · send the answer to the asking agent: Send'+'Message(to=\"eng-1\")')\n"
    )
    launcher.chmod(0o755)
    monkeypatch.setattr(Worker, "require_execution_authority", lambda *a, **k: None)
    delivered = Worker.deliver_answers(controller, eid, {"launcher": str(launcher)})
    argv = json.loads(record.read_text())
    assert argv[argv.index("--") + 1 :] == ["ask", "--answer", "Q1", "--text", "8080"]
    assert delivered[0]["state"] == "Delivered" and "eng-1" in delivered[0]["result"]
    assert Worker.deliver_answers(controller, eid, {"launcher": str(launcher)}) == []
