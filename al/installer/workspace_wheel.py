"""Build and inspect the exact approved Harness wheel in an owned copy."""

import stat
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from .registry import canonical, digest, namespace, write_json
from .validator_origin import capture, safe
from .workspace_source import revalidate_source
from .workspace_tools import environment, run_tool, tool_identity

LEGACY_LAYOUT = {"module-root": ""}
PAIRED_LAYOUT = {"module-root": "", "module-name": ["harness", "al.installer"]}
PAIRED_REQUIRED = {
    "al/installer/" + name
    for name in (
        "__init__.py",
        "storage_filesystem.py",
        "storage_lifecycle.py",
        "storage_inventory.py",
        "storage_declarations.py",
        "verification_contracts.py",
        "verification_harness.py",
        "verification_pytest.py",
        "verification_harness_child.py",
    )
}


def _paired(contents):
    return (
        tomllib.loads(contents["pyproject.toml"].decode())
        .get("tool", {})
        .get("uv", {})
        .get("build-backend")
        == PAIRED_LAYOUT
    )


def _installer_member(name):
    path = Path(name)
    return len(path.parts) == 3 and path.parts[:2] == ("al", "installer") and path.suffix == ".py"


def project(contents):
    if "pyproject.toml" not in contents:
        raise ValueError("approved Harness build configuration missing")
    value = tomllib.loads(contents["pyproject.toml"].decode())
    metadata = value.get("project", {})
    backend = value.get("build-system", {})
    if (
        metadata.get("name") != "harness"
        or not isinstance(metadata.get("version"), str)
        or metadata.get("dependencies") != []
        or metadata.get("dynamic")
        or metadata.get("scripts", {}).get("harness") != "harness.cli:main"
        or backend != {"requires": ["uv_build>=0.10,<0.11"], "build-backend": "uv_build"}
        or value.get("tool", {}).get("uv", {}).get("build-backend")
        not in (LEGACY_LAYOUT, PAIRED_LAYOUT)
    ):
        raise ValueError("unsupported approved Harness build configuration")
    required = {"harness/__init__.py", "harness/cli.py"}
    if _paired(contents):
        required |= PAIRED_REQUIRED
    if not required <= contents.keys():
        raise ValueError("approved Harness implementation missing")
    return metadata


def inspect_wheel(path, version, *, contents=None):
    raw, state = capture(path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) > 10000 or len(set(names)) != len(names):
            raise ValueError("invalid Harness wheel membership")
        if len({name.rstrip("/").casefold() for name in names}) != len(names):
            raise ValueError("case-colliding Harness wheel")
        for item in archive.infolist():
            name = Path(item.filename)
            directory = item.is_dir()
            file_type = stat.S_IFMT(item.external_attr >> 16)
            canonical_name = name.as_posix() + ("/" if directory else "")
            if (
                name.is_absolute()
                or ".." in name.parts
                or canonical_name != item.filename
                or directory
                and (item.file_size != 0 or file_type not in {0, stat.S_IFDIR})
                or not directory
                and file_type not in {0, stat.S_IFREG}
                or item.file_size > 16 * 1024 * 1024
                or ((item.external_attr >> 16) & 0o170000) == 0o120000
            ):
                raise ValueError("unsafe Harness wheel member")
        if contents is not None:
            project(contents)
        paired = (
            _paired(contents)
            if contents is not None
            else any(name.startswith("al/") for name in names)
        )
        directories = {"al/", "al/installer/"} if paired else set()
        if any(
            not (
                name.startswith("harness/")
                or name.startswith("harness-" + version + ".dist-info/")
                or paired
                and (name in directories or _installer_member(name))
            )
            for name in names
        ):
            raise ValueError("unexpected package in Harness wheel")
        actual_files = {item.filename for item in archive.infolist() if not item.is_dir()}
        required = {"harness/__init__.py", "harness/cli.py"} | (
            PAIRED_REQUIRED if paired else set()
        )
        if not required <= actual_files:
            raise ValueError("incomplete Harness wheel implementation")
        if contents is not None:
            expected = {
                name: digest(raw)
                for name, raw in contents.items()
                if name.startswith("harness/") or paired and _installer_member(name)
            }
            packaged = {
                name: digest(archive.read(name))
                for name in actual_files
                if name.startswith("harness/") or _installer_member(name)
            }
            if packaged != expected:
                raise ValueError("Harness wheel differs from approved package source")
        if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
            raise ValueError("Harness wheel expanded byte limit")
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        entries = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata) != 1 or len(entries) != 1:
            raise ValueError("incomplete Harness wheel metadata")
        parsed = BytesParser().parsebytes(archive.read(metadata[0]))
        if (
            parsed["Name"] != "harness"
            or parsed["Version"] != version
            or parsed.get_all("Requires-Dist")
            or "harness/cli.py" not in names
        ):
            raise ValueError("wheel is not the approved dependency-free Harness distribution")
        import configparser

        entry = configparser.ConfigParser(interpolation=None)
        entry.read_string(archive.read(entries[0]).decode())
        if dict(entry["console_scripts"]) != {"harness": "harness.cli:main"}:
            raise ValueError("unexpected Harness wheel executable")
        files = {name: digest(archive.read(name)) for name in names}
    if capture(path)[1] != state:
        raise ValueError("Harness wheel changed during inspection")
    return {
        "filename": path.name,
        "sha256": digest(raw),
        "mode": state["mode"],
        "size": len(raw),
        "version": version,
        "members": files,
    }


