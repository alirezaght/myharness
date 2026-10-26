"""Actual isolated about:blank capability probe; never navigates to staging."""

import subprocess
import tempfile
from pathlib import Path

from harness.browser_driver import BrowserDriver, chrome_executable
from harness.db import utcnow
from harness.execution_contract import canonical, digest
from harness.execution_logs import redact


def probe_capability():
    observation = {
        "available": False,
        "integration": "Chrome CDP pipe",
        "observed_at": utcnow(),
        "limitations": ["Fresh anonymous profile only", "No staging validation performed"],
        "methods": [],
    }
    try:
        executable = chrome_executable()
        version = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()[:256]
        with tempfile.TemporaryDirectory(prefix="harness-browser-probe-") as root:
            plan = {
                "timeouts": {"action_ms": 5000, "run_ms": 15000},
                "viewport": {"width": 1280, "height": 800},
                "navigation_scope": [],
            }
            driver = BrowserDriver(plan, Path(root))
            try:
                observation.update(
                    available=True,
                    executable=executable,
                    version=version,
                    methods=sorted(set(driver.supported_methods)),
                )
            finally:
                driver.close()
    except (ValueError, OSError, TimeoutError, subprocess.SubprocessError) as error:
        observation["error"] = "Chrome capability unavailable: " + redact(str(error))
    return observation


def probe_browser(controller, execution_id):
    from harness.execution_authority import require_execution_authority

    require_execution_authority(controller, execution_id, operation="delivery")
    observation = probe_capability()
    controller.conn.execute(
        "INSERT OR IGNORE INTO browser_capabilities VALUES (?,?,?,?)",
        (digest([execution_id, observation]), execution_id, canonical(observation), utcnow()),
    )
    return observation
