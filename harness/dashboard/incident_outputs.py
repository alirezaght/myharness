"""Bounded descriptor-only native-output inspection, without initialization or content reads."""

import fcntl
import os
import re
import stat

from harness.execution_artifacts import recorded_output_directory
from harness.execution_contract import loads
from harness.reliability.catalog import scope_filter, selected_values
from harness.reliability.contracts import ROLES

MAX_INDEX = 16 * 1024 * 1024


def _regular(fd, maximum):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
        raise ValueError("output descriptor source unavailable")
    return before


def _index(directory):
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError("output descriptor source unavailable")
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        lock = os.open("artifacts.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        try:
            _regular(lock, MAX_INDEX)
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            index = os.open("index.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
            try:
                before = _regular(index, MAX_INDEX)
                chunks, size = [], 0
                while size <= MAX_INDEX:
                    block = os.read(index, min(65536, MAX_INDEX + 1 - size))
                    if not block:
                        break
                    chunks.append(block)
                    size += len(block)
                after = os.fstat(index)
                if size > MAX_INDEX or any(
                    getattr(before, k) != getattr(after, k)
                    for k in ("st_size", "st_mtime_ns", "st_ctime_ns")
                ):
                    raise ValueError("output descriptor source unavailable")
                return loads(b"".join(chunks), max_bytes=MAX_INDEX)
            finally:
                os.close(index)
        finally:
            os.close(lock)
    finally:
        os.close(root)


def list_outputs(conn, selected, *, limit=25, before_id=None):
    selected_values(selected)
    scope_filter(conn, selected)
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or (
            before_id is not None
            and (
                not isinstance(before_id, str)
                or not re.fullmatch(r"artifact-[0-9a-f]{32}", before_id)
            )
        )
    ):
        raise ValueError("invalid output descriptor page")
    directory = recorded_output_directory(conn, selected["execution"], selected["epoch"])
    result = {
        "items": [],
        "next_cursor": None,
        "source_available": False,
        "content_revalidated": False,
        "ordering": "artifact_id descending",
        "metadata_limit_bytes": MAX_INDEX,
    }
    try:
        index = _index(directory)
    except FileNotFoundError:
        return result
    except (ValueError, TypeError, RecursionError):
        raise OSError("output descriptor source unavailable") from None
    if not isinstance(index, dict) or not isinstance(index.get("records"), dict):
        raise OSError("output descriptor source unavailable")
    rows = []
    try:
        for identifier, record in index["records"].items():
            if not isinstance(record, dict):
                raise ValueError()
            if record.get("source") != "native-agent-output":
                continue
            if (
                not re.fullmatch(r"artifact-[0-9a-f]{32}", identifier)
                or record["artifact_id"] != identifier
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", record["sha256"])
                or record["role"] not in ROLES
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", record["owner"])
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.(?:json|md|txt)", record["name"]
                )
                or type(record["size"]) is not int
                or not 0 <= record["size"] <= 16 * 1024 * 1024
                or type(record["graph_revision"]) is not int
                or record["graph_revision"] < 0
            ):
                raise ValueError()
            from pathlib import Path

            path = Path(record["path"])
            if (
                path.parent != directory
                or path.name != identifier + "." + record["name"].rsplit(".", 1)[1]
            ):
                raise ValueError()
            if before_id is None or identifier < before_id:
                rows.append(
                    {
                        k: record[k]
                        for k in (
                            "artifact_id",
                            "sha256",
                            "name",
                            "owner",
                            "role",
                            "size",
                            "graph_revision",
                        )
                    }
                )
    except (KeyError, ValueError, TypeError):
        raise OSError("output descriptor source unavailable") from None
    rows.sort(key=lambda r: r["artifact_id"], reverse=True)
    result.update(
        items=rows[:limit],
        source_available=True,
        next_cursor={"before_id": rows[limit - 1]["artifact_id"]} if len(rows) > limit else None,
    )
    return result
