"""Construct a fixed start-only OS boundary from a closed data descriptor."""

import json
import os
import platform
import stat
from pathlib import Path

from .runtime_pin_transaction import runtime_directory, storage
from .validator_origin import safe

FIELDS = {
    "schema_version",
    "repository",
    "successor_runtime",
    "scratch",
    "protected_paths",
    "hidden_paths",
    "classified_paths",
    "approved_files",
}
SECRET_NAMES = {
    ".netrc",
    ".git-credentials",
    ".mcp.json",
    ".npmrc",
    ".pypirc",
    ".ssh",
    ".aws",
    "auth.json",
    ".credentials.json",
}


def hidden_paths(repository):
    found = set()
    for directory, dirs, files in os.walk(repository, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [*dirs, *files]:
            if name.startswith(".env") or name in SECRET_NAMES:
                found.add(str(Path(directory) / name))
    home = Path.home()
    found.update(
        str(home / p)
        for p in (
            ".codex",
            ".claude",
            ".ssh",
            ".aws",
            ".config/gcloud",
            ".config/gh",
            ".docker/config.json",
            ".azure",
            ".kube",
            ".netrc",
            ".git-credentials",
            "Library/Keychains",
        )
    )
    return sorted(found)


def validate_descriptor(value, transaction, *, scratch, protected, classified, approved_files):
    if (
        not isinstance(value, dict)
        or set(value) != FIELDS
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("invalid prepared isolation descriptor")
    root = Path(transaction["repository_identity"]["root"])
    runtime = runtime_directory(transaction)
    expected = {
        "repository": str(root),
        "successor_runtime": str(runtime),
        "scratch": str(scratch),
        "protected_paths": sorted(set(map(str, protected))),
        "hidden_paths": sorted(set(hidden_paths(root)) - set(classified)),
        "classified_paths": sorted(classified),
        "approved_files": sorted(map(str, approved_files)),
    }
    if any(value[k] != v for k, v in expected.items()):
        raise ValueError("prepared isolation grants differ from issued identities")
    for path in [root, runtime, Path(scratch), *map(Path, approved_files), *map(Path, classified)]:
        if Path(path).absolute() != path or safe(path).resolve(strict=True) != path:
            raise ValueError("prepared isolation aliases refused")
    scratch = Path(scratch)
    info = scratch.stat()
    if (
        info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or scratch.is_relative_to(root)
        or scratch.is_relative_to(storage())
        or root.is_relative_to(scratch)
        or storage().is_relative_to(scratch)
    ):
        raise ValueError("prepared scratch collides with protected authority")
    if any(Path(p).is_relative_to(runtime) for p in protected):
        raise ValueError("prepared runtime overlaps protected predecessor state")
    for path in root.rglob("*"):
        if ".git" in path.relative_to(root).parts:
            continue
        if not path.is_symlink() and path.is_file() and path.stat().st_nlink != 1:
            raise ValueError("prepared repository hardlink alias refused")
    return value


def launch(value, executable, program):
    if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise ValueError("prepared start containment unavailable on this platform")
    literal = lambda p: json.dumps(str(p))
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow file-read*)",
        "(allow process-exec process-fork)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        '(deny sysctl-read (sysctl-name-prefix "kern.procargs"))',
        "(deny process-info*)",
        "(allow process-info* (target self))",
        '(allow file-write* (literal "/dev/null"))',
    ]
    for path in [value["successor_runtime"], value["scratch"]]:
        exclusions = " ".join(
            "(require-not (subpath " + literal(p) + "))" for p in value["protected_paths"]
        )
        rules.append(
            "(allow file-write* (require-all (subpath " + literal(path) + ") " + exclusions + "))"
        )
    for path in value["hidden_paths"]:
        rules.append("(deny file-read* (subpath " + literal(path) + "))")
    # Protected execution state stays unreadable except the exact issued start resources.
    for path in value["protected_paths"]:
        exceptions = " ".join(
            "(require-not (literal " + literal(p) + "))" for p in value["approved_files"]
        )
        rules.append(
            "(deny file-read-data (require-all (subpath " + literal(path) + ") " + exceptions + "))"
        )
    policy = Path(value["scratch"]) / "start.sb"
    policy.write_text("\n".join(rules) + "\n")
    policy.chmod(0o600)
    return ["/usr/bin/sandbox-exec", "-f", str(policy), executable, "-I", "-S", "-B", "-c", program]
