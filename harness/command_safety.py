"""Closed read grammars; unknown effects require exact operation authority."""

import os
import re
import subprocess
from pathlib import Path

from harness.execution_contract import digest

_GIT = {
    "status": {"--porcelain", "--short", "--branch"},
    "rev-parse": {"HEAD", "--show-toplevel", "--verify"},
    "log": {"--oneline", "--all", "--decorate", "--no-decorate"},
    "show": {"--stat", "--oneline", "--no-patch", "HEAD"},
    "diff": {"--stat", "--name-only", "--cached", "--", "HEAD"},
}


def _git_revision(value):
    if value == "HEAD" or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        return True
    if not re.fullmatch(r"refs/(heads|remotes|tags)/[A-Za-z0-9][A-Za-z0-9._/-]*", value):
        return False
    return not any(
        part in {"", ".", ".."}
        or part.startswith(".")
        or part.endswith((".", ".lock"))
        or ".." in part
        for part in value.split("/")
    )


def _git_log_args(args):
    revisions = 0
    for arg in args:
        if arg in _GIT["log"]:
            continue
        if re.fullmatch(r"-n[0-9]{1,3}", arg) and 1 <= int(arg[2:]) <= 100:
            continue
        endpoints = arg.split("..")
        if len(endpoints) not in {1, 2} or not all(_git_revision(v) for v in endpoints):
            return False
        revisions += 1
        if revisions > 1:
            return False
    return True


def normalized_request(request):
    if set(request) - {"argv", "cwd", "environment", "effects", "target", "execution_id"}:
        raise ValueError("unknown command request fields")
    argv = request.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > 100
        or any(not isinstance(a, str) or not a or len(a) > 8192 or "\x00" in a for a in argv)
    ):
        raise ValueError("bounded structured argv required")
    cwd = Path(request["cwd"]).resolve(strict=True)
    environment = request.get("environment", {})
    if not isinstance(environment, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or "\x00" in k + v
        for k, v in environment.items()
    ):
        raise ValueError("invalid explicit command environment")
    return {
        "argv": argv,
        "cwd": str(cwd),
        "environment": environment,
        "effects": request.get("effects", []),
        "target": request.get("target", str(cwd)),
        "execution_id": request.get("execution_id"),
    }


def classify_command(request, context=None):
    value = normalized_request(request)
    argv = value["argv"]
    if value["environment"]:
        return {
            "decision": "approval_required",
            "reason": "environment overrides require authority",
            "request": value,
            "digest": digest(value),
        }
    safe = False
    if argv[0] == "rg":
        from harness.command_discovery import discovery_argv

        try:
            value["argv"] = discovery_argv(
                argv, value["cwd"], (context or {}).get("repository", value["cwd"])
            )
            return {
                "decision": "read_only",
                "reason": "bounded repository discovery",
                "request": value,
                "digest": digest(value),
            }
        except (ValueError, OSError) as error:
            return {
                "decision": "approval_required",
                "reason": str(error),
                "request": value,
                "digest": digest(value),
            }
    if argv[0] == "git" and len(argv) >= 2 and argv[1] in _GIT:
        args = argv[2:]
        safe = _git_log_args(args) if argv[1] == "log" else all(a in _GIT[argv[1]] for a in args)
    elif argv[:2] == ["docker", "ps"]:
        safe = argv[2:] in ([], ["--all"], ["--format", "{{json .}}"])
    elif argv[:3] == ["docker", "inspect", "--type=container"]:
        safe = len(argv) == 4 and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", argv[3]))
    elif argv[:2] == ["launchctl", "print"]:
        safe = len(argv) == 3 and bool(re.fullmatch(r"system/[A-Za-z0-9_.-]{1,160}", argv[2]))
    elif argv[0] == "pwd" and len(argv) == 1:
        safe = True
    return {
        "decision": "read_only" if safe else "approval_required",
        "reason": "closed read grammar" if safe else "unknown or mutating effects",
        "request": value,
        "digest": digest(value),
    }


def safe_environment():
    return {
        "PATH": os.defpath,
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
    }


def git_argv(arguments):
    return [
        "git",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=",
        "-c",
        "core.attributesFile=/dev/null",
        *arguments,
    ]


def git_read(root, arguments):
    request = {"argv": ["git", *arguments], "cwd": str(root)}
    if classify_command(request)["decision"] != "read_only":
        raise ValueError("Git diagnostic not admitted by closed grammar")
    args = list(arguments)
    if args[0] in {"diff", "show", "log"}:
        args[1:1] = ["--no-ext-diff", "--no-textconv"]
    result = subprocess.run(
        git_argv(args), cwd=root, env=safe_environment(), capture_output=True, timeout=10
    )
    if result.returncode or len(result.stdout) > 1048576:
        raise ValueError("bounded Git read failed")
    return result.stdout.decode(errors="replace")
