"""Bounded independent kernel observations for interpreter provenance only."""
import ctypes
import os
from pathlib import Path
import sys
from .activity_process import identity,process_cwd,BSDInfo
from .validator_origin import capture,safe


def process_arguments(pid):
    if type(pid) is not int or pid<=0:raise ValueError('invalid readiness process PID')
    if sys.platform.startswith('linux'):
        raw=Path('/proc/'+str(pid)+'/cmdline').read_bytes()
        if len(raw)>1024*1024 or not raw.endswith(b'\0'):raise ValueError('unavailable bounded process argv')
        return [os.fsdecode(v) for v in raw[:-1].split(b'\0')]
    if sys.platform!='darwin':raise ValueError('unsupported readiness process observation platform')
    libc=ctypes.CDLL(None,use_errno=True);mib=(ctypes.c_int*3)(1,49,pid);size=ctypes.c_size_t()
    if libc.sysctl(mib,3,None,ctypes.byref(size),None,0)!=0 or not 4<size.value<=1024*1024:
        raise OSError(ctypes.get_errno(),'readiness process arguments unavailable')
    buf=ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib,3,buf,ctypes.byref(size),None,0)!=0:raise OSError(ctypes.get_errno(),'readiness process arguments denied')
    argc=ctypes.c_int.from_buffer(buf).value;raw=buf.raw[4:size.value]
    if not 0<argc<=1024 or b'\0' not in raw:raise ValueError('invalid readiness process argument framing')
    tail=raw.split(b'\0',1)[1].lstrip(b'\0');parts=tail.split(b'\0')
    if len(parts)<=argc or not all(parts[:argc]):raise ValueError('incomplete readiness process arguments')
    return [os.fsdecode(v) for v in parts[:argc]]


def kernel_image(pid):
    if sys.platform.startswith('linux'):
        name=os.readlink('/proc/'+str(pid)+'/exe');mechanism='linux-proc-exe-v1'
    elif sys.platform=='darwin':
        library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
        buffer=ctypes.create_string_buffer(4096)
        count=library.proc_pidpath(pid,buffer,len(buffer))
        if not 0<count<len(buffer) or not buffer.value:raise OSError(ctypes.get_errno(),'kernel executable image unavailable')
        name=os.fsdecode(buffer.value);mechanism='darwin-proc-pidpath-v1'
    else:raise ValueError('unsupported interpreter image observation platform')
    path=Path(name)
    if not path.is_absolute() or path!=path.resolve(strict=True):raise ValueError('noncanonical kernel executable image')
    _,metadata=capture(safe(path))
    return dict(metadata,path=str(path),platform=sys.platform,mechanism=mechanism)


def parent_pid(pid):
    if sys.platform.startswith('linux'):
        fields=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()
        return int(fields[1])
    if sys.platform!='darwin':raise ValueError('unsupported interpreter parent observation platform')
    library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True);value=BSDInfo()
    if library.proc_pidinfo(pid,3,0,ctypes.byref(value),ctypes.sizeof(value))!=ctypes.sizeof(value):
        raise OSError(ctypes.get_errno(),'kernel parent unavailable')
    return int(value.ppid)


def observe(pid):
    before=identity(pid)
    if not before or before['zombie']:raise ValueError('interpreter process unavailable')
    def read():
        cwd=process_cwd(pid)
        if cwd is None:raise ValueError('interpreter cwd unavailable')
        return {'identity':before,'argv':process_arguments(pid),'image':kernel_image(pid),
                'cwd':str(cwd),'parent_pid':parent_pid(pid)}
    first=read();second=read()
    if first!=second or identity(pid)!=before:raise ValueError('interpreter process observation changed')
    return first
