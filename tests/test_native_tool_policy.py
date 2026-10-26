"""Version-specific native controller profile checks against installed executables."""

import subprocess

import pytest

from tests.native_model_fixture import native_executable

pytestmark = pytest.mark.native


@pytest.mark.parametrize("host,version", [("codex", "0.164.0"), ("claude", "2.1.310")])
def test_installed_native_profile_prerequisites(host, version):
    executable = native_executable(host)
    assert executable, host
    found = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    assert version in found.stdout, (executable, found.stdout)
    help_result = subprocess.run(
        [executable, *(["exec"] if host == "codex" else []), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert help_result.returncode == 0
    required = (
        ["--ignore-user-config", "--ignore-rules"]
        if host == "codex"
        else ["--restricted", "--strict-mcp-config", "--agents", "--tools"]
    )
    for flag in required:
        assert flag in help_result.stdout, (executable, version, flag)
    print(f"installed native profile {executable} {found.stdout.strip()}")
