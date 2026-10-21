"""Pin execution imports to the running harness, independently of target repository cwd."""

import os
import sys
from pathlib import Path

from harness.execution_contract import BOOTSTRAP


def module_argv(module):
    if module not in {"harness.execution_authority", "harness.execution_worker"}:
        raise ValueError("unsupported trusted execution module")
    return [sys.executable, "-I", "-c", BOOTSTRAP, module]


def execution_environment():
    return dict(os.environ, HARNESS_AUTHORITY_ROOT=str(Path(__file__).resolve().parent.parent))
