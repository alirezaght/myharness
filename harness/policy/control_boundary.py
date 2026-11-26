"""Source-fixed no-descendant capability probe for the sealed control profiles only."""

import subprocess
import sys
from pathlib import Path

from harness.execution_isolation import operation_popen
from harness.policy.contracts import qualification_require
from harness.policy.control_git import capture, environment, file_state

PROBE = r"""
import errno,os,subprocess,sys
allowed=False

def denied(call, kind):
    global allowed
    try:
        result=call()
    except OSError as error:
        if error.errno not in (errno.EPERM,errno.EACCES):raise
        return
    allowed=True
    if kind=='pty':
        pid,descriptor=result
        if pid==0:os._exit(0)
        os.close(descriptor);os.waitpid(pid,0)
    elif kind=='fork':
        if result==0:os._exit(0)
        os.waitpid(result,0)
    elif kind=='pid':os.waitpid(result,0)
    else:
        if result.wait(timeout=2)!=0:raise RuntimeError('probe child did not close')

denied(os.fork,'fork')
denied(os.forkpty,'pty')
child=[sys.executable,'-I','-S','-B','-c','import os;os._exit(0)']
denied(lambda:os.posix_spawn(sys.executable,child,{'PATH':'/usr/bin:/bin'}),'pid')
for vfork in (False,True):
    subprocess._USE_VFORK=vfork
    denied(lambda:subprocess.Popen(child,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True),'process')
denied(lambda:subprocess.Popen(child,stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=False),'process')
denied(lambda:subprocess.Popen(child,stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True),'process')
print('allowed' if allowed else 'denied')
"""


def prepare(operation, interpreter, *, al=False, qualification_ref=None):
    qualification_require(sys.platform == "darwin", "platform_unsupported", qualification_ref)
    profile = operation.profile(al=True, al_optimizations=False) if al else operation.profile(())
    policy = Path(profile.policy)
    policy.write_bytes(policy.read_bytes() + b"\n(deny process-fork)\n")
    process = operation_popen(
        profile,
        [interpreter, "-I", "-S", "-B", "-c", PROBE],
        cwd=operation.envelope["repository"],
        env=environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    raw, code = capture(process, limit=4096, timeout=10)
    qualification_require(
        code == 0 and raw == b"denied\n", "boundary_probe_failed", qualification_ref
    )
    return profile, file_state(policy)
