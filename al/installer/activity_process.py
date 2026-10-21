"""Kernel cwd distinguishes identical session arguments in different worktrees."""
import ctypes
import errno
import os
from pathlib import Path
import sys


class _VInfoStat(ctypes.Structure):
    _fields_ = [('dev', ctypes.c_uint32), ('mode', ctypes.c_uint16), ('nlink', ctypes.c_uint16),
                ('ino', ctypes.c_uint64), ('uid', ctypes.c_uint32), ('gid', ctypes.c_uint32)]
    _fields_ += [(name, ctypes.c_int64) for name in ('atime', 'atimensec', 'mtime', 'mtimensec', 'ctime', 'ctimensec', 'birthtime', 'birthtimensec', 'size', 'blocks')]
    _fields_ += [('blksize', ctypes.c_int32), ('flags', ctypes.c_uint32), ('gen', ctypes.c_uint32),
                ('rdev', ctypes.c_uint32), ('spare', ctypes.c_int64 * 2)]


class _VnodeInfo(ctypes.Structure):
    _fields_ = [('stat', _VInfoStat), ('type', ctypes.c_int32), ('pad', ctypes.c_int32), ('fsid', ctypes.c_int32 * 2)]


class _VnodePath(ctypes.Structure):
    _fields_ = [('info', _VnodeInfo), ('path', ctypes.c_char * 1024)]


class _VnodePaths(ctypes.Structure):
    _fields_ = [('cwd', _VnodePath), ('root', _VnodePath)]


def process_cwd(pid):
    try:
        if sys.platform.startswith('linux'):return Path(os.readlink('/proc/' + str(pid) + '/cwd')).resolve()
        if sys.platform != 'darwin':raise OSError('unsupported process cwd observation platform')
        # Supported SDK sys/proc_info.h: PROC_PIDVNODEPATHINFO=9 and MAXPATHLEN=1024.
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        value = _VnodePaths()
        size = library.proc_pidinfo(int(pid), 9, 0, ctypes.byref(value), ctypes.sizeof(value))
        if size != ctypes.sizeof(value):
            code = ctypes.get_errno()
            if code == errno.ESRCH:return None
            raise OSError(code, 'process cwd identity unavailable')
        raw = bytes(value.cwd.path)
        if not raw:raise OSError('empty process cwd identity')
        return Path(os.fsdecode(raw)).resolve()
    except (ProcessLookupError, FileNotFoundError):return None


class BSDInfo(ctypes.Structure):
    # Layout from the supported macOS SDK's sys/proc_info.h, PROC_PIDTBSDINFO.
    _fields_ = [(name, ctypes.c_uint32) for name in ('flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_seconds', ctypes.c_uint64), ('start_micros', ctypes.c_uint64)]


def identity(pid):
    """Read exact kernel start identity; absence and unavailable identity differ."""
    try:
        if sys.platform == 'darwin':
            library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            value = BSDInfo()
            result = library.proc_pidinfo(int(pid), 3, 0, ctypes.byref(value), ctypes.sizeof(value))
            if result != ctypes.sizeof(value):
                code = ctypes.get_errno()
                if code == errno.ESRCH:return None
                raise OSError(code, 'process identity unavailable')
            start = [value.start_seconds, value.start_micros]
            group = value.pgid
            zombie = value.status == 5
        elif sys.platform.startswith('linux'):
            raw = Path('/proc/' + str(pid) + '/stat').read_text()
            fields = raw.rsplit(')', 1)[1].split()
            start, group, zombie = [int(fields[19])], int(fields[2]), fields[0] == 'Z'
        else:
            raise OSError('unsupported process identity platform')
        return {'pid': int(pid), 'pgid': group, 'sid': os.getsid(pid), 'start': start, 'zombie': zombie}
    except (ProcessLookupError, FileNotFoundError):return None



def process_started_at(observed):
    start = observed['start']
    if sys.platform == 'darwin' and len(start) == 2:return start[0] + start[1] / 1000000
    if sys.platform.startswith('linux') and len(start) == 1:
        lines = Path('/proc/stat').read_text().splitlines()
        boot = next(int(line.split()[1]) for line in lines if line.startswith('btime '))
        return boot + start[0] / os.sysconf('SC_CLK_TCK')
    raise OSError('unsupported recorded kernel start format')
