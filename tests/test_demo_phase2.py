"""P2-08: the Phase 2 demo (scripts/demo_phase2.py) is reproducible, pinned and isolated."""

import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.sample_repo import tree_snapshot
from tests.test_demo import BLOCK

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "demo_phase2.py"
DEMO_MD = REPO_ROOT / "docs" / "demo-phase-2.md"
WRONG = "connection:item-store->exporter"
RIGHT = "connection:exporter->item-store"
END = "next: harness execution reconnect t-xxxxxxxx exec-xxxxxxxxxxxxxxxxxxxxxxxx\n"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the demo needs git")


def _load_demo():
    spec = importlib.util.spec_from_file_location("demo_phase2_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo-phase2")
    home = tmp / "conftest-home"
    snapshots = {name: tree_snapshot(REPO_ROOT / name) for name in ("docs", "tasks")}
    env = {k: v for k, v in os.environ.items() if k not in ("KEEP_DEMO", "HARNESS_PYTHON")}
    env.update(TMPDIR=str(tmp), HARNESS_HOME=str(home), HARNESS_DB=str(tmp / "db" / "x.db"))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    after = {name: tree_snapshot(REPO_ROOT / name) for name in ("docs", "tasks")}
    return proc, tmp, home, snapshots, after


def test_transcript_matches_docs(demo_run):
    proc = demo_run[0]
    assert proc.returncode == 0, proc.stderr
    match = BLOCK.search(DEMO_MD.read_text(encoding="utf-8"))
    assert match is not None
    assert proc.stdout == match.group(1)


def test_the_scenario_milestones_in_order(demo_run):
    out = demo_run[0].stdout
    milestones = [
        "$ POST /api/comments ",
        '201 {"comment": ',
        '"enabled": false',
        "$ POST /api/approve ",
        '409 {"error": "1 diagram comment(s) are not resolved (open 1 · addressed 0)',
        "$ python -m harness.redesign t-xxxxxxxx\n",
        f"#1 · correction · connection Item Store -> Exporter ({WRONG}) · version 1\n",
        "recorded diagram version 2\n#1 addressed in diagram version 2\n",
        "final design review passed (checkpoint recorded)\n",
        f"  removed: connection Item Store -> Exporter ({WRONG})\n",
        f"  added: connection Exporter -> Item Store ({RIGHT})\n",
        '"text": "1 diagram comment(s) not resolved (open 0 · addressed 1)"',
        "$ POST /api/comments/resolve ",
        '200 {"changed": true',
        '"blockers": [], "comments": {"addressed": 0, "open": 0}, "enabled": true',
        "$ POST /api/approve ",
        "201\n  created: true\n",
        "  approval #1 recorded: spec v1 sha256:<hash>",
        "launch: WorkerOwned (external provider boundary simulated)",
        "dashboard stopped\n",
        "approval: spec v1 sha256:<hash> (current)\n",
        "\nv1 ok\n",
        "$ harness resume t-xxxxxxxx\n",
        "next: harness execution reconnect t-xxxxxxxx exec-xxxxxxxxxxxxxxxxxxxxxxxx\n",
    ]
    position = 0
    for milestone in milestones:
        found = out.find(milestone, position)
        assert found >= 0, f"missing transcript milestone: {milestone!r}"
        position = found + len(milestone)
    assert out.endswith(END)
    assert re.findall(r"^(\d{3})\b", out, re.M).count("409") == 1
    assert out.count("(exit ") == 0
    for value in re.findall(r"--(?:reason|rationale) '([^']*)'", out):
        assert value.startswith("[demo]"), value
    for value in re.findall(r"^\$ harness (?:interview answer|testplan add|design \w+) "
                            r"t-xxxxxxxx (?:\w+ )+'([^']*)'", out, re.M):  # fmt: skip
        assert value.startswith("[demo]"), value
    assert '"text": "[demo] ' in out


def test_no_run_specific_values_leak(demo_run):
    proc, tmp = demo_run[0], demo_run[1]
    out = proc.stdout
    assert str(tmp) not in out and os.path.realpath(tmp) not in out
    assert "harness-demo-phase2." not in out and str(Path.home()) not in out
    assert not re.search(r"\bt-[0-9a-f]{8}\b", out)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out)
    assert not re.search(r"\b[0-9a-f]{40}\b|sha256:[0-9a-f]{64}", out)
    assert not re.search(r"127\.0\.0\.1:\d", out)


def test_isolation(demo_run):
    proc, tmp, home, before, after = demo_run
    assert proc.returncode == 0, proc.stderr
    assert after == before
    assert not home.exists() and not (tmp / "db").exists()
    assert list(tmp.glob("harness-demo-phase2.*")) == []


