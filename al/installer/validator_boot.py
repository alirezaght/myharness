"""Preamble embedded in the managed launcher and isolated probe argv."""
import shlex

BOOT = r'''
import hashlib,json,os,stat,sys,types
from pathlib import Path
sys.dont_write_bytecode=True

def _hash(raw):return hashlib.sha256(raw).hexdigest()
def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def _read(path):
    path=Path(path)
    for entry in [*reversed(path.parents),path]:
        if entry.is_symlink():raise ValueError('unsafe validator bootstrap path')
    before=path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size>16*1024*1024:raise ValueError('invalid validator bootstrap file')
    raw=path.read_bytes();after=path.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mode,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mode,after.st_mtime_ns):raise ValueError('validator bootstrap moved')
    return raw,{'sha256':_hash(raw),'mode':stat.S_IMODE(before.st_mode)}
def _boot(request):
    root=Path(request['root']);expected=request['expected']
    if request['mode']=='snapshot':
        origin_raw,origin_state=_read(root/'origin.json');receipt_raw,receipt_state=_read(root/'receipt.json')
        if origin_state['mode']!=0o600 or receipt_state['mode']!=0o600:raise ValueError('protected validator metadata mode changed')
        if _hash(origin_raw)!=expected['origin_sha256'] or _hash(receipt_raw)!=expected['receipt_sha256']:raise ValueError('protected validator metadata changed')
        origin=json.loads(origin_raw)
    elif request['mode']=='canonical':origin=request['origin']
    else:raise ValueError('unknown validator bootstrap mode')
    if _hash(_json({k:v for k,v in origin.items() if k!='origin_digest'}))!=expected['origin_digest']:raise ValueError('protected validator origin changed')
    if _hash(_json(origin['closure']))!=origin['closure_digest']:raise ValueError('validator closure digest changed')
    identity=_hash(_json({'schema_version':1,'closure_digest':origin['closure_digest'],'origin_digest':origin['origin_digest']}))
    if identity!=expected['snapshot_identity']:raise ValueError('validator snapshot identity changed')
    names=set(origin['closure']);captured={}
    if request['mode']=='snapshot':
        actual=set()
        for p in root.rglob('*'):
            if p.is_symlink():raise ValueError('validator bootstrap symlink')
            if p.is_file():actual.add(p.relative_to(root).as_posix())
            elif not p.is_dir():raise ValueError('validator bootstrap special member')
        if actual!=names|{'origin.json','receipt.json'}:raise ValueError('validator bootstrap membership changed')
    for name,item in origin['closure'].items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=name:raise ValueError('unsafe validator member')
        raw,state=_read(root/relative)
        if state!={k:item[k] for k in ('sha256','mode')}:raise ValueError('validator bootstrap resource changed: '+name)
        captured[name]=raw
    value={'snapshot':str(root),'origin':origin,'bytes':captured,'expected':expected,'mode':request['mode']}
    context=types.ModuleType('_al_validator_context');context.captured=value;context.request=request
    sys.modules['_al_validator_context']=context
    return value

def _utility(captured,name):
    filename=str(Path(captured['snapshot'])/'installer'/name)
    module=types.ModuleType('_validator_'+Path(name).stem);module.__file__=filename
    exec(compile(captured['bytes']['installer/'+name],filename,'exec'),module.__dict__)
    return module
'''

CHILD = BOOT + r'''
request=json.loads(sys.stdin.buffer.read(16*1024*1024+1))
captured=_boot(request)
filename=str(Path(captured['snapshot'])/'installer/terminal_probe.py')
namespace={'__name__':'__main__','__file__':filename}
exec(compile(captured['bytes']['installer/terminal_probe.py'],filename,'exec'),namespace)
'''


def launcher(request):
    interpreter = shlex.quote(request['interpreter'])
    return ('#!/bin/sh\n' + "'''exec' " + interpreter + ' -I -S -B "$0" "$@"\n' + "' '''\n" + BOOT + '\n' +
            'captured=_boot('+repr(request)+')\n' +
            "utility=_utility(captured,'validator_loader.py')\n" +
            "utility.install_closed_loader(captured,'codex',installers=True)\n" +
            "filename=str(Path(captured['snapshot'])/'alctl.py')\n" +
            "exec(compile(captured['bytes']['alctl.py'],filename,'exec'),{'__name__':'__main__','__file__':filename})\n").encode()
