"""The approval fixture must distinguish a durable status from an owned, reaped exit."""

import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from tests.fixture_workers import (
    EXPECTED_INTERRUPTION,
    FixtureWorkers,
    wait_for_fixture_execution,
)


@pytest.fixture
def fixture_state(tmp_path):
    database = tmp_path / "state.db"
    handoff = tmp_path / "handoff.json"
    conn = sqlite3.connect(database)
    conn.executescript("""
        CREATE TABLE executions (
            id TEXT, task_id TEXT, status TEXT, result TEXT, error TEXT,
            handoff_path TEXT, epoch_id TEXT, authority_generation INTEGER
        );
        CREATE TABLE execution_attempts (
            id TEXT, execution_id TEXT, token TEXT, status TEXT,
            worker_pid INTEGER, child_pid INTEGER
        );
        CREATE TABLE execution_initializations (execution_id TEXT, state TEXT, identity TEXT);
    """)
    conn.execute(
        "INSERT INTO executions VALUES (?,?,?,?,?,?,?,?)",
        (
            "execution",
            "task",
            "Interrupted",
            None,
            EXPECTED_INTERRUPTION,
            str(handoff),
            "epoch",
            0,
        ),
    )
    conn.execute(
        "INSERT INTO execution_attempts VALUES (?,?,?,?,?,?)",
        (
            "attempt",
            "execution",
            "fixture-token",
            "Exited",
            None,
            12345,
        ),
    )
    conn.commit()
    conn.close()
    settlements = tmp_path / "settlements"
    settlements.mkdir()
    receipt = settlements / "native-fixture-token.json"
    receipt.write_text(
        json.dumps(
            {
                "kind": "closed-native-context-v1",
                "execution_id": "execution",
                "epoch_id": "epoch",
                "authority_generation": 0,
                "token": "fixture-token",
                "child_pid": 12345,
                "native_returncode": 0,
                "owned_processes_closed": True,
                "tool_transport_closed": True,
            }
        )
    )
    receipt.chmod(0o600)

    def launch(owner, program="import time; time.sleep(0.1)"):
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        connection = sqlite3.connect(database)
        connection.execute("UPDATE execution_attempts SET worker_pid=?", (process.pid,))
        connection.commit()
        connection.close()
        owner._own(process, database, "execution")
        return process

    return database, receipt, launch


def test_terminal_row_is_not_completion_until_actual_worker_is_reaped(fixture_state):
    database, _, launch = fixture_state
    with FixtureWorkers() as owner:
        process = launch(owner, "import time; time.sleep(0.3)")
        with pytest.raises(AssertionError, match="completion_wait_timeout"):
            wait_for_fixture_execution(database, "task", timeout=0.05)
        assert process.poll() is None
        execution = wait_for_fixture_execution(database, "task", timeout=3)
        assert execution["status"] == "Interrupted" and execution["result"] is None
        assert process.returncode == 0
        assert owner.records[0]["facts"]["worker_reaped"] is True


@pytest.mark.parametrize(
    "change, code",
    [
        ("native_exit", "native_exit_nonzero"),
        ("closure", "native_context_not_closed"),
        ("completed", "execution_not_interrupted"),
        ("product_result", "product_result_present"),
        ("error", "unexpected_interruption"),
        ("attempt", "attempt_not_exited"),
    ],
)
def test_empty_fixture_cannot_hide_failure_or_claim_product_success(fixture_state, change, code):
    database, receipt, launch = fixture_state
    secret = "synthetic-secret-must-not-appear"
    if change in {"native_exit", "closure"}:
        value = json.loads(receipt.read_text())
        value["native_returncode" if change == "native_exit" else "owned_processes_closed"] = (
            1 if change == "native_exit" else False
        )
        receipt.write_text(json.dumps(value))
    else:
        conn = sqlite3.connect(database)
        if change == "completed":
            conn.execute("UPDATE executions SET status='Completed'")
        elif change == "product_result":
            conn.execute("UPDATE executions SET result=?", (json.dumps({"secret": secret}),))
        elif change == "error":
            conn.execute("UPDATE executions SET error=?", (secret,))
        else:
            conn.execute("UPDATE execution_attempts SET status='Running'")
        conn.commit()
        conn.close()
    with pytest.raises(AssertionError, match=code) as failure, FixtureWorkers() as owner:
        process = launch(owner)
        wait_for_fixture_execution(database, "task", timeout=3)
    assert secret not in str(failure.value)
    assert process.returncode == 0


