"""Closed, scoped dependency setup; installer access is not native command authority."""

import json
import secrets
import subprocess
import tomllib
from pathlib import Path

from harness.execution_contract import digest
from harness.execution_files import relative_path
from harness.execution_logs import StreamRedactor


def ordinary(root, name, maximum=4 * 1024 * 1024):
    relative_path(name)
    from harness.template_classification import _ordinary

    try:
        return _ordinary(root / name, maximum)[0]
    except ValueError as error:
        raise ValueError("bounded ordinary dependency manifest required") from error


def context_revision(state, task_id):
    from harness.execution_lineage import scoped_amendments

    task = state["integration"]["tasks"][task_id]
    return digest(
        {
            "task": task_id,
            "revision": task["revision"],
            "graph_revision": state["integration"]["graph_revision"],
            "amendments": [a["artifact"]["sha256"] for a in scoped_amendments(state, task_id)],
        }
    )


def task_unit(operation, controller, state, principal, request):
    task = state["integration"]["tasks"].get(request["task_id"])
    if not task:
        raise ValueError("registered task required for dependency setup")
    if principal["role"] == "orchestrator":
        if context_revision(state, request["task_id"]) != request["expected_revision"]:
            raise ValueError("current registered task revision required for dependency setup")
    else:
        operation.admitted(controller, principal, request["task_id"], request["expected_revision"])
    unit = request["cwd"]
    if unit != ".":
        relative_path(unit)
    if not any(
        unit == "."
        or f == "."
        or unit == f
        or unit.startswith(f.rstrip("/") + "/")
        or f.startswith(unit.rstrip("/") + "/")
        for f in task["files"]
    ):
        raise ValueError("dependency setup exceeds this task's declared unit")
    return unit


class SetupReport:
    def __init__(self, tooling, unit, record):
        self.tooling, self.unit, self.record = tooling, unit, record
        self.redactors = {name: StreamRedactor() for name in ("stdout", "stderr")}
        self.output, self.size = "", 0

    def consume(self, channel, raw):
        self.size += len(raw)
        if self.size > 1024 * 1024:
            raise ValueError("dependency setup output limit exceeded")
        self.output += self.redactors[channel].feed(raw.decode(errors="replace"), final=not raw)

    def publish(self, code):
        if code:
            result = {
                "status": "Unavailable",
                "kind": "dependency_setup",
                "runtime_kind": self.record.get("kind"),
                "returncode": code,
                "output": self.output[-12000:],
                "test_evidence": False,
            }
            self.tooling.failure(self.unit, self.record, result)
            return result
        for name, expected in self.record["inputs"].items():
            if digest(ordinary(self.tooling.repository, name)) != expected:
                raise ValueError("dependency lock changed during setup; result is stale")
        self.tooling.ready(self.unit, self.record)
        return {
            "status": "Ready",
            "kind": "dependency_setup",
            "runtime_kind": self.record.get("kind"),
            "unit": self.unit,
            "test_evidence": False,
            "inputs": self.record["inputs"],
            "runtime": (
                str(Path(self.record["environment"]) / "bin/python")
                if self.record["kind"] == "python"
                else "pnpm"
            ),
        }

    def close(self):
        pass


