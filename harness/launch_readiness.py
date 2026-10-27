"""Read-only pre-approval tooling readiness, separate from product acceptance."""

import hashlib
import json
import re
from pathlib import Path

from harness.dependency_layout import inspect
from harness.execution_hosts import installed_package

REQUIRED = (
    "SKILL.md",
    "reference/handoffs.md",
    "reference/routing.md",
    "reference/review.md",
    "reference/execution-integration.md",
    "reference/service-coverage.md",
    "reference/gates.md",
    "reference/schemas/gate-manifest.json",
    "reference/schemas/architect-report.json",
)


def readiness(root, host):
    checks = []
    try:
        package = installed_package(host, root)
        from harness.native_tool_policy import executable_identity

        executable = executable_identity(package["executable"], host)
        checks.append(
            {
                "id": "native-host-and-al-protocol",
                "status": "PASS",
                "host": host,
                "package_sha256": package["sha256"],
                "executable": executable,
            }
        )
        base = Path(package["package"])
        inputs = set(REQUIRED)
        visited = set()
        while inputs - visited:
            name = min(inputs - visited)
            visited.add(name)
            path = base / name
            safe = (
                not Path(name).is_absolute()
                and ".." not in Path(name).parts
                and path.is_file()
                and not any(p.is_symlink() for p in [path, *path.parents])
                and path.stat().st_size <= 4 * 1024 * 1024
            )
            checks.append(
                {
                    "id": "skill:" + name,
                    "status": "PASS" if safe else "BLOCKED",
                    "resource": "harness:skill" if name == "SKILL.md" else "harness:skill/" + name,
                }
            )
            if safe:
                raw = path.read_bytes()
                checks[-1]["sha256"] = hashlib.sha256(raw).hexdigest()
                text = raw.decode()
                if path.suffix == ".json":
                    json.loads(text)
                inputs.update(re.findall(r"reference/[A-Za-z0-9_./-]+\.(?:md|json)", text))
                if len(inputs) > 512:
                    raise ValueError("mandatory skill input discovery exceeded its bound")
    except (OSError, ValueError) as exc:
        checks.append(
            {"id": "native-host-and-al-protocol", "status": "BLOCKED", "reason": str(exc)}
        )
    try:
        layout = inspect(root)
        checks.append(
            {
                "id": "dependency-layout",
                "status": "PASS" if layout["ready"] else "BLOCKED",
                "hardlinks": layout["hardlinks"],
                "resolution": "harness dependencies inspect then reconcile the exact "
                "hash-bound ignored dependency plan",
            }
        )
    except (OSError, ValueError) as exc:
        checks.append({"id": "dependency-layout", "status": "BLOCKED", "reason": str(exc)})
    return {
        "schema_version": 1,
        "kind": "launch-readiness-v1",
        "repository": str(Path(root).resolve()),
        "host": host,
        "ready": all(c["status"] == "PASS" for c in checks),
        "checks": checks,
        "authentication": "UNVERIFIED: no provider login, request or credential inspection",
        "product_acceptance": "NOT_ASSESSED",
        "approval_required_for_implementation": True,
    }
