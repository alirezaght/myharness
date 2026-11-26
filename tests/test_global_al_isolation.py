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


def test_leaf_undo_cannot_remove_fixture_owned_environment_or_resolver(tmp_path):
    import json
    import subprocess
    import sys
    from textwrap import dedent

    root = Path(__file__).resolve().parents[1]
    child = tmp_path / "fixture-undo-probe"
    child.mkdir()
    home = child / "fallback-home"
    home.mkdir()
    temporary = child / "tmp"
    temporary.mkdir()
    teardown = child / "teardown.json"
    (child / "conftest.py").write_text(
        dedent(
            """\
            import json
            import os
            from pathlib import Path

            import pytest

            from tests.conftest import (
                al_code_facts as al_code_facts,
                global_alctl_shadow as global_alctl_shadow,
                isolated_home as isolated_home,
            )

            KEYS = (
                "HARNESS_HOME", "HARNESS_DB", "AL_SERVICE", "AL_DISTRIBUTION_STATE",
                "AL_RUNTIME_DIR", "HARNESS_ALCTL", "PATH",
            )


            @pytest.fixture(scope="session", autouse=True)
            def observe_fixture_teardown():
                from harness.knowledge import al_facts

                before = {key: os.environ.get(key) for key in KEYS}
                resolver = al_facts.resolve
                yield
                result = {
                    "environment_restored": before == {key: os.environ.get(key) for key in KEYS},
                    "resolver_restored": al_facts.resolve is resolver,
                }
                Path(os.environ["ISSUE107_TEARDOWN"]).write_text(json.dumps(result))
                assert all(result.values()), result
            """
        )
    )
    (child / "test_fixture_undo.py").write_text(
        dedent(
            """\
            import os
            import shutil

            from harness.knowledge import al_facts

            from conftest import KEYS


            def test_leaf_undo_preserves_fixture_owned_protection(
                tmp_path, isolated_home, al_code_facts, monkeypatch
            ):
                assert os.environ["HARNESS_HOME"] == str(isolated_home)
                assert isolated_home == tmp_path / "home"
                assert "HARNESS_DB" not in os.environ
                assert os.environ["AL_SERVICE"] == "0"
                assert os.environ["AL_DISTRIBUTION_STATE"] == str(
                    tmp_path / "al-distribution" / "registry"
                )
                assert "AL_RUNTIME_DIR" not in os.environ
                assert os.environ["HARNESS_ALCTL"] == str(tmp_path / "no-global-alctl" / "alctl")
                assert shutil.which("alctl") is None
                assert al_facts.resolve.__module__ == "tests.conftest"
                protected = {key: os.environ.get(key) for key in KEYS}
                resolver = al_facts.resolve
                monkeypatch.setenv("ISSUE107_LEAF_PATCH", "temporary")
                monkeypatch.undo()
                lost = [key for key in KEYS if os.environ.get(key) != protected[key]]
                assert not lost and al_facts.resolve is resolver, {
                    "lost_fixture_environment": lost,
                    "fixture_resolver_lost": al_facts.resolve is not resolver,
                }
                assert "ISSUE107_LEAF_PATCH" not in os.environ
            """
        )
    )
    # Start from owned fallbacks, never from ambient credentials or user state.
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "HARNESS_HOME": str(home / "harness"),
        "HARNESS_DB": str(home / "fallback.sqlite"),
        "AL_SERVICE": "fallback",
        "AL_DISTRIBUTION_STATE": str(home / "al-registry"),
        "AL_RUNTIME_DIR": str(home / "al-runtime"),
        "HARNESS_ALCTL": str(home / "missing-alctl"),
        "PATH": os.defpath,
        "TMPDIR": str(temporary),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "PYTHONPATH": str(root),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "ISSUE107_TEARDOWN": str(teardown),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(child / "pytest-tmp"),
            str(child / "test_fixture_undo.py"),
        ],
        cwd=child,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(teardown.read_text()) == {
        "environment_restored": True,
        "resolver_restored": True,
    }
