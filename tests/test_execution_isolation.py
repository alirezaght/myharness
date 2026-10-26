"""Capability proof uses actual sandbox-exec, including descendants."""

import subprocess
import sys
from pathlib import Path

import pytest

from harness.execution_isolation import contained_argv, prepare_isolation, probe_isolation

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS whole-process compatibility sandbox"
)


def test_actual_native_boundary(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    handoff, database = sealed / "handoff.json", sealed / "state.db"
    handoff.write_text("sealed")
    database.write_text("authority")
    runtime = repository / ".codex/al-runtime"
    runtime.mkdir(parents=True)
    source = runtime / "exact.json"
    source.write_text("mutable")
    sibling = runtime / "other.json"
    sibling.write_text("protected")
    binding = sealed / "binding.json"
    binding.write_text("protected")
    lock = sealed / "binding.lock"
    lock.touch()
    profile = prepare_isolation(
        {"handoff_path": str(handoff), "al_session_id": "exact"},
        {"repository": str(repository), "database": str(database)},
        {"source": str(source), "coordination_lock": str(lock)},
        {"port": 54321},
    )
    assert probe_isolation(profile)["status"] == "verified"
    for path in (handoff, database, sibling, binding):
        result = subprocess.run(
            contained_argv(
                profile,
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path;Path(__import__('sys').argv[1]).write_text('escape')",
                    str(path),
                ],
            ),
            capture_output=True,
        )
        assert result.returncode != 0
        assert path.read_text() in {"sealed", "authority", "protected"}
    for _ in range(2):  # Same boundary on resume.
        result = subprocess.run(
            contained_argv(
                profile,
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path;Path(__import__('sys').argv[1]).write_text('ok')",
                    str(source),
                ],
            ),
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
    assert Path(profile.policy).is_file()
