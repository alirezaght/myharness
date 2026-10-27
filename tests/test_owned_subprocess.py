"""A timed-out proof command cannot leave its child running after it returns."""

import os
import subprocess
import sys
import time

import pytest

from harness.owned_subprocess import run_owned


def test_timeout_reaps_the_owned_parent_and_terminates_its_child(tmp_path):
    marker = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\ntime.sleep(60)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_owned(
            [sys.executable, "-c", code, str(marker)], cwd=tmp_path, env=dict(os.environ), timeout=1
        )
    pid = int(marker.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
        )
        if result.returncode or result.stdout.strip().startswith("Z"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("owned command left a live descendant after timeout")
