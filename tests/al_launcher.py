"""A real managed `alctl` launcher for Harness tests that drive AL runs.

New AL runs are admitted only through the protected launcher, which pins the
runtime and keeps run state in the repository's own `.<host>/al-runtime`.
"""

import subprocess
import sys
from pathlib import Path


def bootstrap_alctl(base: Path, destination: Path | None = None, env: dict | None = None) -> Path:
    from al.tests.conftest import copy_al_source

    # The installer refuses symlinked locations (e.g. macOS /var -> /private/var).
    base = Path(base).resolve()
    destination = Path(destination or base / "alctl-bin" / "alctl").resolve()
    source = copy_al_source(base / "alctl-source" / "al")
    argv = [sys.executable, "-I", "-S", "-B", str(source / "alctl.py"), "bootstrap"]
    argv += ["--source-root", str(source), "--destination", str(destination)]
    argv += ["--state-root", str(base / "installer")]
    result = subprocess.run(argv, cwd=base, env=env, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError("alctl bootstrap failed: " + result.stdout + result.stderr)
    return destination


def al_command(alctl, host, repository, session, *arguments, session_directory=False) -> list[str]:
    return [
        str(alctl),
        "run",
        "--repository",
        str(Path(repository).resolve()),
        "--platform",
        host,
        "--session",
        session,
        *(["--session-directory"] if session_directory else []),
        "--",
        *arguments,
    ]


def path_without_global_alctl(base: Path):
    """Return `shadow(PATH)`: PATH with each directory that holds a global `alctl`
    replaced by a mirror of its other entries (#37).

    A launcher under the temporary directory was bootstrapped by a test and stays."""
    import hashlib
    import os
    import tempfile

    temporary = [
        Path(root).resolve() for root in {tempfile.gettempdir(), str(base)} if Path(root).exists()
    ]
    mirrors: dict[str, str] = {}

    def mirror(entry: str) -> str:
        directory = Path(entry)
        launcher = directory / "alctl"
        if not entry or not os.path.lexists(launcher):
            return entry
        resolved = directory.resolve()
        if any(resolved.is_relative_to(root) for root in temporary):
            return entry
        if entry not in mirrors:
            target = Path(base) / hashlib.sha256(entry.encode()).hexdigest()[:16]
            target.mkdir(parents=True, exist_ok=True)
            for child in directory.iterdir():
                if child.name != "alctl" and not os.path.lexists(target / child.name):
                    (target / child.name).symlink_to(child)
            mirrors[entry] = str(target)
        return mirrors[entry]

    def shadow(path: str) -> str:
        return os.pathsep.join(mirror(entry) for entry in path.split(os.pathsep))

    return shadow
