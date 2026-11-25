"""Finite read-only Git forms and bounded nonexecuting configuration qualification."""

import configparser
import hashlib
import os
import re
import selectors
import time
from pathlib import Path

from harness.policy.contracts import require

BYTE_LIMIT = 8 * 1024 * 1024
EXCLUDED = {
    ".git",
    ".codex",
    ".claude",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".turbo",
}
FIXED = {
    "repository_identity": ["rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir"],
    "top": ["rev-parse", "--show-toplevel"],
    "object_binding": ["rev-parse", "--git-common-dir", "--show-object-format"],
    "head": ["rev-parse", "HEAD"],
    "head_verify": ["rev-parse", "--verify", "HEAD^{commit}"],
    "index": ["ls-files", "--stage", "-z"],
    "cached": ["ls-files", "--cached", "-z"],
    "untracked": ["ls-files", "--others", "--exclude-standard", "-z"],
    "status": ["status", "--ignore-submodules=all", "--porcelain"],
}


def revision(value):
    require(
        isinstance(value, str)
        and (value == "HEAD" or re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", value)),
        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    )
    return value


def argv(method, values):
    if method in FIXED:
        require(values == {})
        return FIXED[method]
    if method in {
        "scope_identity",
        "merge_base",
        "tree",
        "long_tree",
        "inventory_tree",
        "currency_diff",
    }:
        require(set(values) == {"revision"})
        ref = revision(values["revision"])
        return {
            "scope_identity": [
                "rev-parse",
                "--git-common-dir",
                "--git-dir",
                "--end-of-options",
                ref + "^{commit}",
                "HEAD^{commit}",
            ],
            "merge_base": ["merge-base", "HEAD", ref],
            "tree": ["ls-tree", "-r", "-z", ref],
            "long_tree": ["ls-tree", "-rlz", "--full-tree", ref],
            "inventory_tree": ["ls-tree", "-r", "-l", "-z", ref],
            "currency_diff": ["diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", ref],
        }[method]
    if method == "retained_tree":
        require(set(values) == {"revision"})
        ref = values["revision"]
        require(isinstance(ref, str) and re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", ref))
        return ["cat-file", "-e", ref + "^{tree}"]
    if method == "parent_diff":
        require(
            set(values) == {"revision", "binary", "ignore"}
            and type(values["binary"]) is bool
            and values["ignore"] in {"all", "none"}
        )
        return [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=" + values["ignore"],
            *(["--binary"] if values["binary"] else ["--name-only", "-z"]),
            revision(values["revision"]),
            "--",
        ]
    excluded = [
        v
        for name in sorted(EXCLUDED)
        for v in ("--exclude=" + name + "/", "--exclude=**/" + name + "/")
    ]
    inventories = {
        "inventory_untracked": [
            "ls-files",
            "--others",
            "--exclude-per-directory=.gitignore",
            *excluded,
            "-z",
        ],
        "inventory_ignores": [
            "ls-files",
            "--others",
            "--cached",
            "--exclude-per-directory=.gitignore",
            *excluded,
            "-z",
            "--",
            ".gitignore",
            ":(glob)**/.gitignore",
        ],
        "inventory_ignored": [
            "ls-files",
            "--others",
            "--ignored",
            "--directory",
            "--exclude-per-directory=.gitignore",
            *excluded,
            "-z",
        ],
    }
    if method in inventories:
        require(values == {})
        return inventories[method]
    if method in {"ledger_tracked", "ledger_tree", "ledger_scope"}:
        from harness.execution_files import relative_path

        require(set(values) == ({"paths"} if method == "ledger_scope" else {"path"}))
        paths = values["paths"] if method == "ledger_scope" else [values["path"]]
        require(isinstance(paths, list) and 0 < len(paths) <= 64)
        require(all(p == "." or str(relative_path(p)) == p for p in paths))
        return (
            ["ls-tree", "-r", "--name-only", "HEAD", "--", *paths]
            if method == "ledger_tree"
            else ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *paths]
            if method == "ledger_scope"
            else ["ls-files", "-z", "--", *paths]
        )
    if method == "inventory_scope":
        require(set(values) == {"path"})
        from harness.execution_files import relative_path

        require(str(relative_path(values["path"])) == values["path"])
        return ["ls-files", "--others", "-z", "--", values["path"]]
    require(False, "UNSUPPORTED_ENFORCEMENT_CAPABILITY")


def file_state(path, limit=128 * 1024 * 1024, *, content=True):
    path = Path(path)
    require(path.is_absolute() and not path.is_symlink())
    before = path.stat()
    require(path.is_file() and before.st_size <= limit)
    raw = path.read_bytes()
    after = path.stat()
    fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns", "st_ctime_ns")
    require(all(getattr(before, k) == getattr(after, k) for k in fields))
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest() if content else None,
        "stat": tuple(getattr(after, k) for k in fields),
    }


