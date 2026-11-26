#!/usr/bin/env python3
"""Offline sealed-source correspondence; candidate rendering grants no qualification."""

import argparse
import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

GROUPS = ("installer", "codex", "claude")
LIMIT = 16 * 1024 * 1024
MAX_FILES = 4096
MAX_TOTAL = 64 * 1024 * 1024
MAX_ISSUES = 40
EXPRESSION = ast.dump(
    ast.parse("dict(zip(NAMES.splitlines(), HASHES.splitlines(), strict=True))", mode="eval").body
)


class InventoryError(ValueError):
    """Input cannot support a stable, bounded correspondence observation."""


def identity(info):
    return tuple(
        getattr(info, key)
        for key in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def safe_path(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise InventoryError("noncanonical input path: " + str(path))
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise InventoryError("symlink input: " + str(path))
    return path


def read_file(path):
    path = safe_path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT:
        raise InventoryError("not a bounded regular input: " + str(path))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        parts = []
        size = 0
        while raw := os.read(descriptor, min(65536, LIMIT + 1 - size)):
            parts.append(raw)
            size += len(raw)
            if size > LIMIT:
                raise InventoryError("input byte limit: " + str(path))
        finished = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = path.lstat()
    if size != before.st_size or any(
        identity(value) != identity(before) for value in (opened, finished, after)
    ):
        raise InventoryError("input changed during observation: " + str(path))
    return b"".join(parts), identity(after)


def in_group(name, group):
    prefix = "installer/" if group == "installer" else group + "/skills/al/scripts/"
    return (group == "installer" and name == "alctl.py") or (
        name.startswith(prefix)
        and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.py", name[len(prefix) :]) is not None
    )


def parse_index(raw, group):
    if len(raw) > 1024 * 1024:
        raise InventoryError("index byte limit: " + group)
    tree = ast.parse(raw)
    values = {}
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                continue
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            raise InventoryError("unsupported index statement: " + group)
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id in values:
            raise InventoryError("duplicate or unsupported index declaration: " + group)
        if target.id in {"NAMES", "HASHES"} and isinstance(node.value, ast.Constant):
            value = node.value.value
            if not isinstance(value, str):
                raise InventoryError("index columns must be strings: " + group)
            values[target.id] = value
        elif (
            target.id == "EXPECTED"
            and set(values) == {"NAMES", "HASHES"}
            and ast.dump(node.value) == EXPRESSION
        ):
            values[target.id] = True
        else:
            raise InventoryError("unsupported index expression: " + group)
    if set(values) != {"NAMES", "HASHES", "EXPECTED"}:
        raise InventoryError("missing index columns: " + group)
    names, hashes = values["NAMES"].splitlines(), values["HASHES"].splitlines()
    if not names or len(names) > MAX_FILES or len(names) != len(hashes):
        raise InventoryError("invalid index column sizes: " + group)
    if names != sorted(set(names)):
        raise InventoryError("index names must be unique and sorted: " + group)
    for name, digest in zip(names, hashes, strict=True):
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != name
            or not in_group(name, group)
            or "\x00" in name
        ):
            raise InventoryError("unsafe or foreign index path: " + group)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InventoryError("invalid index digest: " + group)
    return dict(zip(names, hashes, strict=True))


def physical_paths(root):
    result = {"installer": [root / "al/alctl.py"]}
    for group in GROUPS:
        relative = "installer" if group == "installer" else group + "/skills/al/scripts"
        directory = safe_path(root / "al" / relative)
        if not directory.is_dir():
            raise InventoryError("missing executable root: " + str(directory))
        members = sorted(directory.glob("*.py"))
        for path in members:
            if not in_group(path.relative_to(root / "al").as_posix(), group):
                raise InventoryError("unsupported source filename: " + str(path))
        result.setdefault(group, []).extend(members)
    if sum(map(len, result.values())) > MAX_FILES:
        raise InventoryError("executable inventory file limit")
    return result


def capture(root):
    root = safe_path(root)
    paths = physical_paths(root)
    actual, expected, observations = {}, {}, {}
    total = 0
    for group in GROUPS:
        actual[group] = {}
        index = root / "harness/policy" / ("control_sources_" + group + ".py")
        for path in [index, *paths[group]]:
            raw, observed = read_file(path)
            observations[path] = observed
            total += len(raw)
            if total > MAX_TOTAL:
                raise InventoryError("inventory aggregate byte limit")
            if path == index:
                expected[group] = parse_index(raw, group)
            else:
                name = path.relative_to(root / "al").as_posix()
                actual[group][name] = hashlib.sha256(raw).hexdigest()
    snapshot = (root, paths, observations)
    revalidate(snapshot)
    return actual, expected, snapshot


def revalidate(snapshot):
    root, paths, observations = snapshot
    if physical_paths(root) != paths:
        raise InventoryError("executable membership changed during observation")
    for path, observed in observations.items():
        if identity(safe_path(path).lstat()) != observed:
            raise InventoryError("input changed during observation: " + str(path))


def report_for(actual, expected):
    issues = []
    for group in GROUPS:
        for name in sorted(actual[group].keys() | expected[group].keys()):
            code = (
                "uninspected"
                if name not in expected[group]
                else "missing"
                if name not in actual[group]
                else "changed"
                if actual[group][name] != expected[group][name]
                else None
            )
            if code:
                issues.append({"group": group, "path": name, "kind": code})
    return {
        "ok": not issues,
        "source_count": sum(map(len, actual.values())),
        "issues": issues[:MAX_ISSUES],
        "additional_issues": max(0, len(issues) - MAX_ISSUES),
    }


def check(root):
    actual, expected, _ = capture(root)
    return report_for(actual, expected)


def render_index(records):
    names = sorted(records)
    return (
        '"""Build-owned source identities; correspondence alone is not qualification."""\n\n'
        + 'NAMES = """'
        + "\n".join(names)
        + '"""\n\n'
        + 'HASHES = """'
        + "\n".join(records[name] for name in names)
        + '"""\n'
        + "EXPECTED = dict(zip(NAMES.splitlines(), HASHES.splitlines(), strict=True))\n"
    ).encode()


def render_candidates(root, output):
    actual, expected, snapshot = capture(root)
    rendered = {group: render_index(actual[group]) for group in GROUPS}
    for group, raw in rendered.items():
        if parse_index(raw, group) != actual[group]:
            raise InventoryError("candidate roundtrip differs: " + group)
    output = safe_path(output)
    if output == snapshot[0] or output.is_relative_to(snapshot[0]):
        raise InventoryError("candidate directory must be outside the inspected repository")
    if output.exists() or not output.parent.is_dir():
        raise InventoryError("candidate directory must be new with an existing parent")
    output.mkdir(mode=0o700)
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        owned = os.fstat(directory)
        for group in GROUPS:
            name = "control_sources_" + group + ".py"
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(rendered[group])
        if identity(safe_path(output).lstat())[:3] != identity(owned)[:3]:
            raise InventoryError("candidate directory identity changed")
        revalidate(snapshot)
    finally:
        os.close(directory)
    return report_for(actual, expected) | {
        "candidate_directory": str(output),
        "qualification": "not established",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--candidate-output",
        type=Path,
        help="explicitly render candidates into a new directory outside the source",
    )
    args = parser.parse_args(argv)
    try:
        result = (
            render_candidates(args.root, args.candidate_output)
            if args.candidate_output is not None
            else check(args.root)
        )
    except (InventoryError, OSError, SyntaxError, UnicodeError, RecursionError) as error:
        print(json.dumps({"ok": False, "error": str(error)[:512]}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if args.candidate_output is not None or result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
