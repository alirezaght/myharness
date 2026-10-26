"""Checks get a narrower sandbox than the trusted AL ledger process that records them."""

import json
import os
import secrets
import sys
from pathlib import Path

from harness.command_safety import safe_environment
from harness.execution_isolation import operation_exec, operation_popen, prepare_operation_isolation


def check_profile(context_path, cwd):
    path = Path(context_path)
    if path.is_symlink() or path.stat().st_nlink != 1 or path.stat().st_size > 65536:
        raise OSError("invalid protected check context")
    context = json.loads(path.read_bytes())
    handoff = Path(context["handoff"])
    if not path.resolve().is_relative_to(handoff.parent / "operations"):
        raise OSError("check context is outside its execution")
    temporary = Path(context["temporary"])
    cwd = Path(cwd or context["repository"]).resolve(strict=True)
    original = Path(context["repository"])
    if not (cwd.is_relative_to(original) or cwd.is_relative_to(temporary)):
        raise OSError("check cwd is outside its approved repository or disposable checkout")
    profile = prepare_operation_isolation(
        cwd,
        writable=[],
        protected=context["protected"] + [str(original / ".git"), context["al_session"]],
        hidden=context["hidden"] + [str(original / n) for n in (".codex", ".claude", ".agents")],
        directory=temporary / ("check-" + secrets.token_hex(12)),
    )
    environment = safe_environment() | {
        "PATH": os.environ.get("PATH", os.defpath),
        "TMPDIR": profile.temporary,
        "UV_CACHE_DIR": profile.temporary + "/uv-cache",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return profile, environment


def check_popen(context_path, argv, **kwargs):
    profile, environment = check_profile(context_path, kwargs.get("cwd"))
    kwargs.pop("env", None)
    return operation_popen(profile, argv, env=environment, **kwargs)


def main():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        profile, environment = check_profile(sys.argv[1], os.getcwd())
        operation_exec(profile, sys.argv[2:], environment)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Harness check containment refused: " + str(error), file=sys.stderr)
        raise SystemExit(126) from None