def prepare(operation, controller, principal, state, request):
    unit = task_unit(operation, controller, state, principal, request)
    tooling, root = operation.tooling, Path(operation.envelope["repository"])
    prefix = "" if unit == "." else unit + "/"
    python = (root / prefix / "pyproject.toml").is_file()
    required = (
        [prefix + n for n in ("pyproject.toml", "uv.lock")]
        if python
        else ["package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", prefix + "package.json"]
    )
    if python:
        pending, seen = [unit], set()
        while pending:
            local = pending.pop()
            if local in seen:
                continue
            if len(seen) >= 32:
                raise ValueError("local dependency manifest limit exceeded")
            seen.add(local)
            name = (Path(local) / "pyproject.toml").as_posix()
            project = tomllib.loads(ordinary(root, name).decode())
            required.append(name)
            for entry in project.get("tool", {}).get("uv", {}).get("sources", {}).values():
                if isinstance(entry, dict) and "path" in entry:
                    target = (root / local / entry["path"]).resolve(strict=True)
                    if not target.is_relative_to(root):
                        raise ValueError("local dependency escapes repository")
                    pending.append(target.relative_to(root).as_posix())
    inputs = {name: digest(ordinary(root, name)) for name in set(required)}
    catalog = tooling.catalog()
    tools = catalog["tools"]
    fingerprint = digest({"inputs": inputs, "tools": tools, "capability": catalog["binding"]})
    failures = tooling.directory / "failures.json"
    if failures.is_file():
        failure = json.loads(failures.read_bytes()).get(unit)
        if failure and failure.get("fingerprint") == fingerprint:
            return failure["result"] | {"cached": True, "unit": unit}
    key = (request["task_id"], unit)
    for handle, record in operation.processes.records.items():
        report = record.get("report")
        if (
            isinstance(report, SetupReport)
            and report.unit == unit
            and record["status"] == "Running"
        ):
            return {"handle": handle, "status": "Running", "test_evidence": False}
    index = tooling.directory / "environments.json"
    if index.is_file():
        cached = json.loads(index.read_bytes()).get(unit)
        if cached and cached["inputs"] == inputs and cached.get("tools") == digest(tools):
            return {"status": "Ready", "unit": unit, "test_evidence": False, "cached": True}
    active = sum(
        isinstance(r.get("report"), SetupReport) and r["status"] == "Running"
        for r in operation.processes.records.values()
    )
    active_node = any(
        isinstance(r.get("report"), SetupReport)
        and r["status"] == "Running"
        and r["report"].record["kind"] == "node"
        for r in operation.processes.records.values()
    )
    if active >= 2 or not python and active_node:
        operation.setup_pending[key] = (principal, request)
        return {"status": "Queued", "unit": unit, "test_evidence": False}
    tools = tooling.catalog()["tools"]
    needed = {"uv", "python"} if python else {"node", "pnpm"}
    if not needed <= tools.keys():
        return {
            "status": "Unavailable",
            "runtime_kind": "python" if python else "node",
            "reason": "Required operator runtimes are not installed: "
            + ", ".join(sorted(needed - tools.keys())),
            "test_evidence": False,
        }
    writable = []
    if not python:
        from harness.setup_manifests import node_lock

        node_lock(ordinary(root, "pnpm-lock.yaml"))
        # Only ignored dependency directories get write access. Frozen installs
        # cannot alter package manifests, source, lockfiles or AL authority.
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "ls-files",
                "-z",
                "--",
                "package.json",
                "*/package.json",
            ],
            cwd=root,
            capture_output=True,
            env=tooling.environment(root, operation.directory, prepared=False),
            timeout=10,
            check=True,
        )
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("workspace manifest enumeration exceeds bound")
        for name in result.stdout.decode().split("\0"):
            if not name:
                continue
            from harness.setup_manifests import node_manifest

            node_manifest(ordinary(root, name), root, name)
            directory = (Path(name).parent / "node_modules").as_posix()
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", "--", directory + "/"],
                cwd=root,
                capture_output=True,
                env=tooling.environment(root, operation.directory, prepared=False),
                timeout=10,
            )
            if ignored.returncode:
                raise ValueError("dependency setup requires ignored node_modules directories")
            path = root / directory
            if path.is_symlink():
                raise ValueError("dependency directory alias requires supported reconciliation")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            writable.append(directory)
    profile = operation.profile(writable)
    from harness.execution_isolation import provisioning_profile

    profile = provisioning_profile(profile)
    env = tooling.environment(root / unit, profile.temporary, prepared=False)
    home = Path(profile.temporary) / "installer-home"
    home.mkdir(mode=0o700)
    env.update(
        CI="1",
        HOME=str(home),
        UV_NO_CONFIG="1",
        COREPACK_HOME=str(home / "corepack"),
        NPM_CONFIG_USERCONFIG="/dev/null",
        NPM_CONFIG_GLOBALCONFIG="/dev/null",
    )
    record = {
        "kind": "python" if python else "node",
        "inputs": inputs,
        "setup_id": secrets.token_hex(16),
        "tools": digest(tools),
        "fingerprint": fingerprint,
    }
    if python:
        project = tomllib.loads(ordinary(root, prefix + "pyproject.toml").decode())
        lock = tomllib.loads(ordinary(root, prefix + "uv.lock").decode())
        from harness.setup_manifests import python_lock

        python_lock(lock)
        environment = str(Path(profile.temporary) / "venv")
        sources = [str(root / unit / "src"), str(root / unit)]
        for source in project.get("tool", {}).get("uv", {}).get("sources", {}).values():
            if isinstance(source, dict) and "path" in source:
                path = (root / unit / source["path"]).resolve(strict=True)
                if not path.is_relative_to(root) or path.is_symlink():
                    raise ValueError("local Python dependency escapes task repository")
                sources.extend((str(path / "src"), str(path)))
        env.update(
            UV_PROJECT_ENVIRONMENT=environment,
            UV_PYTHON_INSTALL_DIR=str(Path(profile.temporary) / "python-installations"),
        )
        argv = [
            tools["uv"]["path"],
            "sync",
            "--locked",
            "--no-install-local",
            "--no-build",
            "--no-config",
        ]
        selected = []
        for name in sorted(
            set(project.get("dependency-groups", {})) & {"dev", "test", "tests", "testing"}
        ):
            argv.extend(["--group", name])
            selected.append("group:" + name)
        for name in sorted(
            set(project.get("project", {}).get("optional-dependencies", {}))
            & {"dev", "test", "tests", "testing"}
        ):
            argv.extend(["--extra", name])
            selected.append("extra:" + name)
        record.update(environment=environment, sources=sources, selected=selected)
    else:
        manager = json.loads(ordinary(root, "package.json")).get("packageManager", "")
        if (
            not manager.startswith("pnpm@")
            or not manager[5:].split("+")[0].replace(".", "").isdigit()
        ):
            raise ValueError("project must declare an exact pnpm packageManager version")
        argv = [
            str(tooling.bin / "pnpm"),
            "--filter",
            ".",
            "--filter",
            "." if unit == "." else "./" + unit + "...",
            "install",
            "--frozen-lockfile",
            "--ignore-scripts",
            "--ignore-pnpmfile",
            "--package-import-method=copy",
            "--store-dir",
            str(home / "pnpm-store"),
        ]
    report = SetupReport(tooling, unit, record)
    report.request, report.principal = request, principal
    return operation.processes.start(
        principal["thread_id"],
        profile,
        argv,
        str(root / unit if python else root),
        task=request["task_id"],
        revision=request["expected_revision"],
        timeout=300,
        environment=env,
        report=report,
    )


def auto_prepare(operation, controller, principal, state, task_id):
    """Prepare only the registered writer's concrete units; never scan artifacts."""
    task = state["integration"]["tasks"][task_id]
    root, units = Path(operation.envelope["repository"]), set()
    for name in task["files"]:
        path = root / name
        if path.is_file():
            path = path.parent
        if not path.exists() or path.is_symlink():
            continue
        candidates = [path, *[path / child for child in ("python", "typescript")]]
        for unit in candidates:
            if unit.is_relative_to(root) and (
                (unit / "uv.lock").is_file() or (unit / "package.json").is_file()
            ):
                units.add(unit.relative_to(root).as_posix())
    results = []
    for unit in sorted(units, key=lambda name: (not (root / name / "uv.lock").is_file(), name))[:8]:
        try:
            result = prepare(
                operation,
                controller,
                principal,
                state,
                {
                    "operation": "prepare",
                    "task_id": task_id,
                    "expected_revision": context_revision(state, task_id),
                    "cwd": unit,
                },
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result = {"status": "Unavailable", "reason": str(error), "test_evidence": False}
        operation.setup_outcomes[unit] = result
        results.append({"unit": unit, **result})
    return results
