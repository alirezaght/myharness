"""Owned wheel-only Harness environments and nonlaunching neutral verification."""

import json
import os
from pathlib import Path
import shlex
import stat

from .registry import canonical, digest, write_json
from .validator_origin import capture, safe
from .workspace_tools import environment, run_tool, tool_identity, verify_tool
from .workspace_wheel import inspect_wheel


def inventory(root, interpreter):
    root = safe(root)
    result = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        state = path.lstat()
        if stat.S_ISLNK(state.st_mode):
            resolved = path.resolve(strict=True)
            internal = resolved.is_relative_to(root)
            if not internal and (
                path.parent != root / "bin"
                or not path.name.startswith("python")
                or resolved != Path(interpreter["canonical_executable"])
            ):
                raise ValueError("unexpected installed environment symlink")
            result[name] = {
                "type": "symlink",
                "target": os.readlink(path),
                "resolved": str(resolved),
            }
        elif stat.S_ISREG(state.st_mode):
            _, observed = capture(path)
            result[name] = dict(observed, type="file")
        elif not stat.S_ISDIR(state.st_mode):
            raise ValueError("special installed environment member")
    return result


def launcher(directory):
    python = safe(directory) / "bin/python"
    return ("#!/bin/sh\nexec " + shlex.quote(str(python)) + ' -I -B -m harness.cli "$@"\n').encode()


def create_environment(wheel, directory):
    """Internal preparation from the current build result, never a public trust seam."""
    directory = safe(directory)
    if directory.exists():
        raise ValueError("Harness environment collision or pending preparation")
    path = directory / "environment"
    tools = directory / "tools"
    python, uv = tool_identity("python"), tool_identity("uv")
    artifact = wheel["wheel"]
    observed = inspect_wheel(Path(artifact["path"]), artifact["version"])
    if observed != {k: v for k, v in artifact.items() if k != "path"}:
        raise ValueError("prepared Harness wheel changed")
    journal = {
        "schema_version": 1,
        "kind": "harness-environment-preparation-v1",
        "wheel_digest": wheel["digest"],
        "status": "PREPARING",
        "steps": [],
    }
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_json(directory / "operation.json", journal)
    try:
        tools.mkdir(mode=0o700)
        env = environment(directory / "execution-environment")
        journal["steps"].append(
            run_tool(
                python,
                ["-I", "-S", "-B", "-m", "venv", "--without-pip", str(path)],
                cwd=directory,
                env=env,
                directory=tools / "venv",
            )
        )
        write_json(directory / "operation.json", journal)
        journal["steps"].append(
            run_tool(
                uv,
                [
                    "pip",
                    "install",
                    "--python",
                    str(path / "bin/python"),
                    "--offline",
                    "--no-index",
                    "--no-deps",
                    "--no-config",
                    artifact["path"],
                ],
                cwd=directory,
                env=env,
                directory=tools / "install",
            )
        )
        write_json(directory / "operation.json", journal)
        # The neutral directory is outside source and the installed module lookup is isolated.
        neutral = directory / "neutral"
        neutral.mkdir(mode=0o700)
        installed_python = dict(python, executable=str(path / "bin/python"), kind="venv-python")
        checks = [
            ("-I", "-B", "-m", "harness.cli", "--version"),
            ("-I", "-B", "-m", "harness.cli", "--help"),
            (
                "-I",
                "-B",
                "-c",
                'import importlib.util,json;print(json.dumps({"origin":importlib.util.find_spec("harness").origin}))',
            ),
        ]
        for index, argv in enumerate(checks):
            step = run_tool(
                installed_python,
                argv,
                cwd=neutral,
                env=env,
                directory=tools / ("verify-" + str(index)),
            )
            journal["steps"].append(step)
        origin = json.loads((tools / "verify-2/output.log").read_bytes())["origin"]
        if not Path(origin).resolve().is_relative_to(path.resolve()):
            raise ValueError("Harness module did not resolve to owned environment")
        if inspect_wheel(Path(artifact["path"]), artifact["version"]) != observed:
            raise ValueError("Harness wheel moved during environment preparation")
        files = inventory(path, python)
        value = {
            "schema_version": 1,
            "kind": "managed-harness-environment-v1",
            "directory": str(path),
            "wheel_digest": wheel["digest"],
            "wheel_sha256": artifact["sha256"],
            "interpreter": python,
            "module_origin": origin,
            "files": files,
            "verification": journal["steps"][2:],
        }
        value["digest"] = digest(canonical(value))
        write_json(directory / "receipt.json", value)
        journal.update(status="COMPLETE", receipt_digest=value["digest"])
        write_json(directory / "operation.json", journal)
        return value
    except BaseException as exc:
        journal.update(status="PENDING", error=type(exc).__name__ + ": " + str(exc))
        write_json(directory / "operation.json", journal)
        raise


def verify_environment(value):
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "managed-harness-environment-v1"
        or value.get("digest")
        != digest(canonical({k: v for k, v in value.items() if k != "digest"}))
    ):
        raise ValueError("invalid Harness environment receipt")
    verify_tool(value["interpreter"])
    path = safe(value["directory"])
    if inventory(path, value["interpreter"]) != value["files"]:
        raise ValueError("managed Harness environment drift")
    return value
