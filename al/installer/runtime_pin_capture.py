"""Capture a release-layout runtime without executing its application modules."""

import os
from pathlib import Path

from .validator_origin import capture, decode, digest, safe

LAYOUT_FIELDS = {
    "schema_version",
    "platforms",
    "package",
    "package_roots",
    "roles",
    "required_scripts",
    "required_claude_scripts",
    "exclude_parts",
    "exclude_suffixes",
    "prerequisites",
    "runtime_schema",
}


def relative(value):
    path = Path(value)
    if (
        not isinstance(value, str)
        or not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise ValueError("unsafe runtime pin resource name")
    return path


def distribution(root, platform):
    root = safe(root).resolve(strict=True)
    raw, metadata = capture(root / "release-layout.json")
    layout = decode(raw)
    if (
        set(layout) != LAYOUT_FIELDS
        or layout["schema_version"] != 1
        or platform not in layout["platforms"]
        or platform not in {"codex", "claude"}
    ):
        raise ValueError("unsupported runtime pin release layout")
    if layout["package"] != "skills/al" or set(layout["package_roots"]) != {
        "SKILL.md",
        "scripts",
        "reference",
    }:
        raise ValueError("unsupported runtime pin package layout")
    package = root / platform / relative(layout["package"])
    paths = [root / "release-layout.json", root / "alctl.py"]
    paths.extend(sorted((root / "installer").glob("*.py")))
    for name in layout["package_roots"]:
        member = package / relative(name)
        safe(member)
        if member.is_file():
            paths.append(member)
        elif member.is_dir():
            for directory, dirs, files in os.walk(member, followlinks=False):
                directory = Path(directory)
                dirs[:] = sorted(d for d in dirs if d not in layout["exclude_parts"])
                for child in dirs:
                    safe(directory / child)
                for child in sorted(files):
                    path = directory / child
                    if (
                        child not in layout["exclude_parts"]
                        and path.suffix not in layout["exclude_suffixes"]
                    ):
                        safe(path)
                        paths.append(path)
        else:
            raise ValueError("missing runtime pin package root")
    roles = root / platform / "agents"
    suffix = ".toml" if platform == "codex" else ".md"
    expected_roles = {name + suffix for name in layout["roles"]}
    if not expected_roles or not roles.is_dir():
        raise ValueError("missing runtime pin roles")
    actual_roles = {
        path.name for path in roles.iterdir() if path.name not in layout["exclude_parts"]
    }
    if actual_roles != expected_roles:
        raise ValueError("runtime pin role membership differs from release layout")
    paths.extend(roles / name for name in sorted(expected_roles))
    required = list(layout["required_scripts"])
    if platform == "claude":
        required += layout["required_claude_scripts"]
    for name in required:
        path = package / "scripts" / relative(name)
        if path not in paths:
            raise ValueError("missing required runtime pin script: " + name)
    files, contents, resources = {}, {}, {}
    for path in sorted(paths):
        content, state = capture(path)
        name = path.relative_to(root).as_posix()
        files[name] = dict(state, kind="file")
        contents[name] = content
        if path.is_relative_to(package):
            logical = path.relative_to(package).as_posix()
        elif path.is_relative_to(roles):
            logical = "roles/" + path.name
        else:
            logical = "bootstrap/" + path.relative_to(root).as_posix()
        resources[logical] = name
    if contents["release-layout.json"] != raw or files["release-layout.json"] != dict(
        metadata, kind="file"
    ):
        raise ValueError("runtime pin release layout moved")
    return (
        {"source_root": str(root), "platform": platform, "layout_sha256": digest(raw)},
        files,
        contents,
        resources,
    )


def stable_distribution(root, platform):
    first = distribution(root, platform)
    second = distribution(root, platform)
    if first != second:
        raise ValueError("runtime distribution moved during capture")
    return first
