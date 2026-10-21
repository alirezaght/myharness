"""Workspace and verification tools share actual identity-checked ownership."""

import os
import sys

from al import verification_process
from al.installer import owned_process


def test_same_lifecycle_and_isolated_bootstrap_ignore_hostile_startup(tmp_path):
    assert verification_process is owned_process
    hostile = tmp_path / "pythonpath"
    hostile.mkdir()
    marker = tmp_path / "startup-ran"
    (hostile / "sitecustomize.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ').write_text("unsafe")\n'
    )
    output = tmp_path / "tool-output"
    with output.open("wb") as stream:
        owner = owned_process.execute(
            [sys.executable, "-I", "-S", "-B", "-c", 'print("actual tool")'],
            cwd=tmp_path,
            env=dict(os.environ, PYTHONPATH=str(hostile)),
            output=stream,
            directory=tmp_path / "private",
            assignment="workspace-build",
            timeout=10,
        )
    assert owner.process.returncode == 0
    assert output.read_bytes() == b"actual tool\n"
    assert not marker.exists()
    assert not owner.members()
    assert owner.record()["identity"] and owner.record()["token"]


def test_owned_launch_bootstrap_cannot_import_target_installer_package(tmp_path):
    marker = tmp_path / "target-imported"
    package = tmp_path / "installer"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ').write_text("unsafe")\n'
    )
    with (tmp_path / "output").open("wb") as stream:
        owner = owned_process.execute(
            [sys.executable, "-I", "-S", "-B", "-c", "pass"],
            cwd=tmp_path,
            env=dict(os.environ, PYTHONPATH=str(tmp_path)),
            output=stream,
            directory=tmp_path / "private",
            assignment="workspace-native",
            timeout=10,
        )
    assert owner.process.returncode == 0 and not marker.exists()
    assert not owner.members()