def build_copy(contents, directory, files=None):
    metadata = project(contents)
    root = safe(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, raw in contents.items():
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != name
            or ".git" in relative.parts
        ):
            raise ValueError("unsafe approved build input")
        target = root / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(files[name]["mode"] if files is not None else 0o644)
    return root, metadata


def wheel_output(directory):
    entries = list(safe(directory).iterdir())
    marker = Path(directory) / ".gitignore"
    if marker in entries:
        raw, state = capture(marker)
        if raw != b"*" or state["mode"] != 0o644:
            raise ValueError("unsupported uv wheel-output marker")
        entries.remove(marker)
    if len(entries) != 1 or entries[0].suffix != ".whl":
        raise ValueError("Harness build did not produce exactly one wheel")
    capture(entries[0])
    return entries[0]


def build_wheel(source, state_root):
    contents = revalidate_source(source)
    parent = namespace(state_root) / "harness-builds"
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="build-", dir=parent))
    write_json(directory / "operation.json", {"status": "PREPARING", "source": source["digest"]})
    try:
        copy, metadata = build_copy(contents, directory / "source", source["files"])
        env = environment(directory / "environment")
        tool = tool_identity("uv")
        record = run_tool(
            tool,
            [
                "build",
                "--wheel",
                "--offline",
                "--no-sources",
                "--no-config",
                "--out-dir",
                str(directory / "wheels"),
            ],
            cwd=copy,
            env=env,
            directory=directory / "tool",
        )
        artifact = wheel_output(directory / "wheels")
        wheel = inspect_wheel(artifact, metadata["version"], contents=contents)
        revalidate_source(source)
        result = {
            "schema_version": 1,
            "kind": "approved-harness-wheel-v1",
            "source_digest": source["digest"],
            "build_inputs": {name: digest(raw) for name, raw in contents.items()},
            "tool": record,
            "wheel": dict(wheel, path=str(artifact)),
        }
        result["digest"] = digest(canonical(result))
        write_json(directory / "wheel.json", result)
        write_json(directory / "operation.json", {"status": "COMPLETE", "wheel": result["digest"]})
        return result
    except BaseException as exc:
        write_json(
            directory / "operation.json",
            {"status": "PENDING", "error": type(exc).__name__ + ": " + str(exc)},
        )
        raise
