"""Resolve resources from the selected AL package, never an ambient install."""
from pathlib import Path


def package_root(test_file):
    return Path(test_file).resolve().parents[1]


def repository_root(test_file):
    for parent in Path(test_file).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def platform_name(test_file):
    package = package_root(test_file)
    return package.parents[1].name.lstrip(".")


def platform_root(test_file):
    package = package_root(test_file)
    return package.parents[1]


def peer_package_root(test_file):
    root = platform_root(test_file)
    installed = root.name.startswith(".")
    name = platform_name(test_file)
    peer = "claude" if name == "codex" else "codex"
    return root.parent / (("." if installed else "") + peer) / "skills" / "al"


def role_dir(test_file):
    root = platform_root(test_file)
    installed_claude = platform_name(test_file) == "claude" and root.name.startswith(".")
    return root / "agents" / ("al" if installed_claude else "")
