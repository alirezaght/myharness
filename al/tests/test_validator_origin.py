"""Protected snapshot identity and closed original-resource provenance."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from al.installer import validator_origin as V
from al.installer.bootstrap import bootstrap_cli
from al.tests.conftest import copy_al_source


@pytest.fixture
def installed(tmp_path):
    source = copy_al_source(tmp_path/'source/al')
    launcher = tmp_path/'bin/alctl'
    result = bootstrap_cli(source, launcher, tmp_path/'state')
    return source,launcher,result


def test_same_bytes_at_different_roots_never_share_original_origin(tmp_path):
    first = copy_al_source(tmp_path/'first/al')
    second = copy_al_source(tmp_path/'second/al')
    a,b = V.capture_canonical(first),V.capture_canonical(second)
    assert a['bytes']==b['bytes'] and a['origin']['closure_digest']==b['origin']['closure_digest']
    assert a['origin']['origin_digest']!=b['origin']['origin_digest']
    assert a['expected']['snapshot_identity']!=b['expected']['snapshot_identity']


@pytest.mark.parametrize('damage',['origin','receipt','module','reference','extra','missing','symlink','origin-mode','receipt-mode'])
def test_snapshot_tampering_cannot_recertify_itself(installed, damage):
    source,launcher,result = installed
    root = Path(result['snapshot'])
    if damage in {'origin-mode','receipt-mode'}:
        (root/(damage.split('-')[0]+'.json')).chmod(0o644)
    elif damage in {'origin','receipt'}:
        path = root/(damage+'.json');value=json.loads(path.read_bytes())
        if damage=='origin':value['source']['al_root']=str(source.parent/'foreign')
        else:value['source_root']=str(source.parent/'foreign')
        path.write_text(json.dumps(value))
    elif damage=='extra':(root/'injected.py').write_text('raise RuntimeError("unbound")\n')
    else:
        name = 'codex/skills/al/scripts/al_store.py' if damage!='reference' else next(n for n in result['receipt']['files'] if '/reference/' in n)
        path = root/name
        if damage=='missing':path.unlink()
        elif damage=='symlink':path.unlink();path.symlink_to(source/name)
        else:path.write_bytes(path.read_bytes()+b'\n# changed\n')
    with pytest.raises(ValueError):V.verify_snapshot(root,result['validator_binding'])
    observed = subprocess.run([str(launcher),'--help'],capture_output=True,text=True,timeout=30)
    assert observed.returncode != 0
    assert len(observed.stdout)<1000


@pytest.mark.parametrize('damage',['runner','reference','missing','member','interpreter'])
def test_original_resources_remain_data_with_exact_membership(installed,damage):
    source,launcher,result = installed
    captured = V.verify_snapshot(result['snapshot'],result['validator_binding'])
    if damage=='member':(source/'codex/skills/al/scripts/al_unreviewed.py').write_text('x=1\n')
    elif damage=='interpreter':captured['origin']['interpreter']['executable']='/foreign/python'
    else:
        name = 'codex/skills/al/scripts/al_architecture_check.py' if damage!='reference' else next(n for n in result['receipt']['files'] if '/reference/' in n)
        path = source/name
        if damage=='missing':path.unlink()
        else:path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError):V.verify_origin_data(captured['origin'],captured['bytes'])
    # Ordinary tagged CLI operations do not require origin data to stay present.
    result = subprocess.run([str(launcher),'--help'],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr[-1500:]


def run_control(body, root, *args):
    program = "import sys;sys.path.insert(0,sys.argv[1]);"+body
    result = subprocess.run([sys.executable,'-I','-S','-B','-c',program,str(root),*args],
                            capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr[-3000:]+result.stdout[-1000:]


@pytest.mark.parametrize('mode',['unknown','preloaded','resource','rebind','truthful'])
def test_closed_loader_has_no_target_or_environment_fallback(installed,tmp_path,mode):
    source,_,_ = installed
    marker = tmp_path/'marker'
    (tmp_path/'al_unknown.py').write_text('open('+repr(str(marker))+',"w").write("executed")')
    body = '''
from installer.validator_origin import capture_canonical
from installer.validator_loader import install_closed_loader,verify_loaded_modules
import types
captured=capture_canonical(sys.argv[1])
mode=sys.argv[2]
if mode=='preloaded':sys.modules['al_store']=types.ModuleType('al_store')
try:
 loader=install_closed_loader(captured,'codex')
 if mode=='preloaded':raise AssertionError('foreign preload accepted')
 if mode=='unknown':
  sys.path.insert(0,sys.argv[3]);__import__('al_unknown');raise AssertionError('fallback accepted')
 import al_runtime_resources as resources
 if mode=='resource':resources.resource_identity('../al_store.py');raise AssertionError('unknown resource accepted')
 if mode=='rebind':
  resources.implementation_digest();resources._bind_verified_context({});raise AssertionError('late bind accepted')
 if mode=='truthful':
  import al_models
  assert al_models.__file__==str(__import__('pathlib').Path(sys.argv[1])/'codex/skills/al/scripts/al_models.py')
  assert verify_loaded_modules(loader)['al_models']['origin']==al_models.__file__
except (ValueError,ImportError):
 if mode=='truthful':raise
'''
    run_control(body,source,mode,str(tmp_path))
    assert not marker.exists()


@pytest.mark.parametrize('operation',['write','gate','git-mutation','git-external'])
def test_probe_observational_boundary_denies_mutation_and_gate_execution(tmp_path,operation):
    root = Path(__file__).resolve().parents[1]
    marker = tmp_path/'marker'
    body = '''
from installer.validator_readonly import Readonly
from pathlib import Path
import subprocess
readonly=Readonly();readonly.start()
try:
 operation=sys.argv[2]
 if operation=='write':Path(sys.argv[3]).write_text('forbidden')
 if operation=='gate':subprocess.run([sys.executable,'-c','print("gate")'])
 if operation=='git-mutation':subprocess.run(['git','reset','--hard'])
 if operation=='git-external':subprocess.run(['git','diff','--ext-diff'])
 raise AssertionError('mutation accepted')
except PermissionError:pass
'''
    run_control(body,root,operation,str(marker))
    assert not marker.exists()


def test_read_set_detects_actual_external_input_drift(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path/'input';path.write_text('before')
    body = '''
from installer.validator_readonly import Readonly
from pathlib import Path
import subprocess
child=subprocess.Popen([sys.executable,'-I','-S','-c',"import sys;sys.stdin.read(1);open(sys.argv[1],'w').write('after')",sys.argv[2]],stdin=subprocess.PIPE)
readonly=Readonly();readonly.start()
assert Path(sys.argv[2]).read_text()=='before'
child.stdin.write(b'x');child.stdin.close()
assert child.wait(timeout=10)==0
try:readonly.verify();raise AssertionError('input drift accepted')
except ValueError:pass
'''
    run_control(body,root,str(path))


@pytest.mark.parametrize('argv',[
    ['cat-file','--filters','HEAD:secret'],
    ['cat-file','--batch','--filters'],
    ['cat-file','--textconv','HEAD:secret'],
    ['diff','--no-ext-diff','--no-textconv','--output=secret','HEAD'],
    ['show','--ext-diff','HEAD'],
    ['-c','core.sshCommand=evil','rev-parse','HEAD'],
    ['--config-env=core.fsmonitor=EVIL','status','--porcelain'],
])
def test_git_audit_rejects_external_and_unknown_execution_grammar(tmp_path,argv):
    root=Path(__file__).resolve().parents[1]
    body='''
from installer.validator_readonly import Readonly
import subprocess,json
import os
for key in list(os.environ):
 if key.startswith('GIT_'):os.environ.pop(key)
os.environ.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_OPTIONAL_LOCKS='0',GIT_NO_REPLACE_OBJECTS='1',GIT_CONFIG_COUNT='2',GIT_CONFIG_KEY_0='core.hooksPath',GIT_CONFIG_VALUE_0=os.devnull,GIT_CONFIG_KEY_1='core.fsmonitor',GIT_CONFIG_VALUE_1='false')
readonly=Readonly();readonly.start()
try:subprocess.run(['git',*json.loads(sys.argv[2])]);raise AssertionError('unsafe Git accepted')
except PermissionError:pass
'''
    run_control(body,root,json.dumps(argv))


def test_stdlib_optional_absence_is_precise_without_import_fallthrough(installed):
    source,_,_ = installed
    body='''
from installer.validator_origin import capture_canonical
from installer.validator_loader import install_closed_loader
import importlib.abc
captured=capture_canonical(sys.argv[1]);calls=[]
class ForbiddenFallback(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  calls.append(fullname);raise AssertionError('import fell through')
sys.modules.pop('subprocess',None)
install_closed_loader(captured,'codex')
sys.meta_path.append(ForbiddenFallback())
import subprocess
assert callable(subprocess.run)
try:__import__('genuinely_absent_validator_module');raise AssertionError('missing module accepted')
except ModuleNotFoundError as exc:assert exc.name=='genuinely_absent_validator_module'
try:__import__('al_unbound');raise AssertionError('unknown AL accepted')
except ImportError:pass
assert not calls
'''
    run_control(body,source)


def test_wrong_platform_mapping_is_not_authenticated_by_its_own_digest(installed):
    _,_,result=installed
    captured=V.verify_snapshot(result['snapshot'],result['validator_binding'])
    origin=captured['origin'];origin['platforms']['codex']=origin['platforms']['claude']
    origin['origin_digest']=V.digest(V.canonical({k:v for k,v in origin.items() if k!='origin_digest'}))
    with pytest.raises(ValueError,match='mapping/identity'):V.verify_origin(origin)


def test_actual_interpreter_mismatch_is_refused_after_origin_resources_match(installed,monkeypatch):
    _,_,result=installed
    captured=V.verify_snapshot(result['snapshot'],result['validator_binding'])
    changed=dict(V.interpreter_identity(),executable='/different/interpreter')
    monkeypatch.setattr(V,'interpreter_identity',lambda:changed)
    with pytest.raises(ValueError,match='interpreter identity changed'):
        V.verify_origin_data(captured['origin'],captured['bytes'])


def test_graph_cache_resource_binding_distinguishes_physical_origin_and_platform(tmp_path):
    first=copy_al_source(tmp_path/'one/al');second=copy_al_source(tmp_path/'two/al')
    results=[]
    body='''
from installer.validator_origin import capture_canonical
from installer.validator_loader import install_closed_loader
import json
captured=capture_canonical(sys.argv[1]);platform=sys.argv[2]
loader=install_closed_loader(captured,platform)
import al_runtime_resources as resources
mapping=captured['origin']['platforms'][platform]['resources']
resources._bind_verified_context({'platform':platform,'closure_digest':captured['origin']['closure_digest'],'origin_digest':captured['origin']['origin_digest'],'bytes':{n:captured['bytes'][p] for n,p in mapping.items()},'identities':{n:str(__import__('pathlib').Path(sys.argv[1])/p) for n,p in mapping.items()}})
print(json.dumps(resources.binding_identity()))
'''
    for source,platform in ((first,'codex'),(second,'codex'),(first,'claude')):
        program="import sys;sys.path.insert(0,sys.argv[1]);"+body
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',program,str(source),platform],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr[-2000:]
        results.append(json.loads(result.stdout))
    assert results[0]['origin_digest']!=results[1]['origin_digest']
    assert results[0]['physical_scripts_root']!=results[1]['physical_scripts_root']
    assert results[0]['platform']!=results[2]['platform']
