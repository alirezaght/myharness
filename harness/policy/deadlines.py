"A fixed wall interval includes downtime; same-supervisor monotonic time adds a bound."

import platform
import subprocess
import time
from pathlib import Path

from harness.policy.contracts import require


def clock_identity():
    try:
        if platform.system() == "Linux":
            return "linux:" + Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            return "darwin:" + result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


CLOCK_ID = clock_identity()


def deadline(conn, pools, requested):
    now, mono = time.time(), time.monotonic()
    remaining = requested
    for pool in pools:
        dimension = pool["dimension"]
        require(
            dimension not in {"generation_tokens", "monetary_cost"},
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        if dimension == "operation_deadline_ms":
            remaining = min(remaining, pool["maximum"] / 1000)
        if dimension == "execution_elapsed_ms":
            require(
                CLOCK_ID is not None
                and pool["clock_id"] == CLOCK_ID
                and pool["state"] == "active"
                and now >= pool["last_wall"]
                and now >= pool["origin_wall"],
                "CLOCK_UNCERTAIN",
            )
            remaining = min(remaining, pool["deadline_wall"] - now)
            if pool["clock_id"] == CLOCK_ID:
                remaining = min(
                    remaining, pool["maximum"] / 1000 - (mono - pool["origin_monotonic"])
                )
            conn.execute("UPDATE policy_pools SET last_wall=? WHERE id=?", (now, pool["id"]))
    require(remaining > 0, "BUDGET_EXHAUSTED")
    return mono + remaining, now + remaining


def origin_receipt():
    """Call before an epoch claim or transaction; never reconstruct an old origin."""
    return {"wall": time.time(), "monotonic": time.monotonic(), "clock_id": CLOCK_ID}


def record_origin(conn, execution_id, receipt):
    conn.execute(
        "INSERT INTO policy_origins VALUES (?,?,?,?)",
        (execution_id, receipt["wall"], receipt["monotonic"], receipt["clock_id"]),
    )
