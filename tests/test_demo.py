import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.sample_repo import tree_snapshot

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "demo.py"
DEMO_MD = REPO_ROOT / "docs" / "demo.md"
BLOCK = re.compile(
    r"<!-- demo-transcript:begin -->\n```text\n(.*)```\n<!-- demo-transcript:end -->", re.S
)
Q5 = "Q5 [acceptance_criteria] What are the acceptance criteria for calling this done?\n"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the demo needs git")


def _load_demo():
    spec = importlib.util.spec_from_file_location("demo_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo")
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
        timeout=120,
    )
    after = {name: tree_snapshot(REPO_ROOT / name) for name in ("docs", "tasks")}
    return proc, tmp, home, snapshots, after


def test_transcript_matches_docs(demo_run):
    proc = demo_run[0]
    assert proc.returncode == 0, proc.stderr
    match = BLOCK.search(DEMO_MD.read_text(encoding="utf-8"))
    assert match is not None
    assert proc.stdout == match.group(1)


def test_milestones(demo_run):
    out = demo_run[0].stdout
    interrupt = out.index("## Interrupt")
    finish = out.index("## 4. Finish the interview")
    assert out[:interrupt].endswith(Q5 + "\n")
    resumed = out[interrupt:finish]
    assert f"{Q5}next: harness interview next t-xxxxxxxx\n" in resumed
    assert resumed.endswith(f"$ harness interview next t-xxxxxxxx\n{Q5}\n")
    assert "recorded DEC-0002 v1 -> <demo>/repo/docs/decisions/" in out
    assert (
        "$ harness approve t-xxxxxxxx\nharness approve: task t-xxxxxxxx is in phase test_plan"
        in out
    )
    assert "test_plan; run: harness phase t-xxxxxxxx next\n(exit 1)\n" in out
    assert out.count("(exit ") == 1
    assert "final design review" in out and out.count("blocking: no\n") >= 4
    assert '$ git commit -m "[demo] record decisions"\n' in out
    assert "(revision <rev>, clean)" in out and "approval #1 recorded" in out
    assert out.count("sha256:<hash> (current)") == 2
    assert "\nv1 ok\n" in out
    assert out.endswith("next: harness execution start t-xxxxxxxx\n")
    for line in re.findall(r"^- [QES]\d+ .*$", out, re.M):
        assert "[demo]" in line, line
    for value in re.findall(r"--(?:title|recommendation|rationale|impact|reason) '([^']*)'", out):
        assert value.startswith("[demo]"), value


def test_no_run_specific_values_leak(demo_run):
    proc, tmp = demo_run[0], demo_run[1]
    out = proc.stdout
    assert str(tmp) not in out and os.path.realpath(tmp) not in out
    assert "harness-demo." not in out and str(Path.home()) not in out
    assert not re.search(r"\bt-[0-9a-f]{8}\b", out)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", out)
    assert not re.search(r"\b[0-9a-f]{40}\b|sha256:[0-9a-f]{64}", out)


def test_isolation(demo_run):
    proc, tmp, home, before, after = demo_run
    assert proc.returncode == 0, proc.stderr
    assert after == before
    assert not home.exists() and not (tmp / "db").exists()
    assert list(tmp.glob("harness-demo.*")) == []


def test_normalise_rules(tmp_path):
    normalise = _load_demo().normalise
    demo_dir = tmp_path / "harness-demo.abc"
    rev, digest = "0123456789abcdef0123456789abcdef01234567", "ab" * 32
    text = (
        f"approved v1 -> {demo_dir}/repo/docs/specs/t-1a2b3c4d/v1.md (revision {rev}, clean)\n"
        f"{os.path.realpath(demo_dir)}/x 2026-10-17T11:28:40+00:00 2026-01-01 sha256:{digest}\n"
        "DEC-0002 v1 Q5 P1 E3 S1 approval #1 (#32) 6 files t-123 deadbeef\n"
    )
    assert normalise(text, demo_dir) == (
        "approved v1 -> <demo>/repo/docs/specs/t-xxxxxxxx/v1.md (revision <rev>, clean)\n"
        "<demo>/x <timestamp> <date> sha256:<hash>\n"
        "DEC-0002 v1 Q5 P1 E3 S1 approval #1 (#32) 6 files t-123 deadbeef\n"
    )
