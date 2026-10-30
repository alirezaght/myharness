"""Execution-owned tool selection; agents never inherit the operator's ambient PATH."""

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.command_safety import safe_environment
from harness.execution_contract import canonical, digest
from harness.execution_handoff import durable_file

NAMES = ("python", "python3", "uv", "node", "pnpm")


def identity(path):
    path = Path(path).resolve(strict=True)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        raise ValueError("task tool must be an executable regular file")
    return {
        "path": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "modified": info.st_mtime_ns,
    }


class TaskTooling:
    def __init__(self, directory, repository, binding=None):
        self.directory = Path(directory)
        self.repository = Path(repository).resolve(strict=True)
        self.path = self.directory / "catalog.json"
        if binding is not None:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            tools = {}
            for name in NAMES:
                executable = (
                    getattr(sys, "_base_executable", sys.executable)
                    if name in {"python", "python3"}
                    else shutil.which(name)
                )
                if executable and not Path(executable).resolve().is_relative_to(self.repository):
                    tools[name] = identity(executable)
            self.bin = self.directory / "bin"
            self.bin.mkdir(mode=0o700, exist_ok=True)
            aliases = {}
            for name in NAMES:
                link = self.bin / name
                if link.is_symlink():
                    link.unlink()
                elif link.exists():
                    if not link.is_file() or link.stat().st_nlink != 1:
                        raise ValueError("task tool alias must be supervisor-owned")
                    link.unlink()
                if name in tools:
                    if name in {"python", "python3", "uv"}:
                        program = (
                            f"#!{tools['python']['path']} -I\n"
                            "import sys\n"
                            f"sys.path.insert(0,{str(Path(__file__).resolve().parent.parent)!r})\n"
                            "from harness.task_tooling import exec_selected\n"
                            f"exec_selected({str(self.directory)!r},{str(self.repository)!r},{name!r},sys.argv[1:])\n"
                        )
                        link.write_text(program)
                        link.chmod(0o500)
                        aliases[name] = digest(program.encode())
                    else:
                        link.symlink_to(tools[name]["path"])
                        aliases[name] = "symlink"
            value = {
                "schema_version": 1,
                "binding": binding,
                "repository": str(self.repository),
                "tools": tools,
                "aliases": aliases,
            }
            # A new native attempt may reselect tools, never an in-flight native request.
            if self.path.exists():
                if self.path.is_symlink():
                    raise ValueError("task tooling catalog alias refused")
                self.path.unlink()
            durable_file(self.path, canonical(value).encode())
        self.bin = self.directory / "bin"

    def catalog(self):
        if self.path.is_symlink() or self.path.stat().st_size > 65536:
            raise ValueError("invalid task tooling catalog")
        value = json.loads(self.path.read_bytes())
        if value.get("repository") != str(self.repository):
            raise ValueError("task tooling belongs to another repository")
        for name, observed in value["tools"].items():
            link = self.bin / name
            if name not in NAMES or identity(observed["path"]) != observed:
                raise ValueError("task executable changed; refresh trusted task tooling")
            alias = value["aliases"][name]
            if alias == "symlink":
                valid = link.is_symlink() and identity(link) == observed
            else:
                from harness.template_classification import _ordinary

                valid = not link.is_symlink() and digest(_ordinary(link, 8192)[0]) == alias
            if not valid:
                raise ValueError("task executable changed; refresh trusted task tooling")
        return value

    def inspect(self):
        value = self.catalog()
        return {
            "schema_version": 1,
            "tools": {name: {"available": name in value["tools"]} for name in NAMES},
            "setup_operation": "environment_operation",
            "setup_is_test_evidence": False,
            "credential_environment_inherited": False,
        }

    def environment(self, cwd, temporary, *, prepared=True, source_root=None):
        catalog = self.catalog()
        source_root = Path(source_root or self.repository)
        env = safe_environment() | {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "TMPDIR": str(temporary),
            "UV_CACHE_DIR": str(Path(temporary) / "uv-cache"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        path = self.directory / "environments.json"
        if prepared and path.exists():
            if path.is_symlink() or path.stat().st_size > 262144:
                raise ValueError("invalid task environment index")
            records = json.loads(path.read_bytes())
            relative = Path(cwd).resolve().relative_to(self.repository).as_posix()
            matches = [
                key
                for key in records
                if key == "." or key == relative or relative.startswith(key.rstrip("/") + "/")
            ]
            unit = max(matches, key=len) if matches else None
            record = records[unit] if unit is not None else None
            if record:
                if record.get("tools") != digest(catalog["tools"]):
                    raise ValueError("prepared runtime changed; prepare current dependencies")
                for name, expected in record["inputs"].items():
                    if digest((source_root / name).read_bytes()) != expected:
                        raise ValueError(
                            "prepared dependencies are stale; prepare the current lock"
                        )
                env.update(HARNESS_TOOLING_UNIT=unit, HARNESS_TOOLING_SOURCE_ROOT=str(source_root))
                if record["kind"] == "python":
                    venv = Path(record["environment"])
                    env.update(
                        PATH=str(venv / "bin") + os.pathsep + env["PATH"],
                        VIRTUAL_ENV=str(venv),
                        UV_PROJECT_ENVIRONMENT=str(venv),
                        UV_NO_SYNC="1",
                        PYTHONPATH=os.pathsep.join(
                            str(source_root / Path(p).relative_to(self.repository))
                            for p in record["sources"]
                        ),
                    )
        for location in (Path(cwd) / "node_modules/.bin", source_root / "node_modules/.bin"):
            if location.is_dir():
                env["PATH"] = env["PATH"] + os.pathsep + str(location)
        return env

    def failure(self, unit, record, result):
        self._update(
            "failures.json", unit, {"fingerprint": record.get("fingerprint"), "result": result}
        )

    def ready(self, unit, record):
        self._update("environments.json", unit, record)

    def _update(self, filename, unit, record):
        descriptor = os.open(
            self.directory / "environments.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.directory / filename
            if path.is_symlink():
                raise ValueError("task environment index alias refused")
            records = json.loads(path.read_bytes()) if path.exists() else {}
            records[unit] = record
            fd, temporary = tempfile.mkstemp(prefix=".environments-", dir=self.directory)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(canonical(records))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)


def exec_selected(directory, repository, name, arguments):
    """Nested package scripts select the same private environment by actual cwd."""
    try:
        tooling = TaskTooling(directory, repository)
        catalog = tooling.catalog()
        cwd = Path.cwd().resolve()
        if cwd.is_relative_to(tooling.repository):
            env = tooling.environment(cwd, os.environ.get("TMPDIR", "/tmp"))
        else:
            source_root = Path(os.environ["HARNESS_TOOLING_SOURCE_ROOT"]).resolve(strict=True)
            if not source_root.is_relative_to(
                tooling.directory.parent / "operations"
            ) or not cwd.is_relative_to(source_root):
                raise ValueError("task tool checkout is outside owned check storage")
            env = tooling.environment(
                tooling.repository / os.environ["HARNESS_TOOLING_UNIT"],
                os.environ.get("TMPDIR", "/tmp"),
                source_root=source_root,
            )
        # Retain only the existing clean command environment, never caller auth.
        for key in (
            "PATH",
            "TMPDIR",
            "UV_CACHE_DIR",
            "PYTHONDONTWRITEBYTECODE",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "UV_NO_SYNC",
            "PYTHONPATH",
            "HARNESS_TOOLING_UNIT",
            "HARNESS_TOOLING_SOURCE_ROOT",
        ):
            if key in env:
                os.environ[key] = env[key]
            else:
                os.environ.pop(key, None)
        target = catalog["tools"][name]["path"]
        if name in {"python", "python3"} and env.get("VIRTUAL_ENV"):
            target = str(Path(env["VIRTUAL_ENV"]) / "bin/python")
        if name == "uv" and arguments and arguments[0] == "sync":
            if not env.get("VIRTUAL_ENV"):
                raise ValueError("locked dependencies must be prepared by environment_operation")
            # Validate the prepared registry environment; local packages are
            # explicitly loaded through the recorded source paths, never built
            # with the installer's network capability.
            command = [target, *arguments, "--check", "--no-install-local", "--no-config"]
            result = subprocess.run(command, check=False)
            raise SystemExit(0 if result.returncode == 0 else 126)
        if name == "uv" and arguments and arguments[0] == "run" and not env.get("VIRTUAL_ENV"):
            raise ValueError("prepare this unit's locked dependencies before uv run")
        os.execve(target, [target, *arguments], dict(os.environ))
    except (OSError, ValueError, KeyError) as error:
        print("Harness task environment unavailable: " + str(error), file=sys.stderr)
        raise SystemExit(126) from None