def test_port_normalisation(tmp_path):
    normalise_phase2 = _load_demo().normalise_phase2
    demo_dir = tmp_path / "harness-demo-phase2.abc"
    text = (
        "dashboard: http://127.0.0.1:53817/ · task t-1a2b3c4d · Ctrl-C stops it\n"
        f"{demo_dir}/repo 2026-10-17T11:28:40+00:00 127.0.0.2:80 localhost:8080 #1 v1\n"
    )
    assert normalise_phase2(text, demo_dir) == (
        "dashboard: http://127.0.0.1:<port>/ · task t-xxxxxxxx · Ctrl-C stops it\n"
        "<demo>/repo <timestamp> 127.0.0.2:80 localhost:8080 #1 v1\n"
    )


def test_early_demo_failure_keeps_real_dashboard_owner_until_worker_reaped(tmp_path):
    module = _load_demo()
    demo = module.Phase2Demo(tmp_path, sys.executable)
    ready = tmp_path / "native-ready"
    executable = tmp_path / "native-bin" / "claude"
    text = executable.read_text()
    marker = 'print(json.dumps({"type": "system"'
    assert marker in text
    executable.write_text(
        text.replace(
            marker,
            f"Path({str(ready)!r}).touch()\n__import__('time').sleep(12)\n" + marker,
            1,
        )
    )
    owner = None
    try:
        demo.make_sample_repo()
        task, _ = module.build_task(demo)
        demo.start_dashboard(task)
        owner = demo.dashboard
        state = demo.dashboard_state(task)
        status, _ = demo.http("POST", "/api/approve", module._tokens(state))
        assert status == 201
        deadline = time.monotonic() + 60
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists() and owner.poll() is None
        conn = sqlite3.connect(demo.env["HARNESS_DB"])
        try:
            assert conn.execute("SELECT status FROM executions").fetchone()[0] == "Running"
        finally:
            conn.close()
        started = time.monotonic()
        with pytest.raises(module.DemoError, match="synthetic early failure"):
            try:
                raise module.DemoError("synthetic early failure after native activation")
            finally:
                demo.kill_dashboard()
        from tests.fixture_workers import OWNER_SHUTDOWN_TIMEOUT

        assert time.monotonic() - started < OWNER_SHUTDOWN_TIMEOUT
        assert owner.returncode == 0 and demo.dashboard is None
        execution = demo.wait_for_execution(task)
        receipt = Path(execution["handoff_path"]).parent / "fixture-worker-exit.json"
        facts = json.loads(receipt.read_text())["facts"]
        assert facts["worker_reaped"] is True and facts["native_closed"] is True
        assert facts["worker_returncode"] == facts["native_returncode"] == 0
    finally:
        demo.kill_dashboard()
        if demo.retention_thread is not None:
            demo.retention_thread.join(timeout=130)
    assert owner is not None and owner.returncode == 0


def test_unresolved_demo_shutdown_retains_handle_and_durable_state(tmp_path, monkeypatch):
    module = _load_demo()
    from tests import fixture_workers

    monkeypatch.setattr(fixture_workers, "OWNER_SHUTDOWN_TIMEOUT", 0.05)
    ready, release = tmp_path / "owner-ready", tmp_path / "owner-release"
    program = (
        "import signal,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(ready)!r}).touch()\n"
        "deadline=time.monotonic()+5\n"
        f"while not Path({str(release)!r}).exists() and time.monotonic()<deadline:\n"
        "    time.sleep(0.01)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    demo = module.Phase2Demo.__new__(module.Phase2Demo)
    demo.demo_dir, demo.dashboard = tmp_path, process
    demo._dashboard_stopping, demo.retention_thread, demo.retain_state = False, None, False
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        with pytest.raises(module.DemoError, match="ownership retained"):
            demo.kill_dashboard()
        assert demo.dashboard is process and process.poll() is None
        assert demo.retain_state is True and demo.retention_thread.is_alive()
        assert demo.retention_thread.daemon is False
        path = tmp_path / "fixture-owner-retained.json"
        saved = json.loads(path.read_text())
        assert saved["owner_reaped"] is False
        assert saved["descendant_cleanup"] == "unverified"
    finally:
        release.touch()
        if demo.retention_thread is not None:
            demo.retention_thread.join(timeout=7)
        process.wait(timeout=2)
    assert demo.dashboard is None and demo.retention_thread.is_alive() is False
    assert path.exists() and json.loads(path.read_text())["owner_reaped"] is True
