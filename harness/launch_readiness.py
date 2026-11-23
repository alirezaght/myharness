"""Read-only pre-approval tooling readiness, separate from product acceptance."""

import hashlib
import json
import re
import shlex
from pathlib import Path

from harness.dependency_layout import LIMIT_ENV, DependencyLayoutLimit, inspect, scan_limit
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


RESOLUTION = (
    "harness dependencies inspect then reconcile the exact hash-bound ignored dependency plan"
)


def _command(task_id, *parts):
    return " ".join(["harness", parts[0], shlex.quote(task_id) if task_id else "TASK", *parts[1:]])


def dependency_commands(task_id, root, host, scopes):
    """Exact commands that resolve blocked dependency hardlinks, in order.

    Readiness re-observes every dependency scope; once inspect and reconcile have
    replaced the scoped hardlinks with private copies, the re-run passes.
    """
    repo = shlex.quote(str(Path(root).resolve()))
    return [
        _command(
            task_id,
            "dependencies inspect",
            "--repo",
            repo,
            "--scopes",
            shlex.quote(",".join(scopes)),
            "--output",
            "layout.json",
        ),
        _command(
            task_id,
            "dependencies reconcile",
            "--repo",
            repo,
            "--file",
            "layout.json",
            "--sha256",
            "SHA256_PRINTED_BY_INSPECT",
        ),
        _command(task_id, "execution readiness", "--host", host, "--repo", repo),
    ]


def _dependency_check(root, host, task_id):
    try:
        layout = inspect(root)
    except DependencyLayoutLimit as exc:
        if exc.kind == "hardlinks":
            # Too many hardlinks for one plan: reconcile the named scope first.
            commands = dependency_commands(task_id, root, host, [exc.scope])
        else:
            # Observation budget: the whole layout must be observed, so raise it for
            # readiness and start alike; a re-run names any hardlinks it then finds.
            env = LIMIT_ENV + "=" + str(scan_limit() * 2) + " "
            repo = shlex.quote(str(Path(root).resolve()))
            commands = [
                env + _command(task_id, "execution readiness", "--host", host, "--repo", repo),
                env + _command(task_id, "execution start", "--host", host),
            ]
        return {
            "id": "dependency-layout",
            "status": "BLOCKED",
            "reason": str(exc) + " → " + "; then ".join(commands),
            "scope": exc.scope,
            "next": commands,
        }
    if layout["ready"]:
        return {
            "id": "dependency-layout",
            "status": "PASS",
            "hardlinks": [],
            "resolution": RESOLUTION,
        }
    scopes = [
        scope
        for scope in layout["scopes"]
        if any(
            row["path"] == scope or row["path"].startswith(scope + "/")
            for row in layout["hardlinks"]
        )
    ]
    commands = dependency_commands(task_id, root, host, scopes)
    return {
        "id": "dependency-layout",
        "status": "BLOCKED",
        "reason": str(len(layout["hardlinks"]))
        + " ignored dependency hardlinks need private copies → "
        + "; then ".join(commands),
        "hardlinks": layout["hardlinks"],
        "next": commands,
        "resolution": RESOLUTION,
    }


def readiness(root, host, *, accept_unverified_host_version=False, task_id=None):
    """``accept_unverified_host_version``: True or a recorded override record.

    ``task_id`` only fills the exact next commands named by a BLOCKED check.
    """
    checks = []
    try:
        package = installed_package(host, root)
        from harness.native_tool_policy import executable_identity

        executable = executable_identity(
            package["executable"], host, accept_unverified=accept_unverified_host_version
        )
        checks.append(
            {
                "id": "native-host-and-al-protocol",
                "status": "PASS",
                "host": host,
                "package_sha256": package["sha256"],
                "executable": executable,
            }
        )
        if "qualification" in executable:
            checks[-1]["override"] = executable["qualification"]
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
        checks.append(_dependency_check(root, host, task_id))
    except (OSError, ValueError) as exc:
        checks.append({"id": "dependency-layout", "status": "BLOCKED", "reason": str(exc)})
    return {
        "schema_version": 1,
        "kind": "launch-readiness-v1",
        "repository": str(Path(root).resolve()),
        "host": host,
        "ready": all(c["status"] == "PASS" for c in checks),
        "ready_scope": "launch_prerequisites",
        "implementation_readiness": "UNVERIFIED",
        "readiness_notice": (
            "Launch prerequisites only; planning, candidate inspection and scoped writer "
            "admission have not been verified."
        ),
        "checks": checks,
        "authentication": "UNVERIFIED: no provider login, request or credential inspection",
        "product_acceptance": "NOT_ASSESSED",
        "approval_required_for_implementation": True,
    }


class LaunchReadinessError(ValueError):
    def __init__(self, message, *, execution_id=None):
        super().__init__(message)
        self.execution_id = execution_id


def require_launch_ready(root, host, *, accept_unverified_host_version=False, task_id=None):
    result = readiness(
        root,
        host,
        accept_unverified_host_version=accept_unverified_host_version,
        task_id=task_id,
    )
    failures = [check for check in result["checks"] if check["status"] != "PASS"]
    if failures:
        raise LaunchReadinessError(
            "Selected host is not ready: "
            + "; ".join(
                check.get("reason") or check.get("resolution") or check["id"] for check in failures
            )
        )
    return result
