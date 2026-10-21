"""Real local wheel/venv preparation, without claiming source approval/apply."""

import json
from pathlib import Path
import zipfile

import pytest

from al.installer import workspace_environment as E
from al.installer import workspace_tools as T
from al.installer import workspace_wheel as W
from al.installer.registry import canonical, digest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    directory = tmp_path_factory.mktemp("actual-harness-wheel")
    contents = {name: (ROOT / name).read_bytes() for name in ("pyproject.toml", "README.md")}
    for path in (ROOT / "harness").rglob("*"):
        if (
            path.is_file()
            and not {"__pycache__", ".pytest_cache"} & set(path.parts)
            and path.suffix not in {".pyc", ".pyo"}
        ):
            contents[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    copy, metadata = W.build_copy(contents, directory / "source")
    env = T.environment(directory / "owned-environment")
    tool = T.tool_identity("uv")
    record = T.run_tool(
        tool,
        [
            "build",
            "--wheel",
            "--offline",
            "--no-sources",
            "--no-config",
            "--out-dir",
            str(directory / "dist"),
        ],
        cwd=copy,
        env=env,
        directory=directory / "build-tool",
    )
    artifact = W.wheel_output(directory / "dist")
    wheel = W.inspect_wheel(artifact, metadata["version"])
    # Component input only; no terminal approval or workspace apply is fabricated.
    result = {
        "kind": "component-built-harness-wheel",
        "tool": record,
        "inputs": {name: digest(raw) for name, raw in contents.items()},
        "wheel": dict(wheel, path=str(artifact)),
    }
    result["digest"] = digest(canonical(result))
    return directory, result


def test_real_harness_wheel_installs_in_owned_venv_and_neutral_help_does_not_launch(built):
    directory, wheel = built
    receipt = E.create_environment(wheel, directory / "installation")
    assert E.verify_environment(receipt) == receipt
    assert Path(receipt["module_origin"]).is_relative_to(Path(receipt["directory"]))
    assert receipt["wheel_sha256"] == wheel["wheel"]["sha256"]
    assert len(receipt["verification"]) == 3
    for step in receipt["verification"]:
        assert step["returncode"] == 0 and step["cleanup"]["complete"]
    output = directory / "installation/tools/verify-0/output.log"
    assert output.read_text().strip() == "harness " + wheel["wheel"]["version"]
    with pytest.raises(ValueError, match="collision"):
        E.create_environment(wheel, directory / "installation")
    target = Path(receipt["module_origin"])
    raw = target.read_bytes()
    try:
        target.write_bytes(raw + b"\n")
        with pytest.raises(ValueError, match="drift"):
            E.verify_environment(receipt)
    finally:
        target.write_bytes(raw)
    assert E.verify_environment(receipt) == receipt
    forged = json.loads(json.dumps(receipt))
    forged["interpreter"]["sha256"] = "0" * 64
    forged["digest"] = digest(canonical({k: v for k, v in forged.items() if k != "digest"}))
    with pytest.raises(ValueError, match="bytes/mode"):
        E.verify_environment(forged)
    assert b'-I -B -m harness.cli "$@"' in E.launcher(Path(receipt["directory"]))


def test_build_rejects_arbitrary_backend_and_install_refuses_changed_wheel(built, tmp_path):
    contents = {
        "pyproject.toml": b'[project]\nname="harness"\nversion="1"\ndependencies=[]\n[build-system]\nbuild-backend="unknown"\n'
    }
    with pytest.raises(ValueError, match="configuration"):
        W.project(contents)
    _, result = built
    changed = json.loads(json.dumps(result))
    source = Path(result["wheel"]["path"])
    corrupt = tmp_path / source.name
    corrupt.write_bytes(source.read_bytes() + b"changed")
    changed["wheel"]["path"] = str(corrupt)
    with pytest.raises(ValueError, match="wheel changed"):
        E.create_environment(changed, tmp_path / "refused")
    assert not (tmp_path / "refused/environment").exists()


@pytest.mark.parametrize("name", ["../escape.py", "/absolute", "other_package/__init__.py"])
def test_wheel_member_escape_and_unrelated_packages_refuse(tmp_path, name):
    path = tmp_path / "harness-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "pass")
    with pytest.raises(ValueError, match="unsafe|unexpected"):
        W.inspect_wheel(path, "0.1.0")


def test_tool_binary_drift_or_retargeted_invocation_refuses_before_spawn(tmp_path):
    file = tmp_path / "tool"
    file.write_bytes(b"#!/bin/sh\nexit 0\n")
    file.chmod(0o755)
    from al.installer.validator_origin import capture

    _, state = capture(file)
    identity = dict(state, executable=str(file), kind="uv")
    T.verify_tool(identity)
    file.write_bytes(b"#!/bin/sh\nexit 1\n")
    with pytest.raises(ValueError, match="bytes/mode"):
        T.verify_tool(identity)
    file.unlink()
    other = tmp_path / "different-tool"
    other.write_bytes(b"#!/bin/sh\nexit 0\n")
    other.chmod(0o755)
    file.symlink_to(other)
    with pytest.raises(ValueError, match="invocation path"):
        T.verify_tool(identity)


def test_executable_identity_streams_supported_large_tools_with_explicit_bound(tmp_path):
    from al.installer.workspace_executable import executable_state, LIMIT

    tool = tmp_path / "large-tool"
    raw = b"x" * (17 * 1024 * 1024)
    tool.write_bytes(raw)
    tool.chmod(0o755)
    assert executable_state(tool) == {"sha256": digest(raw), "mode": 0o755}
    with tool.open("wb") as stream:
        stream.truncate(LIMIT + 1)
    with pytest.raises(ValueError, match="bounded"):
        executable_state(tool)


@pytest.mark.parametrize(
    "kind", ["directory-payload", "symlink", "device", "file-directory-collision"]
)
def test_wheel_special_or_ambiguous_members_refuse(tmp_path, kind):
    import stat

    path = tmp_path / "harness-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        if kind == "file-directory-collision":
            archive.writestr("harness/x", "value")
            archive.writestr("harness/x/", "")
        else:
            info = zipfile.ZipInfo("harness/x/" if kind == "directory-payload" else "harness/x")
            info.external_attr = (
                {
                    "directory-payload": stat.S_IFDIR,
                    "symlink": stat.S_IFLNK,
                    "device": stat.S_IFCHR,
                }[kind]
                | 0o755
            ) << 16
            archive.writestr(info, "not an empty directory")
    with pytest.raises(ValueError, match="unsafe|colliding"):
        W.inspect_wheel(path, "0.1.0")
