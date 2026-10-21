"""Isolated external native CLI for approval tests and demos.

The fixture crosses the host executable boundary but emits no AL task work. The
real harness worker, installed AL package adapter and approval lifecycle remain
under test.
"""

import os
from pathlib import Path

_CLAUDE = """#!/usr/bin/env python3
import json
import sys
import uuid

args = sys.argv
native_id = args[args.index("--session-id") + 1]
uuid.UUID(native_id)
print(json.dumps({"type": "system", "session_id": native_id}), flush=True)
print(json.dumps({"type": "result", "session_id": native_id, "is_error": False}), flush=True)
"""


def install_fake_claude(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "claude"
    executable.write_text(_CLAUDE, encoding="utf-8")
    executable.chmod(0o700)
    return executable


def isolated_provider_environment(env: dict[str, str], root: Path) -> dict[str, str]:
    """Return a child env with a temp home and our only allowed Claude executable."""
    bin_dir = root / "native-provider-bin"
    install_fake_claude(bin_dir)
    result = dict(env)
    result["HOME"] = str(root / "native-provider-home")
    result["PATH"] = os.pathsep.join((str(bin_dir), result.get("PATH", "")))
    return result
