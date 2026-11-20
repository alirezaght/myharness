"""Public fixture and paired-wheel paths preserve source materialization boundaries."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from al.installer import workspace_tools as T
from al.installer import workspace_wheel as W
from al.installer.registry import digest
from al.tests.conftest import copy_al_source

ROOT = Path(__file__).resolve().parents[1]


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def test_two_real_al_fixtures_isolate_source_mutation_and_deletion(tmp_path):
    source = ROOT / "al"
    first = copy_al_source(tmp_path / "fixtures/first/al")
    sibling = copy_al_source(tmp_path / "fixtures/sibling/al")
    name = "alctl.py"
    original = (source / name).read_bytes()
    original_mode = (source / name).stat().st_mode & 0o7777
    source_member, first_member, sibling_member = (
        source / name,
        first / name,
        sibling / name,
    )
    assert len({_identity(source_member), _identity(first_member), _identity(sibling_member)}) == 3
    assert first_member.read_bytes() == sibling_member.read_bytes() == original
    assert first_member.stat().st_mode & 0o7777 == sibling_member.stat().st_mode & 0o7777

    first_member.write_bytes(b"fixture-only mutation")
    assert source_member.read_bytes() == sibling_member.read_bytes() == original
    first_member.write_bytes(b"")
    assert source_member.read_bytes() == sibling_member.read_bytes() == original
    first_member.chmod(0o600)
    assert source_member.stat().st_mode & 0o7777 == original_mode
    assert sibling_member.stat().st_mode & 0o7777 == original_mode
    first_member.unlink()
    assert not first_member.exists()
    assert source_member.read_bytes() == sibling_member.read_bytes() == original


def _paired_inputs():
    contents = {name: (ROOT / name).read_bytes() for name in ("pyproject.toml", "README.md")}
    for path in sorted((ROOT / "harness").rglob("*")):
        if (
            path.is_file()
            and not {"__pycache__", ".pytest_cache"} & set(path.parts)
            and path.suffix not in {".pyc", ".pyo"}
        ):
            contents[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for path in sorted((ROOT / "al/installer").glob("*.py")):
        contents[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    return contents


def test_paired_wheel_installs_source_materializer_without_workspace_imports(tmp_path):
    contents = _paired_inputs()
    build_backend = tomllib.loads(contents["pyproject.toml"].decode())["tool"]["uv"][
        "build-backend"
    ]
    declared_migrations = {
        path.relative_to(ROOT).as_posix()
        for pattern in build_backend["source-include"]
        if pattern.startswith("harness/migrations/")
        for path in ROOT.glob(pattern)
        if path.is_file() and path.suffix == ".sql"
    }
    assert W.PAIRED_RESOURCE_REQUIRED <= contents.keys()
    assert declared_migrations
    assert declared_migrations <= contents.keys()
    source, metadata = W.build_copy(contents, tmp_path / "source")
    tool = T.tool_identity("uv")
    build_env = T.environment(tmp_path / "build-environment")
    build = T.run_tool(
        tool,
        [
            "build",
            "--wheel",
            "--offline",
            "--no-sources",
            "--no-config",
            "--out-dir",
            str(tmp_path / "wheels"),
        ],
        cwd=source,
        env=build_env,
        directory=tmp_path / "build-tool",
    )
    assert build["returncode"] == 0 and build["cleanup"]["complete"]
    wheel = W.wheel_output(tmp_path / "wheels")
    observed = W.inspect_wheel(wheel, metadata["version"], contents=contents)
    for module in (
        "al/installer/source_materialization.py",
        "al/installer/source_materialization_metadata.py",
    ):
        assert observed["members"][module] == digest(contents[module])

    installed = tmp_path / "installed-wheel"
    install_env = T.environment(tmp_path / "install-environment")
    install = T.run_tool(
        tool,
        [
            "pip",
            "install",
            "--target",
            str(installed),
            "--no-deps",
            "--offline",
            str(wheel),
        ],
        cwd=tmp_path,
        env=install_env,
        directory=tmp_path / "install-tool",
    )
    assert install["returncode"] == 0 and install["cleanup"]["complete"]
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from al.installer import source_materialization as module; "
        "from al.installer import source_materialization_metadata as metadata; "
        "assert callable(module.copy2) and callable(module.write_captured); "
        "assert callable(metadata.observe) and callable(metadata.observe_acl); "
        "assert module.__file__.startswith(sys.argv[1]); "
        "assert metadata.__file__.startswith(sys.argv[1]); "
        "print(module.__file__); print(metadata.__file__)"
    )
    probe = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(installed)],
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert str(installed) in probe.stdout