def configuration(root):
    root = Path(root)
    metadata = root / ".git"
    # v1 deliberately refuses linked worktrees, submodules and alternate object stores.
    require(metadata.is_dir() and not metadata.is_symlink(), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    for name in (
        "commondir",
        "config.worktree",
        "shallow",
        "info/grafts",
        "objects/info/alternates",
    ):
        require(not (metadata / name).exists(), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    require(
        not list((metadata / "objects/pack").glob("*.promisor")),
        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    )
    path = metadata / "config"
    state = file_state(path, 65536, content=False)
    parser = configparser.RawConfigParser(strict=True, interpolation=None)
    try:
        parser.read_string(path.read_text())
    except configparser.Error:
        require(False, "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    allowed = {
        "core": {
            "repositoryformatversion",
            "filemode",
            "bare",
            "logallrefupdates",
            "ignorecase",
            "precomposeunicode",
        },
        "user": {"name", "email"},
        "extensions": {"objectformat"},
    }
    for section in parser.sections():
        group = section.split(" ", 1)[0].lower()
        keys = set(parser[section])
        require(group in allowed and keys <= allowed[group], "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
        if group == "extensions":
            require(
                dict(parser[section]) == {"objectformat": "sha256"},
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )
    replacements = metadata / "refs/replace"
    require(not replacements.exists(), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    packed = metadata / "packed-refs"
    if packed.exists():
        require(
            packed.stat().st_size <= 4 * 1024 * 1024
            and b" refs/replace/" not in packed.read_bytes(),
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
    require(file_state(path, 65536, content=False) == state)
    metadata_states = []
    for item in (metadata / "HEAD", metadata / "index", metadata / "packed-refs"):
        metadata_states.append(
            file_state(item, 16 * 1024 * 1024, content=False) if item.exists() else None
        )
    references = sorted((metadata / "refs").rglob("*"))
    require(len(references) <= 4096, "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    for item in references:
        require(not item.is_symlink(), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
        if item.is_file():
            metadata_states.append(file_state(item, 65536, content=False))
    return {"configuration": state, "metadata": metadata_states}


def environment():
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_PAGER": "cat",
        "GIT_ATTR_NOSYSTEM": "1",
    }


OPTIONS = [
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.excludesFile=/dev/null",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
    "-c",
    "maintenance.autoDetach=false",
    "-c",
    "gc.autoDetach=false",
    "-c",
    "diff.external=",
    "-c",
    "core.pager=cat",
]


def capture(process, input_bytes=b"", *, limit=BYTE_LIMIT, timeout=10):
    """Finite requests have bounded pipes; no process wait or bytes transfer holds SQLite."""
    output = bytearray()
    errors = bytearray()
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, output)
        selector.register(process.stderr, selectors.EVENT_READ, errors)
        if input_bytes:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, memoryview(input_bytes))
        else:
            process.stdin.close()
        try:
            while selector.get_map():
                require(time.monotonic() < deadline, "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    if key.fileobj is process.stdin:
                        count = os.write(key.fd, key.data[:65536])
                        remaining = key.data[count:]
                        if remaining:
                            selector.modify(key.fileobj, selectors.EVENT_WRITE, remaining)
                        else:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        raw = os.read(key.fd, 65536)
                        if not raw:
                            selector.unregister(key.fileobj)
                        else:
                            key.data.extend(raw)
                            require(
                                len(output) + len(errors) <= limit,
                                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
                            )
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
            return bytes(output), process.returncode
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
