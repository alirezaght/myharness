"""Issuer history is complete immutable data, never executable validation code."""
import hashlib
import json
import shutil
import sys
from pathlib import Path
import pytest
from al.installer.issuer_provenance import capture_issuer, retain_issuer, verify_issuer
from al.installer.validator_origin import canonical


def fixture(tmp_path,platform='codex'):
    root=tmp_path/'issuer-home'/('.'+platform)/'skills/al'
    files={'scripts/al_state.py':'raise RuntimeError("must never execute issuer")\n',
           'scripts/al_store.py':'VALUE=1\n','reference/rules.md':'reviewed runtime resource\n','SKILL.md':'issuer skill\n'}
    for name,text in files.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    repository=tmp_path/'repository';repository.mkdir()
    observed={name:{'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest(),'mode':0o644} for name in files}
    data={'schema_version':1,'kind':'al-issuer-invocation-provenance',
          'known_supervisor_invocation':{'argv':[sys.executable,str(root/'scripts/al_state.py'),'--session','known-session']},
          'run':{'repository':str(repository),'session':'known-session','run':'known-run'},
          'platforms':{platform:{'original_installation':str(root),'files':observed,'roles':{}}}}
    source=tmp_path/'known-invocation.json';source.write_bytes(canonical(data))
    args={'platform':platform,'repository':repository,'session':'known-session','run':'known-run',
          'invocation_provenance':{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}}
    return root,args,source


@pytest.mark.parametrize('platform',['codex','claude'])
def test_complete_data_capture_survives_issuer_removal(tmp_path,platform):
    root,args,_=fixture(tmp_path,platform)
    captured=capture_issuer(root,**args)
    binding=retain_issuer(captured,tmp_path/'retained')
    assert verify_issuer(binding['root'],binding,observe_live=True)['live_observed'] is True
    assert captured['provenance']['resource_inventory']['members']==sorted(['provenance/supervisor-invocation.json',*('skill/'+p for p in ['scripts/al_state.py','scripts/al_store.py','reference/rules.md','SKILL.md'])])
    shutil.rmtree(root.parent.parent)
    assert verify_issuer(binding['root'],binding)['capture_digest']==binding['capture_digest']
    with pytest.raises((ValueError,FileNotFoundError)):verify_issuer(binding['root'],binding,observe_live=True)


@pytest.mark.parametrize('change',['extra','missing','mode','body','symlink','role'])
def test_current_issuer_must_match_reviewed_complete_input(tmp_path,change):
    root,args,_=fixture(tmp_path)
    target=root/'reference/rules.md'
    if change=='extra':(root/'scripts/extra.py').write_text('NEW=1\n')
    elif change=='missing':target.unlink()
    elif change=='mode':target.chmod(0o755)
    elif change=='body':target.write_text('changed\n')
    elif change=='symlink':target.unlink();target.symlink_to(root/'SKILL.md')
    else:
        path=root.parent.parent/'agents/al-engineer.toml';path.parent.mkdir();path.write_text('model="different"\n')
    with pytest.raises(ValueError):capture_issuer(root,**args)


@pytest.mark.parametrize('change',['provenance','receipt','blob','mode','extra','symlink-directory'])
def test_retained_data_requires_protected_exact_membership(tmp_path,change):
    root,args,_=fixture(tmp_path)
    binding=retain_issuer(capture_issuer(root,**args),tmp_path/'retained');stored=Path(binding['root'])
    if change in {'provenance','receipt'}:
        path=stored/(change+'.json');path.write_bytes(path.read_bytes()+b'\n')
    elif change=='blob':next((stored/'blobs').iterdir()).write_bytes(b'corrupt')
    elif change=='mode':(stored/'receipt.json').chmod(0o644)
    elif change=='extra':(stored/'unexpected').write_text('extra')
    else:(stored/'foreign').symlink_to(root,target_is_directory=True)
    with pytest.raises(ValueError):verify_issuer(stored,binding)


@pytest.mark.parametrize('change',['foreign-run','foreign-root','argv','interpreter','source-hash','target-source'])
def test_known_invocation_is_not_selected_from_mutable_state(tmp_path,change):
    root,args,source=fixture(tmp_path)
    data=json.loads(source.read_bytes())
    if change=='foreign-run':args['run']='other'
    elif change=='foreign-root':data['platforms']['codex']['original_installation']=str(tmp_path/'foreign')
    elif change=='argv':data['known_supervisor_invocation']['argv'][1]=str(root/'scripts/al_store.py')
    elif change=='interpreter':data['known_supervisor_invocation']['argv'][0]='/nonexistent/python'
    elif change=='target-source':
        other=args['repository']/'invocation.json';other.write_bytes(source.read_bytes());args['invocation_provenance']['path']=str(other)
    else:args['invocation_provenance']['sha256']='0'*64
    if change in {'foreign-root','argv','interpreter'}:
        source.write_bytes(canonical(data));args['invocation_provenance']['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError):capture_issuer(root,**args)


def test_same_bytes_foreign_capture_binding_and_live_drift_refuse(tmp_path):
    root,args,_=fixture(tmp_path)
    captured=capture_issuer(root,**args);binding=retain_issuer(captured,tmp_path/'retained')
    copied=tmp_path/'copied';shutil.copytree(binding['root'],copied)
    with pytest.raises(ValueError):verify_issuer(copied,binding)
    (root/'scripts/al_store.py').write_text('VALUE=2\n')
    with pytest.raises(ValueError):verify_issuer(binding['root'],binding,observe_live=True)
    assert verify_issuer(binding['root'],binding)['live_observed'] is False
    with pytest.raises(ValueError):retain_issuer(captured,tmp_path/'retained')
