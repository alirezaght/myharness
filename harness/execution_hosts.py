"""Thin native CLI adapters. Host JSON is never semantic AL evidence."""

import os
import shutil
import subprocess
from pathlib import Path

from harness.execution_contract import digest, loads


def package_fingerprint(package):
    directory = Path(package)
    files = [directory / "SKILL.md", *sorted((directory / "scripts").glob("*.py"))]
    return digest({str(path.relative_to(directory)): digest(path.read_bytes()) for path in files})


def installed_package(host, repository):
    # Native per-project installation wins over the user's selected platform skill.
    if host == "claude":
        roots = [
            Path(repository) / ".claude" / "skills" / "al",
            Path.home() / ".claude" / "skills" / "al",
        ]
    elif host == "codex":
        roots = [
            Path(repository) / ".agents" / "skills" / "al",
            Path(repository) / ".codex" / "skills" / "al",
            Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills" / "al",
        ]
    else:
        raise ValueError("unsupported native host")
    executable = shutil.which(host)
    if executable is None:
        raise ValueError(
            f"{host} CLI unavailable; install/authenticate the selected host explicitly"
        )
    package = next((p for p in roots if (p / "SKILL.md").is_file()), None)
    if package is None:
        raise ValueError(f"{host} installed AL skill unavailable")
    script = package / "scripts" / "al_state.py"
    if (
        not (package / "scripts" / "al_events.py").is_file()
        or not (package / "scripts" / "al_execution_tasks.py").is_file()
    ):
        raise ValueError(
            f"{host} installed AL lacks al-execution-v1; source changes do not upgrade it"
        )
    # Package files and capability command establish protocol support, not provider access.
    import sys

    result = subprocess.run(
        [sys.executable, str(script), "integration", "capabilities"],
        cwd=repository,
        capture_output=True,
        timeout=10,
    )
    if (
        result.returncode
        or len(result.stdout) > 65536
        or loads(result.stdout).get("protocol") != "al-execution-v1"
    ):
        raise ValueError("installed AL capability check failed")
    role_root = Path(repository) / (".claude/agents/al" if host == "claude" else ".codex/agents")
    global_roles = Path.home() / (".claude/agents/al" if host == "claude" else ".codex/agents")
    suffix = ".md" if host == "claude" else ".toml"
    roles = (
        "al-architect",
        "al-architect-fallback",
        "al-designer",
        "al-designer-fallback",
        "al-engineer",
        "al-builder",
        "al-scout",
    )
    if any(
        not any((base / (role + suffix)).is_file() for base in (role_root, global_roles))
        for role in roles
    ):
        raise ValueError("installed AL role resources unavailable")
    return {
        "host": host,
        "executable": executable,
        "package": str(package.resolve()),
        "script": str(script.resolve()),
        "sha256": package_fingerprint(package),
    }


def start_argv(host, executable, repository, native_id):
    if host == "claude":
        return [
            executable,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--session-id",
            native_id,
        ]
    if host == "codex":
        return [
            executable,
            "exec",
            "--json",
            "--cd",
            str(repository),
            "--sandbox",
            "workspace-write",
            "-",
        ]
    raise ValueError("unsupported host")


def resume_argv(host, executable, repository, native_id):
    if not isinstance(native_id, str) or not native_id.strip() or native_id.startswith("-"):
        raise ValueError("explicit recorded native identity required")
    if host == "claude":
        return [
            executable,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--resume",
            native_id,
        ]
    if host == "codex":
        return [executable, "exec", "resume", "--json", native_id, "-"]
    raise ValueError("unsupported host")


def native_identity(host, raw):
    record = loads(raw)
    if not isinstance(record, dict):
        raise ValueError("native stream record must be an object")
    value = None
    if host == "claude" and record.get("type") in {"system", "result"}:
        value = record.get("session_id")
    elif host == "codex" and record.get("type") == "thread.started":
        value = record.get("thread_id")
    if value is not None:
        import uuid

        return str(uuid.UUID(value))
    return None


def native_diagnostic(host, raw):
    record = loads(raw)
    if not isinstance(record, dict):
        raise ValueError("native diagnostic must be an object")
    if (
        host == "claude"
        and record.get("type") == "result"
        and (record.get("is_error") is True or str(record.get("subtype", "")).startswith("error"))
    ):
        errors = record.get("errors", [])
        return (
            "Claude "
            + str(record.get("subtype", "failed"))
            + ": "
            + "; ".join(str(e) for e in errors)[:4096]
        )
    if host == "codex" and record.get("type") in {"error", "thread.failed", "turn.failed"}:
        error = record.get("error", record.get("message", "native host failed"))
        return (
            "Codex: "
            + (
                str(error.get("message", "native host failed"))
                if isinstance(error, dict)
                else str(error)
            )[:4096]
        )
    return None
