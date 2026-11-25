"""No test reaches the global AL launcher or its real run registry (#37)."""

import os
from pathlib import Path

import pytest

from harness.execution_hosts import managed_launcher
from tests.al_launcher import path_without_global_alctl


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_the_session_hides_the_global_launcher_and_registry(tmp_path):
    import shutil

    assert shutil.which("alctl") is None
    with pytest.raises(ValueError, match="launcher unavailable"):
        managed_launcher()
    assert Path(os.environ["AL_DISTRIBUTION_STATE"]).is_relative_to(tmp_path)


def test_harness_alctl_names_the_launcher_when_none_is_on_path(tmp_path, monkeypatch):
    launcher = _executable(tmp_path / "private" / "alctl")
    monkeypatch.setenv("HARNESS_ALCTL", str(launcher))
    assert managed_launcher() == str(launcher)
    on_path = _executable(tmp_path / "bin" / "alctl")
    monkeypatch.setenv("PATH", str(on_path.parent) + os.pathsep + os.environ["PATH"])
    assert managed_launcher() == str(on_path)


def test_path_mirror_drops_only_a_global_alctl(tmp_path, monkeypatch):
    import tempfile

    outside = Path.home() / ".cache" / "harness-test-never-created"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    global_bin = tmp_path / "global-bin"
    _executable(global_bin / "alctl")
    _executable(global_bin / "uv")
    fixture_bin = tmp_path / "tmp" / "fixture-bin"
    _executable(fixture_bin / "alctl")
    plain = tmp_path / "plain"
    plain.mkdir()
    shadow = path_without_global_alctl(tmp_path / "tmp" / "mirrors")
    entries = shadow(os.pathsep.join(map(str, (fixture_bin, global_bin, plain, outside)))).split(
        os.pathsep
    )
    assert entries[0] == str(fixture_bin) and entries[2:] == [str(plain), str(outside)]
    mirror = Path(entries[1])
    assert sorted(p.name for p in mirror.iterdir()) == ["uv"]
    assert (mirror / "uv").resolve() == (global_bin / "uv").resolve()
