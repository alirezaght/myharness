"""Fixed isolated run bootstrap, before any repository/runtime module import."""

from .validator_boot import BOOT


CHILD = (
    BOOT
    + r"""
request=REQUEST
transaction=request['transaction']
base=Path(request['storage'])
run_context=types.ModuleType('_al_run_pin_context');run_context.transaction=transaction;run_context.storage=str(base)
run_context.readonly=bool(request.get('existing_run'))
sys.modules['_al_run_pin_context']=run_context
pin=transaction['pin'];root=base/'pins'/pin['pin_identity']
raw,state=_read(root/'manifest.json')
if state['mode']!=0o600 or _hash(raw)!=pin['pin_manifest_digest']:raise ValueError('protected pin manifest changed')
manifest=json.loads(raw)
if manifest['pin_identity']!=pin['pin_identity'] or _hash(_json({k:v for k,v in manifest.items() if k!='pin_identity'}))!=pin['pin_identity']:raise ValueError('protected pin identity differs')
files=manifest['complete_file_manifest'];payload=root/'payload';captured={};actual=set()
for path in root.rglob('*'):
    if path.is_symlink():raise ValueError('runtime pin symlink')
    if path.is_file():actual.add(path.relative_to(root).as_posix())
    elif not path.is_dir():raise ValueError('runtime pin special member')
if actual!={'manifest.json'}|{'payload/'+name for name in files}:raise ValueError('runtime pin membership changed')
for name,item in files.items():
    relative=Path(name)
    if relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=name:raise ValueError('unsafe pin member')
    content,state=_read(payload/relative)
    if state!={k:item[k] for k in ('sha256','mode')}:raise ValueError('runtime pin member changed')
    captured[name]=content
platform=transaction['platform'];prefix=platform+'/skills/al/scripts/'
modules={Path(name).stem:name for name in files if name.startswith(prefix) and name.endswith('.py') and '/' not in name[len(prefix):]}
context={'snapshot':str(payload),'origin':{'platforms':{platform:{'modules':modules}}},'bytes':captured}
filename=str(payload/'installer/validator_loader.py')
namespace={'__name__':'_pin_closed_loader','__file__':filename}
exec(compile(captured['installer/validator_loader.py'],filename,'exec'),namespace)
loader=namespace['install_closed_loader'](context,platform,installers=True)
from installer.runtime_pin import verify_pin
from installer.runtime_pin_transaction import subject,read_state,commit_run_binding,coordinated,read,validate
from installer.interpreter_execution import validate_live
from installer.execution_process import observe
verified=verify_pin(base/'pins',pin)
if verified['bytes']!=captured:raise ValueError('runtime pin moved before import')
value=subject(transaction['repository_identity']['root'],platform,transaction['session_id'])
with coordinated(value) as path:
    if read(path)!=transaction:raise ValueError('protected run transaction changed')
validate(transaction,value)
validate_live(manifest['interpreter_execution_identity'],manifest['interpreter'],observe(os.getpid()))
old=read_state(transaction)
if run_context.readonly and (transaction['status']!='ACTIVE' or old is None):raise ValueError('existing-run requires matching ACTIVE state')
if transaction['status']=='ACTIVE' and old is None:raise ValueError('active runtime state missing')
if transaction['status']=='RESERVED' and old is not None:
    commit_run_binding(transaction)
    transaction=dict(transaction,status='ACTIVE');run_context.transaction=transaction
    if request['argv'][0]=='start':
        print('AL start recovered exact published run '+transaction['session_id'])
        raise SystemExit(0)
elif request['argv'][0]!='start' and old is None:raise ValueError('runtime start transaction has not published state')
import al_runtime_resources
resources={};identities={}
for logical,name in manifest['resource_map'].items():
    key=logical[len('scripts/'):] if logical.startswith('scripts/') else logical
    resources[key]=captured[name];identities[key]=str(payload/name)
al_runtime_resources._bind_verified_context({'identities':identities,'bytes':resources,'platform':platform,'closure_digest':manifest['closure_digest'],'origin_digest':manifest['pin_identity']})
import al_state
# A Claude conversation's implicit command keeps al_state's native selection and pointer path.
explicit=[] if request.get('native_implicit') else ['--session',transaction['session_id']]
sys.argv=[str(payload/prefix/'al_state.py'),*explicit,*request['argv']]
al_state.main()
transaction=run_context.transaction
if run_context.readonly:
    with coordinated(value) as path:
        if read(path)!=request['transaction']:raise ValueError('existing-run protected binding changed')
    if read_state(transaction) is None:raise ValueError('existing-run state disappeared')
elif read_state(transaction) is not None:commit_run_binding(transaction)
"""
)


def program(request):
    return CHILD.replace("request=REQUEST", "request=" + repr(request), 1)
