"""Test seam for AL code facts (#34): answer `alctl run ... -- facts ...` from this checkout's
AL scripts, without an installed launcher or an AL run."""

import os
import subprocess
import sys
from pathlib import Path

from harness.knowledge.al_facts import ALFacts
from harness.knowledge.al_facts import resolve as real_resolve  # noqa: F401  (before patching)

SCRIPTS = Path(__file__).resolve().parents[1] / "al" / "claude" / "skills" / "al" / "scripts"


def environment(index_dir):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_", "AL_"))}
    env["AL_ANALYSIS_INDEX_DIR"] = str(index_dir)
    return env


def runner(index_dir, calls=None):
    def run(argv, *, cwd, capture_output, timeout, stdin):
        assert argv[1:3] == ["run", "--repository"] and "--" in argv
        if calls is not None:
            calls.append(argv)
        arguments = argv[argv.index("--") + 1 :]
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "al_state.py"), *arguments],
            cwd=cwd,
            capture_output=capture_output,
            timeout=timeout,
            stdin=stdin,
            env=environment(index_dir),
        )

    return run


def al_facts(repository, index_dir, calls=None):
    return ALFacts(
        repository, "claude", "al-test-run", launcher="alctl", runner=runner(index_dir, calls)
    )


def source_facts(repository, index_dir):
    """AL code facts answered by `al_code_facts.py` itself (faster than the full CLI)."""

    def run(argv, *, cwd, capture_output, timeout, stdin):
        arguments = argv[argv.index("--") + 1 :]
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "al_code_facts.py"), *arguments],
            cwd=cwd,
            capture_output=capture_output,
            timeout=timeout,
            stdin=stdin,
            env=environment(index_dir),
        )

    return ALFacts(repository, "claude", "al-test-run", launcher="alctl", runner=run)


def direct(repository, index_dir, *arguments):
    """AL's analyser run as its own module (not through the knowledge graph's launcher path)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "al_code_facts.py"), "facts", *arguments],
        cwd=repository,
        capture_output=True,
        timeout=120,
        env=environment(index_dir),
        check=True,
    )
    return result.stdout
