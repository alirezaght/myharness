"""P2-08: the Phase 2 demo (scripts/demo_phase2.py) is reproducible, pinned and isolated."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
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
