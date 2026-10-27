"""A short supervisor command owns and reaps its entire descendant process group."""

import os
import signal
import subprocess


def run_owned(argv, *, cwd, env, timeout):
    with subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        finally:
            # This group was created by this exact Popen; also stop descendants
            # if the immediate command exited first. No detached groups are used
            # by the fixed managed AL proof/start/show commands.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
