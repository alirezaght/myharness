"""Bounded streamed executable identity; validator resource limits stay unchanged."""

import hashlib
import os
from pathlib import Path
import stat

from .validator_origin import safe

LIMIT = 128 * 1024 * 1024


def executable_state(path):
    path = safe(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= LIMIT:
        raise ValueError("invalid bounded workspace executable")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        h = hashlib.sha256()
        total = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while raw := stream.read(1024 * 1024):
                total += len(raw)
                if total > LIMIT:
                    raise ValueError("workspace executable byte limit")
                h.update(raw)
        finished = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = safe(path).stat()
    keys = ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns", "st_ctime_ns")
    if total != before.st_size or any(
        getattr(item, key) != getattr(before, key)
        for item in (opened, finished, after)
        for key in keys
    ):
        raise ValueError("workspace executable moved during capture")
    return {"sha256": h.hexdigest(), "mode": stat.S_IMODE(before.st_mode)}
