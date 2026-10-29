"""Descriptor-relative repository files; symlinks and hardlink aliases are refused."""

import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

PROTECTED = {
    ".git",
    ".codex",
    ".claude",
    ".agents",
    ".ssh",
    ".aws",
    ".netrc",
    ".git-credentials",
    ".mcp.json",
    ".npmrc",
    ".pypirc",
}


def relative_path(path):
    value = PurePosixPath(path)
    if (
        value.is_absolute()
        or not value.parts
        or any(p in {"..", "."} or p in PROTECTED or p.startswith(".env") for p in value.parts)
    ):
        raise ValueError("path is not an ordinary confined repository file")
    if str(value) != path:
        raise ValueError("canonical relative file path required")
    return value


@contextmanager
def parent_descriptor(root, path, *, create=False):
    relative = relative_path(path)
    descriptor = os.open(
        Path(root).resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        for part in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, relative.name
    finally:
        os.close(descriptor)


def _ordinary(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("file must be regular with exactly one link")


def read_file(root, path, *, offset=0, limit=16384):
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 65536:
        raise ValueError("bounded file read required")
    with parent_descriptor(root, path) as (directory, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            _ordinary(os.fstat(descriptor))
            raw = os.pread(descriptor, limit + 1, offset)
        finally:
            os.close(descriptor)
    return {
        "text": raw[:limit].decode(errors="replace"),
        "truncated": len(raw) > limit,
        "offset": offset,
    }


def parse_patch(patch):
    if not isinstance(patch, str) or len(patch.encode()) > 262144:
        raise ValueError("bounded patch required")
    lines = patch.splitlines(keepends=True)
    if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise ValueError("complete patch envelope required")
    edits, current = [], None
    for line in lines[1:-1]:
        if line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ")):
            kind, path = line[4:].rstrip("\n").split(": ", 1)
            relative_path(path)
            current = {"kind": kind, "path": path, "lines": []}
            edits.append(current)
        elif current is None or line.startswith("***"):
            raise ValueError("unsupported patch construct")
        else:
            current["lines"].append(line)
    if not edits or len({e["path"] for e in edits}) != len(edits):
        raise ValueError("nonempty unique patch targets required")
    return edits


def transform(text, edit):
    if edit["kind"] == "Delete File":
        if edit["lines"]:
            raise ValueError("delete file has unexpected patch content")
        return None
    if edit["kind"] == "Add File":
        if any(not line.startswith("+") for line in edit["lines"]):
            raise ValueError("add-file lines require '+'")
        return "".join(line[1:] for line in edit["lines"])
    hunks, hunk = [], []
    for line in edit["lines"]:
        if line.startswith("@@"):
            if hunk:
                hunks.append(hunk)
            hunk = []
        elif line[:1] in {" ", "+", "-"}:
            hunk.append(line)
        else:
            raise ValueError("unsupported patch hunk")
    if hunk:
        hunks.append(hunk)
    for hunk in hunks:
        old = "".join(line[1:] for line in hunk if not line.startswith("+"))
        new = "".join(line[1:] for line in hunk if not line.startswith("-"))
        if not old or text.count(old) != 1:
            raise ValueError("patch context is missing or ambiguous")
        text = text.replace(old, new, 1)
    if not hunks:
        raise ValueError("update has no hunks")
    return text


MAX_EDIT_TARGET = 8 * 1024 * 1024


def _file_version(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_edit_target(root, path):
    # Patch payloads and ordinary reads keep their existing bounds. A small
    # patch may target a larger file, observed through one stable no-follow fd.
    with parent_descriptor(root, path) as (directory, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            _ordinary(before)
            if before.st_size > MAX_EDIT_TARGET:
                raise ValueError("patch target exceeds 8 MiB bounded edit limit")
            chunks, size = [], 0
            while size <= MAX_EDIT_TARGET:
                chunk = os.read(descriptor, min(65536, MAX_EDIT_TARGET + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            after = os.fstat(descriptor)
            if size > MAX_EDIT_TARGET or _file_version(before) != _file_version(after):
                raise ValueError("patch target moved or exceeded bounded edit limit")
            return b"".join(chunks).decode("utf-8"), _file_version(after)
        finally:
            os.close(descriptor)


def apply_patch(root, patch, authorize_paths):
    edits = parse_patch(patch)
    authorize_paths([e["path"] for e in edits])
    prepared = []
    # Validate every target/context before performing any mutation.
    for edit in edits:
        if edit["kind"] == "Add File":
            try:
                with parent_descriptor(root, edit["path"]) as (directory, name):
                    if _exists(directory, name):
                        raise ValueError("add-file target already exists")
            except FileNotFoundError:
                pass
            prepared.append((edit, transform("", edit), None))
        else:
            text, version = _read_edit_target(root, edit["path"])
            prepared.append((edit, transform(text, edit), version))
    authorize_paths([e["path"] for e in edits])
    for edit, content, expected in prepared:
        with parent_descriptor(root, edit["path"], create=True) as (directory, name):
            if expected:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                _ordinary(info)
                if expected != _file_version(info):
                    raise ValueError("patch target changed before commit")
            elif _exists(directory, name):
                raise ValueError("add-file target appeared before commit")
            if content is None:
                os.unlink(name, dir_fd=directory)
            else:
                temporary = ".harness-patch-" + secrets.token_hex(12)
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content.encode())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass
    return {"changed": [e["path"] for e in edits]}


def _exists(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
