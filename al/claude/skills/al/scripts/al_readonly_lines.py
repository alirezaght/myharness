#!/usr/bin/env python3
"""Bounded source-line measurement without evaluation or subprocesses."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat

MAX_FILES = 128
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def measure(value, remaining):
    path = Path(value).absolute()
    size = 0
    try:
        if not all(hasattr(os, flag) for flag in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')):
            return {'path': value, 'status': 'unsupported-platform'}, 0
        if path.resolve(strict=True) != path:
            return {'path': value, 'status': 'aliased-path'}, 0
        if len(path.parts) < 2:
            return {'path': value, 'status': 'not-regular'}, 0
        with ExitStack() as stack:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            parent = os.open(path.anchor, directory_flags)
            stack.callback(os.close, parent)
            associations = []
            for component in path.parts[1:-1]:
                child = os.open(component, directory_flags, dir_fd=parent)
                stack.callback(os.close, child)
                selected = os.fstat(child)
                associations.append((parent, component, selected.st_dev, selected.st_ino))
                parent = child
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stack.callback(os.close, descriptor)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                return {'path': value, 'status': 'not-regular'}, 0
            if before.st_size > min(MAX_FILE_BYTES, remaining):
                return {'path': value, 'status': 'read-budget-exceeded'}, 0
            lines = 0
            last = b''
            while size < before.st_size:
                block = os.read(descriptor, min(65536, before.st_size - size))
                if not block:
                    break
                size += len(block)
                if size > min(MAX_FILE_BYTES, remaining):
                    return {'path': value, 'status': 'read-budget-exceeded'}, size
                lines += block.count(b'\n')
                last = block[-1:]
            after = os.fstat(descriptor)
            if identity(before) != identity(after) or size != before.st_size:
                return {'path': value, 'status': 'input-changed'}, size
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if identity(current) != identity(after):
                return {'path': value, 'status': 'input-changed'}, size
            for directory, component, device, inode in associations:
                current = os.stat(component, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (device, inode):
                    return {'path': value, 'status': 'input-changed'}, size
            return {'path': value, 'status': 'measured', 'lines': lines + int(bool(last) and last != b'\n'), 'bytes': size}, size
    except (OSError, ValueError):
        return {'path': value, 'status': 'unavailable'}, size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', help='Explicit regular source files; no recursive scan')
    args = parser.parse_args(argv)
    if len(args.paths) > MAX_FILES:
        parser.error('at most 128 explicit files are supported')
    rows, remaining = [], MAX_TOTAL_BYTES
    for value in args.paths:
        row, consumed = measure(value, remaining)
        remaining = max(0, remaining - consumed)
        rows.append(row)
    print(json.dumps({'schema_version': 1, 'line_definition': 'newline-delimited lines, including a final unterminated line', 'files': rows}))
    return 0 if all(row['status'] == 'measured' for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
