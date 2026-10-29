"""Bounded repository discovery, independent of writable task admission."""

import os
import shutil
import stat
from pathlib import Path

from harness.execution_files import PROTECTED, relative_path

_FLAGS = {
    "--files",
    "-n",
    "--line-number",
    "-l",
    "--files-with-matches",
    "-i",
    "--ignore-case",
    "-F",
    "--fixed-strings",
    "--hidden",
    "--no-heading",
    "--count",
    "-c",
}
_VALUES = {"-g", "--glob", "-e", "--regexp"}


def trusted_rg(root):
    hidden = [
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve(),
        Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).resolve(),
    ]
    for candidate in (
        "/opt/homebrew/bin/rg",
        "/usr/local/bin/rg",
        "/usr/bin/rg",
        shutil.which("rg"),
    ):
        if not candidate:
            continue
        try:
            binary = Path(candidate).resolve(strict=True)
            info = binary.stat()
        except OSError:
            continue
        if (
            any(binary.is_relative_to(p) for p in [root, *hidden])
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
            or info.st_uid not in {0, os.getuid()}
        ):
            continue
        return str(binary)
    raise ValueError(
        "read-only discovery requires an operator-trusted ripgrep "
        "outside repository and native private roots"
    )


def discovery_argv(argv, cwd, root):
    if not argv or argv[0] != "rg":
        raise ValueError("discovery requires the fixed rg command")
    cwd, root = Path(cwd).absolute(), Path(root).resolve(strict=True)
    if not cwd.is_relative_to(root):
        raise ValueError("discovery cwd escapes repository")
    _path(root, cwd.relative_to(root).as_posix())
    args, paths, pattern = [], [], False
    files = False
    i, operands = 1, False
    while i < len(argv):
        arg = argv[i]
        if arg == "--" and not operands:
            operands = True
            i += 1
            continue
        if not operands and arg in _FLAGS:
            if arg == "--files":
                files = True
            args.append(arg)
        elif not operands and arg in _VALUES:
            i += 1
            if i >= len(argv):
                raise ValueError("discovery option needs an operand")
            value = argv[i]
            if arg in {"-g", "--glob"}:
                if value.startswith("/") or ".." in value.split("/") or "\x00" in value:
                    raise ValueError("invalid repository discovery glob")
            else:
                pattern = True
            args.extend([arg, value])
        elif not operands and arg.startswith("-"):
            raise ValueError("unsupported discovery option")
        elif not files and not pattern:
            args.extend(["-e", arg])
            pattern = True
        else:
            _path(root, (cwd.relative_to(root) / arg).as_posix())
            paths.append(arg)
        i += 1
    if files and pattern or not files and not pattern:
        raise ValueError("discovery requires either --files or a search pattern")
    exclusions = [".env*", *sorted(PROTECTED)]
    guards = [v for name in exclusions for v in ("--glob", "!" + name, "--glob", "!**/" + name)]
    return [
        trusted_rg(root),
        "--no-config",
        "--color",
        "never",
        "--max-filesize",
        "1M",
        *args,
        *guards,
        "--",
        *(paths or ["."]),
    ]


def _path(root, value):
    if value == ".":
        return
    relative = relative_path(value)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("discovery symlink operands are refused")
    if not cursor.resolve(strict=True).is_relative_to(root):
        raise ValueError("discovery path escapes repository")


INSPECTED_PIN = "e36cc3fa820c9fdb3d627b3c7b78b7546a8d2f572e37b227203dff5ee340a8ab"


def attribution_compatibility(pin_identity=None):
    known = pin_identity == INSPECTED_PIN
    return {
        "status": "unsupported" if known else "unverified",
        "pin_identity": pin_identity,
        "metadata_only": False if known else None,
        "target": "metadata-only attribution compatibility",
        "candidate_requirement": "unverified",
        "message": (
            "Metadata-only attribution is unsupported for this inspected pin; "
            "actual candidate requirement and coverage remain unverified. "
            if known
            else "Current candidate attribution has not been verified. "
        )
        + (
            "The inspected pin requires candidate lstat, content hashing, binary diffs "
            "and retained source bytes. A Harness bridge upgrade does not change "
            "an immutable AL pin; protected content remains denied."
        ),
        "inspected_contract": {
            "pin_identity": INSPECTED_PIN,
            "metadata_only_compatibility": "unsupported",
            "sources": [
                "al_task_attribution._rows",
                "al_scope._records",
                "_capture_candidate",
                "observe(retain_content=True)",
            ],
        },
        "discovery_coverage": "unverified",
    }