def test_fixture_teardown_reaps_workers_even_when_caller_never_waits(fixture_state):
    _, _, launch = fixture_state
    processes = []
    with FixtureWorkers() as owner:
        # Real dashboard request threads are daemons; ownership monitors must
        # explicitly remain non-daemon even when spawned from that context.
        request = threading.Thread(target=lambda: processes.append(launch(owner)), daemon=True)
        request.start()
        request.join(timeout=3)
        assert len(processes) == 1
        assert owner.records[0]["thread"].daemon is False
    assert processes[0].returncode == 0
    assert owner.records[0]["thread"].is_alive() is False


def test_worker_nonzero_exit_is_failure_even_with_successful_native_receipt(fixture_state):
    database, _, launch = fixture_state
    with pytest.raises(AssertionError, match="worker_exit_nonzero"), FixtureWorkers() as owner:
        process = launch(owner, "raise SystemExit(7)")
        wait_for_fixture_execution(database, "task", timeout=3)
    assert process.returncode == 7


def test_bounded_timeout_preserves_observation_and_unwinds_owned_worker(fixture_state, tmp_path):
    database, _, _ = fixture_state
    ready, closed = tmp_path / "ready", tmp_path / "closed"
    program = (
        "from pathlib import Path\nimport time\n"
        f"Path({str(ready)!r}).touch()\n"
        "try:\n    time.sleep(30)\nfinally:\n"
        f"    Path({str(closed)!r}).touch()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        with (
            pytest.raises(AssertionError, match="worker_timeout"),
            FixtureWorkers(
                work_timeout=0.05,
                cleanup_timeout=2,
            ) as owner,
        ):
            owner._own(process, database, "execution")
            wait_for_fixture_execution(database, "task", timeout=3)
        facts = owner.records[0]["facts"]
        assert facts["at_timeout"]["status"] == "Interrupted"
        assert facts["worker_reaped"] is True and facts["worker_returncode"] != 0
        assert closed.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


def test_unresponsive_worker_retains_ownership_of_its_live_child(fixture_state, tmp_path):
    database, receipt, _ = fixture_state
    receipt.unlink()  # A live child has no closed-native-context receipt.
    ready, release, closed = (tmp_path / name for name in ("ready", "release", "closed"))
    program = (
        "import json,signal,subprocess,sys,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
        "value={'child_pid':child.pid,'child_live':child.poll() is None}\n"
        f"Path({str(ready)!r}).write_text(json.dumps(value))\n"
        "try:\n    deadline=time.monotonic()+6\n"
        f"    while not Path({str(release)!r}).exists() and time.monotonic()<deadline:\n"
        "        time.sleep(0.01)\n"
        "finally:\n    child.terminate()\n"
        "    try:\n        child.wait(timeout=2)\n"
        "    except subprocess.TimeoutExpired:\n        child.kill(); child.wait(timeout=2)\n"
        "    value={'child_reaped':child.returncode is not None}\n"
        f"    Path({str(closed)!r}).write_text(json.dumps(value))\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    owner = FixtureWorkers(work_timeout=0.05, cleanup_timeout=0.05)
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        child = json.loads(ready.read_text())
        conn = sqlite3.connect(database)
        conn.execute(
            "UPDATE execution_attempts SET worker_pid=?,child_pid=?",
            (
                process.pid,
                child["child_pid"],
            ),
        )
        conn.commit()
        conn.close()
        with pytest.raises(AssertionError, match="worker_unresolved"), owner:
            owner._own(process, database, "execution")
            wait_for_fixture_execution(database, "task", timeout=3)
        facts = owner.records[0]["facts"]
        assert facts["ownership_retained"] is True and facts["worker_reaped"] is False
        assert facts["descendants_settled"] is False and facts["native_closed"] is False
        assert process.poll() is None and owner.records[0]["thread"].is_alive()
        assert child["child_live"] is True and not closed.exists()
        saved = json.loads((tmp_path / "fixture-worker-exit.json").read_text())["facts"]
        assert saved["code"] == "worker_unresolved" and saved["ownership_retained"] is True
    finally:
        release.touch()  # The fixture's actual owner reaps its own child.
        process.wait(timeout=7)
        if owner.records:
            owner.records[0]["thread"].join(timeout=3)
    assert json.loads(closed.read_text())["child_reaped"] is True
    assert owner.records[0]["thread"].is_alive() is False
    assert owner.records[0]["facts"]["code"] == "worker_timeout"
    assert owner.records[0]["facts"]["worker_reaped"] is True
