"""Closed build environment and identity-bound owned local tool invocations."""

import os
from pathlib import Path
import shutil
import sys

from .owned_process import execute
from .registry import canonical, digest, read_json, write_json
from .validator_origin import interpreter_identity, safe
from .workspace_executable import executable_state


def tool_identity(name):
    if name == "python":
        return dict(interpreter_identity(), kind="python")
    if name != "uv":
        raise ValueError("unsupported workspace tool")
    found = shutil.which("uv")
    if not found:
        raise ValueError("workspace build requires the actual uv executable")
    path = safe(Path(found).resolve(strict=True))
    state = executable_state(path)
    if not state["mode"] & 0o111:
        raise ValueError("workspace tool is not executable")
    return dict(state, executable=str(path), kind="uv")


def verify_tool(identity):
    path = identity.get("canonical_executable", identity["executable"])
    if Path(identity["executable"]).resolve(strict=True) != Path(path):
        raise ValueError("workspace tool invocation path changed")
    state = executable_state(path)
    if state != {k: identity[k] for k in ("sha256", "mode")}:
        raise ValueError("workspace tool bytes/mode changed")
    if identity["kind"] == "python" and identity != dict(interpreter_identity(), kind="python"):
        raise ValueError("workspace Python invocation identity changed")


def environment(directory):
    directory = safe(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = {name: directory / name for name in ("home", "tmp", "cache")}
    for path in paths.values():
        path.mkdir(mode=0o700)
    return {
        "HOME": str(paths["home"]),
        "TMPDIR": str(paths["tmp"]),
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "UV_CACHE_DIR": str(paths["cache"]),
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_OFFLINE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


def run_tool(tool, arguments, *, cwd, env, directory, timeout=180):
    verify_tool(tool)
    root = safe(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    record = {
        "schema_version": 1,
        "kind": "workspace-tool-execution-v1",
        "tool": tool,
        "argv": [tool["executable"], *map(str, arguments)],
        "cwd": str(safe(cwd)),
        "environment": {k: digest(v.encode()) for k, v in sorted(env.items())},
        "status": "RUNNING",
    }
    write_json(root / "tool.json", record)
    try:
        with (root / "output.log").open("wb") as output:
            owner = execute(
                record["argv"],
                cwd=cwd,
                env=env,
                output=output,
                directory=root / "ownership",
                assignment=digest(canonical(record)),
                timeout=timeout,
            )
        verify_tool(tool)
        journal = read_json(root / "ownership/process-journal.json")
        if journal["status"] != "FINISHED" or not journal["cleanup"]["complete"]:
            raise ValueError("workspace tool cleanup incomplete")
        record.update(
            status="FINISHED",
            returncode=owner.process.returncode,
            ownership=owner.record(),
            cleanup=journal["cleanup"],
            output_sha256=digest((root / "output.log").read_bytes()),
        )
        write_json(root / "tool.json", record)
        if owner.process.returncode:
            raise ValueError("workspace tool failed; retained output: " + str(root / "output.log"))
        return record
    except BaseException as exc:
        record.update(status="PENDING", error=type(exc).__name__ + ": " + str(exc))
        write_json(root / "tool.json", record)
        raise
