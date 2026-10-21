"""Trusted owned-group bootstrap; publish identity before executing reviewed argv."""
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from al.verification_process import identity  # noqa: E402
from al.verification_profile import write_json  # noqa: E402


def main():
    if len(sys.argv)<4 or sys.argv[2]!='--':raise ValueError('invalid owned bootstrap argv')
    path=Path(sys.argv[1])
    token=os.environ['AL_VERIFICATION_LAUNCH_TOKEN']
    assignment=os.environ['AL_VERIFICATION_ASSIGNMENT']
    observed=identity(os.getpid())
    if observed is None or observed['pgid']!=os.getpid() or observed['sid']!=os.getpid():
        raise ValueError('bootstrap is not an owned session leader')
    if path.exists():raise ValueError('owned receipt already exists')
    write_json(path,{'schema_version':1,'token':token,'assignment':assignment,'identity':observed})
    signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGINT,signal.SIGTERM})
    os.execvpe(sys.argv[3],sys.argv[3:],os.environ)


if __name__=='__main__':main()
