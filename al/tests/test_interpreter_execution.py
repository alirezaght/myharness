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
