"""Short shared action claims exclude exclusive execution authority transitions."""

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from harness.execution_models import ExecutionConflict


@contextmanager
def epoch_action_lock(execution, *, exclusive=False):
    directory = Path(execution.get("authority_directory") or Path(execution["handoff_path"]).parent)
    descriptor = os.open(
        directory / "runtime-recovery.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "a") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ExecutionConflict("unsafe runtime recovery lock")
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExecutionConflict(
                "execution authority transition or action is in progress; refresh recorded status"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
