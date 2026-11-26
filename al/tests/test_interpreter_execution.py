"""Two bound interpreter identities and independently observed probe provenance."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from al.installer import execution_process as processes
from al.installer import interpreter_execution as execution
from al.installer.issuer_provenance import capture_issuer,retain_issuer,verify_issuer
from al.installer.validator_origin import capture,decode,executable_state,interpreter_identity
from al.tests.test_issuer_provenance import fixture


@pytest.fixture(scope='module')
def pinned():
    expected=interpreter_identity()
    return expected,execution.capture_profile(sys.executable,expected,sys.executable)


def test_real_native_profile_matches_independent_observation(pinned):
    expected,profile=pinned
    observed=processes.observe(os.getpid())
    execution.validate_live(profile,expected,observed)
    assert profile['response']['image']==observed['image']['path']
    assert profile['probe_argv']==[sys.executable,'-I','-S','-B','-c',execution.PROBE]
    assert profile['observation']['identity']['pid']==profile['response']['pid']
    assert profile['image']['mechanism']==('darwin-proc-pidpath-v1' if sys.platform=='darwin' else 'linux-proc-exe-v1')


@pytest.mark.parametrize('field',['path','sha256','mode','platform','mechanism'])
def test_shared_foreign_image_cannot_replace_protected_expected_image(pinned,field):
    expected,profile=pinned;observed=processes.observe(os.getpid())
    observed['image'][field]={'path':'/foreign/same-python','sha256':'0'*64,'mode':0o600,'platform':'foreign','mechanism':'guessed'}[field]
    # This represents BOTH owner and caller having the same foreign image;
    # their equality does not grant authority relative to the protected profile.
    for same_image in (observed,deepcopy(observed)):
        with pytest.raises(ValueError,match='protected kernel image'):execution.validate_live(profile,expected,same_image)


@pytest.mark.parametrize('field',['canonical_executable','sha256','mode','executable'])
def test_requested_launcher_identity_is_independently_pinned(pinned,field):
    expected,profile=pinned;changed=deepcopy(expected)
    changed[field]=0o600 if field=='mode' else '/unbound/python' if 'executable' in field else '0'*64
    with pytest.raises(ValueError):execution.validate_live(profile,changed,processes.observe(os.getpid()))


def test_unbound_argv0_spelling_refuses_without_path_lookup(pinned):
    expected,profile=pinned;observed=processes.observe(os.getpid());observed['argv'][0]='same-python-version'
    with pytest.raises(ValueError,match='unbound'):execution.validate_live(profile,expected,observed)


def test_forged_child_report_disagrees_with_independent_kernel_observation(monkeypatch):
    original=execution._response
    def forged(child):
        value=original(child);value['image']='/forged/self-report';return value
    monkeypatch.setattr(execution,'_response',forged)
    with pytest.raises(ValueError,match='independent'):execution.capture_profile(sys.executable,interpreter_identity(),sys.executable)


@pytest.mark.parametrize('change',['identity','argv','image','cwd','parent'])
def test_paired_observations_refuse_mutation(monkeypatch,change):
    real=processes.observe(os.getpid());calls={}
    monkeypatch.setattr(processes,'identity',lambda pid:dict(real['identity'],start=[999]) if change=='identity' else real['identity'])
    def next_value(name,value):
        calls[name]=calls.get(name,0)+1
        if calls[name]>1 and change==name:return '/changed' if name=='cwd' else [*value,'extra'] if name=='argv' else dict(value,sha256='0'*64) if name=='image' else 999
        return deepcopy(value)
    monkeypatch.setattr(processes,'process_arguments',lambda pid:next_value('argv',real['argv']))
    monkeypatch.setattr(processes,'kernel_image',lambda pid:next_value('image',real['image']))
    monkeypatch.setattr(processes,'process_cwd',lambda pid:Path(next_value('cwd',real['cwd'])))
    monkeypatch.setattr(processes,'parent_pid',lambda pid:next_value('parent',real['parent_pid']))
    if change=='identity':
        identity_calls=[0]
        def changing(pid):
            identity_calls[0]+=1
            return real['identity'] if identity_calls[0]==1 else dict(real['identity'],start=[999])
        monkeypatch.setattr(processes,'identity',changing)
    with pytest.raises(ValueError,match='changed'):processes.observe(os.getpid())


@pytest.mark.parametrize('error',[PermissionError('denied'),ValueError('malformed kernel response')])
def test_uncertain_kernel_observation_refuses(monkeypatch,error):
    def fail(pid):raise error
    monkeypatch.setattr(processes,'kernel_image',fail)
    with pytest.raises(type(error)):processes.observe(os.getpid())


def test_unsupported_platform_refuses(monkeypatch):
    monkeypatch.setattr(processes,'sys',SimpleNamespace(platform='unsupported'))
    with pytest.raises(ValueError,match='unsupported'):processes.kernel_image(os.getpid())


def test_linux_mechanism_simulation_uses_exact_proc_path(monkeypatch):
    # Mechanism test on this host, not a claim of actual Linux-host execution.
    image=Path(sys.executable).resolve();seen=[]
    monkeypatch.setattr(processes,'sys',SimpleNamespace(platform='linux'))
    monkeypatch.setattr(processes,'os',SimpleNamespace(readlink=lambda path:(seen.append(path) or str(image))))
    result=processes.kernel_image(123)
    assert seen==['/proc/123/exe']
    assert result==dict(executable_state(image),path=str(image),platform='linux',mechanism='linux-proc-exe-v1')


def test_new_capture_references_but_never_mutates_v1(tmp_path):
    root,args,_=fixture(tmp_path)
    v1=retain_issuer(capture_issuer(root,**args),tmp_path/'v1')
    before={p.relative_to(v1['root']).as_posix():(p.read_bytes(),p.stat().st_mode) for p in Path(v1['root']).rglob('*') if p.is_file()}
    captured=capture_issuer(root,**args,execution_profile=True,previous_binding=v1)
    v2=retain_issuer(captured,tmp_path/'v2');data=decode(capture(Path(v2['root'])/'provenance.json')[0])
    assert data['schema_version']==2 and data['previous_capture']['capture_digest']==v1['capture_digest']
    assert 'execution_profile' not in decode(capture(Path(v1['root'])/'provenance.json')[0])['invocation']
    assert before=={p.relative_to(v1['root']).as_posix():(p.read_bytes(),p.stat().st_mode) for p in Path(v1['root']).rglob('*') if p.is_file()}
    assert verify_issuer(v2['root'],v2,observe_live=True)['capture_digest']==v2['capture_digest']
    path=Path(v2['root'])/'provenance.json';data['invocation']['execution_profile']['image']['path']='/foreign/python'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):verify_issuer(v2['root'],v2)


def private_launch_resources(tmp_path, pinned):
    # Structural profile fixture for resource admission only; never executable authority.
    expected, profile = map(deepcopy, pinned)
    target = tmp_path/'python-image'; target.write_bytes(b'private launcher'); target.chmod(0o755)
    requested = tmp_path/'python'; requested.symlink_to(target)
    image = tmp_path/'kernel-image'; image.write_bytes(b'private kernel image'); image.chmod(0o755)
    expected.update(executable=str(requested), canonical_executable=str(target), **executable_state(target))
    profile['requested_executable'] = profile['requested_argv0'] = str(requested)
    profile['probe_argv'] = [str(requested),'-I','-S','-B','-c',execution.PROBE]
    profile['image'].update(path=str(image), **executable_state(image))
    profile['observation']['image'] = deepcopy(profile['image'])
    profile['observation']['argv'] = list(profile['probe_argv'])
    profile['response']['image'] = str(image)
    profile['argv0_forms'] = sorted({str(requested),str(target)})
    return expected, profile, requested, target, image


def test_launch_resource_admission_does_not_compare_the_current_issuer(tmp_path, pinned, monkeypatch):
    expected, profile, requested, _, _ = private_launch_resources(tmp_path, pinned)
    def forbidden(*args): raise AssertionError('launch admission used live issuer/probe')
    monkeypatch.setattr(execution, 'launcher', forbidden)
    monkeypatch.setattr(execution.subprocess, 'Popen', forbidden)
    execution.validate_launch(profile, expected, str(requested))


@pytest.mark.parametrize('mutation', ['missing-launcher','rebound-launcher','launcher-bytes',
    'launcher-mode','missing-image','rebound-image','image-bytes','image-mode','unbound-argument'])
def test_pinned_launch_resource_changes_refuse_before_execution(tmp_path, pinned, monkeypatch, mutation):
    expected, profile, requested, target, image = private_launch_resources(tmp_path, pinned)
    argument = str(requested)
    if mutation == 'missing-launcher': target.unlink()
    elif mutation == 'rebound-launcher':
        other = tmp_path/'other-launcher'; other.write_bytes(target.read_bytes()); other.chmod(0o755)
        requested.unlink(); requested.symlink_to(other)
    elif mutation == 'launcher-bytes': target.write_bytes(b'changed launcher')
    elif mutation == 'launcher-mode': target.chmod(0o700)
    elif mutation == 'missing-image': image.unlink()
    elif mutation == 'rebound-image':
        other = tmp_path/'other-image'; other.write_bytes(image.read_bytes()); other.chmod(0o755)
        image.unlink(); image.symlink_to(other)
    elif mutation == 'image-bytes': image.write_bytes(b'changed image')
    elif mutation == 'image-mode': image.chmod(0o700)
    else: argument = str(target)
    def forbidden(*args, **kwargs): raise AssertionError('unbound resources executed a child')
    monkeypatch.setattr(execution.subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='resources unavailable or changed|unbound pinned interpreter'):
        execution.validate_launch(profile, expected, argument)


@pytest.mark.parametrize('mutation', ['missing-context','foreign-root','interpreter-binding','foreign-interpreter'])
def test_managed_issuer_mismatch_refuses_before_pinned_delegation(monkeypatch, mutation):
    from al.installer import runtime_run
    expected = interpreter_identity()
    root = Path(runtime_run.__file__).resolve().parents[1]
    request = {'mode':'snapshot','root':str(root),'expected':{'controlled':'binding'},
               'interpreter':expected['executable']}
    if mutation == 'foreign-root': request['root'] = '/foreign/issuer'
    elif mutation == 'interpreter-binding': request['interpreter'] = '/foreign/python'
    elif mutation == 'foreign-interpreter': expected['version'] = [0,0,0]
    if mutation == 'missing-context': monkeypatch.delitem(sys.modules,'_al_validator_context',raising=False)
    else: monkeypatch.setitem(sys.modules,'_al_validator_context',SimpleNamespace(request=request))
    # The snapshot verifier remains a separately tested boundary; this double supplies
    # authenticated metadata to exercise issuer selection and the real launcher guard.
    verified = []
    def snapshot(path, binding):
        verified.append((path,binding))
        return {'origin':{'interpreter':expected}}
    monkeypatch.setattr(runtime_run,'verify_snapshot',snapshot)
    def forbidden(*args): raise AssertionError('foreign issuer delegated a command')
    monkeypatch.setattr(runtime_run.os,'execv',forbidden)
    with pytest.raises(ValueError, match='issuer|interpreter identity changed'):
        runtime_run._managed_issuer()
    assert bool(verified) is (mutation not in {'missing-context','foreign-root'})
