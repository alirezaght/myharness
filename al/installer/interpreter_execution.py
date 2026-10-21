"""Protected launcher-to-image relationship from a fixed isolated probe."""
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from .execution_process import observe
from .activity_process import identity
from .validator_origin import canonical,capture,decode,digest,interpreter_identity

PROBE=r'''
import ctypes,json,os,sys
if sys.platform=='darwin':
    library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    buffer=ctypes.create_string_buffer(4096)
    count=library.proc_pidpath(os.getpid(),buffer,len(buffer))
    if not 0<count<len(buffer) or not buffer.value:raise OSError(ctypes.get_errno(),'probe image unavailable')
    image=os.fsdecode(buffer.value)
elif sys.platform.startswith('linux'):image=os.readlink('/proc/self/exe')
else:raise RuntimeError('unsupported image platform')
print(json.dumps({'pid':os.getpid(),'parent_pid':os.getppid(),'cwd':os.path.realpath(os.getcwd()),'image':image,'platform':sys.platform}),flush=True)
if sys.stdin.readline()!='finish\n':raise RuntimeError('invalid probe completion')
'''


def launcher(expected):
    current=interpreter_identity()
    if current!=expected:raise ValueError('requested interpreter identity changed')
    return current


def _response(child):
    deadline=time.monotonic()+10;raw=b''
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout,selectors.EVENT_READ)
        while b'\n' not in raw:
            remaining=deadline-time.monotonic()
            if remaining<=0 or not selector.select(remaining):raise ValueError('interpreter probe timed out')
            part=os.read(child.stdout.fileno(),65537-len(raw))
            if not part or len(raw)+len(part)>65536:raise ValueError('invalid bounded interpreter probe response')
            raw+=part
    if not raw.endswith(b'\n') or raw.count(b'\n')!=1:raise ValueError('ambiguous interpreter probe response')
    return decode(raw)


def probe(requested,expected):
    launcher(expected)
    path=Path(requested)
    if not path.is_absolute() or str(path.resolve(strict=True))!=expected['canonical_executable']:
        raise ValueError('probe requested interpreter is not pinned')
    # No target code, import paths, startup hooks, environment interpreter settings,
    # or arbitrary script can enter the fixed isolated probe.
    argv=[str(path),'-I','-S','-B','-c',PROBE]
    child=subprocess.Popen(argv,cwd='/',env={'PATH':'/usr/bin:/bin'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    owned=None;owned_start=None
    try:
        owned_start=identity(child.pid)
        if not owned_start:raise ValueError('probe start identity unavailable')
        response=_response(child);owned=observe(child.pid)
        expected_response={'pid':child.pid,'parent_pid':os.getpid(),'cwd':'/','image':owned['image']['path'],'platform':sys.platform}
        if owned['identity']!=owned_start or response!=expected_response or owned['parent_pid']!=os.getpid() or owned['cwd']!='/' or owned['argv'][1:]!=argv[1:]:
            raise ValueError('independent interpreter probe observation disagrees')
        if observe(child.pid)!=owned:raise ValueError('interpreter probe changed before completion')
        output,error=child.communicate(b'finish\n',timeout=10)
        if child.returncode!=0 or output or error:raise ValueError('interpreter probe did not complete successfully')
        launcher(expected)
        return {'schema_version':1,'kind':'al-interpreter-execution-profile','profile':'isolated-python-kernel-image-v1',
                'requested_executable':str(path),'probe_argv':argv,'probe_sha256':digest(PROBE.encode()),
                'image':owned['image'],'observation':owned,'response':response,'exit_code':0}
    finally:
        if child.poll() is None:
            # This fixed probe cannot spawn descendants. Closing its pipe asks it
            # to exit; any signal targets only the still-owned exact child.
            if child.stdin and not child.stdin.closed:child.stdin.close();child.stdin=None
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if owned_start is None or identity(child.pid)!=owned_start:raise ValueError('probe cleanup identity unavailable or changed')
                child.kill();child.wait(timeout=2)
        for stream in (child.stdin,child.stdout,child.stderr):
            if stream and not stream.closed:stream.close()


def capture_profile(requested,expected,requested_spelling):
    value=probe(requested,expected)
    value['requested_argv0']=requested_spelling
    value['argv0_forms']=sorted({requested_spelling,expected['executable'],expected['canonical_executable'],value['observation']['argv'][0]})
    validate_profile(value,expected)
    return value


def validate_profile(value,expected):
    fields={'schema_version','kind','profile','requested_executable','probe_argv','probe_sha256','image','observation','response','exit_code','argv0_forms','requested_argv0'}
    if not isinstance(value,dict) or set(value)!=fields:raise ValueError('missing or invalid protected interpreter execution profile')
    if (value['schema_version'],value['kind'],value['profile'],value['exit_code'])!=(1,'al-interpreter-execution-profile','isolated-python-kernel-image-v1',0):
        raise ValueError('unsupported interpreter execution profile')
    path=Path(value['requested_executable'])
    # Stored provenance can be verified after its original issuer is removed;
    # launcher/image resources are independently revalidated at live admission.
    if not path.is_absolute() or value['probe_argv']!=[str(path),'-I','-S','-B','-c',PROBE] or value['probe_sha256']!=digest(PROBE.encode()):
        raise ValueError('interpreter fixed probe provenance differs')
    image=value['image'];observed=value['observation'];response=value['response']
    if set(image)!={'path','sha256','mode','platform','mechanism'} or not Path(image['path']).is_absolute():raise ValueError('invalid protected image identity')
    mechanisms={'darwin':'darwin-proc-pidpath-v1','linux':'linux-proc-exe-v1'}
    if image['platform'] not in mechanisms or image['mechanism']!=mechanisms[image['platform']]:raise ValueError('unsupported protected kernel mechanism')
    forms=sorted({value['requested_argv0'],expected['executable'],expected['canonical_executable'],observed['argv'][0]})
    if observed['image']!=image or observed['cwd']!='/' or observed['argv'][1:]!=value['probe_argv'][1:] or observed['identity']['pid']!=response['pid'] or observed['parent_pid']!=response['parent_pid']:
        raise ValueError('protected interpreter observation differs')
    if response!={'pid':observed['identity']['pid'],'parent_pid':observed['parent_pid'],'cwd':'/','image':image['path'],'platform':image['platform']}:
        raise ValueError('protected interpreter self report differs')
    if value['argv0_forms']!=forms or observed['argv'][0] not in value['argv0_forms'] or not all(isinstance(v,str) and v for v in value['argv0_forms']):
        raise ValueError('invalid protected interpreter argv framing')


def validate_live(profile,expected,observed):
    validate_profile(profile,expected);launcher(expected)
    _,metadata=capture(Path(profile['requested_executable']).resolve(strict=True))
    if str(Path(profile['requested_executable']).resolve(strict=True))!=expected['canonical_executable'] or metadata!={k:expected[k] for k in ('sha256','mode')}:
        raise ValueError('requested interpreter launcher changed')
    if observed['image']!=profile['image']:raise ValueError('live interpreter differs from protected kernel image')
    if observed['argv'][0] not in profile['argv0_forms']:raise ValueError('unbound interpreter executable spelling')
