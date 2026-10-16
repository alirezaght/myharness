"""Ownership provenance and recoverable installation boundary regressions."""
import hashlib
import json
import os
import sys
from pathlib import Path
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import al_distribution as D
import al_distribution_registry as R


def setup(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'owned.py').write_text('source')
    target=tmp_path/'target';target.mkdir();registry=tmp_path/'registry'
    identity=D.repo_identity(target);rules=D.instructions(target,identity)
    targets=[{'path':str(target),'platform':'codex','identity':identity,'instructions':rules}]
    reviews={str(target):{'reviewed':True,'instructions':rules}}
    return source,target,registry,targets,reviews


def test_mode_drift_and_changed_registry_generation_refuse_without_target_writes(tmp_path):
    source,target,registry,targets,reviews=setup(tmp_path)
    (target/'owned.py').write_text('local');(target/'owned.py').chmod(0o600)
    manifest=D.plan(source,targets,reviews,registry=registry)
    (target/'owned.py').chmod(0o644)
    with pytest.raises(ValueError,match='target drift'):D.apply(manifest,tmp_path/'refused')
    assert (target/'owned.py').read_text()=='local'
    (target/'owned.py').chmod(0o600)
    D.apply(manifest,tmp_path/'first')
    stale=D.plan(source,targets,reviews,registry=registry)
    record=R.record_path(registry,str(target),'codex');data=json.loads(record.read_text());data['generation']+=1;record.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='generation/hash'):D.apply(stale,tmp_path/'generation-refused')
    assert (target/'owned.py').read_text()=='source'


def test_obsolete_modified_owned_resource_is_a_conflict_and_unmanaged_extra_survives(tmp_path):
    source,target,registry,targets,reviews=setup(tmp_path)
    D.apply(D.plan(source,targets,reviews,registry=registry),tmp_path/'first')
    (source/'owned.py').unlink();(source/'new.py').write_text('new')
    (target/'custom.py').write_text('unmanaged');(target/'owned.py').write_text('user modified')
    with pytest.raises(ValueError,match='obsolete managed resource drift'):D.plan(source,targets,reviews,registry=registry)
    assert (target/'owned.py').read_text()=='user modified'
    assert (target/'custom.py').read_text()=='unmanaged'


def test_interruption_after_verified_backup_keeps_pending_transaction_and_preimage(tmp_path,monkeypatch):
    source,target,registry,targets,reviews=setup(tmp_path)
    (target/'owned.py').write_text('prior');(target/'owned.py').chmod(0o600)
    manifest=D.plan(source,targets,reviews,registry=registry);original=D.atomic
    def fail_target(path,raw,mode):
        if path==target/'owned.py':raise OSError('simulated interruption')
        return original(path,raw,mode)
    monkeypatch.setattr(D,'atomic',fail_target)
    with pytest.raises(OSError,match='simulated interruption'):D.apply(manifest,tmp_path/'backup')
    journal=json.loads((tmp_path/'backup/distribution-journal.json').read_text())
    assert journal['status']=='FAILED' and journal['actions'][0]['status']=='BACKED_UP'
    backup=Path(journal['actions'][0]['backup'])
    assert backup.read_text()=='prior' and backup.stat().st_mode & 0o777 == 0o600
    assert (target/'owned.py').read_text()=='prior'
    with pytest.raises(ValueError,match='unresolved transaction'):D.plan(source,targets,reviews,registry=registry)
    with pytest.raises(ValueError,match='unresolved transaction'):D.verify(manifest)


def test_registry_symlink_and_inside_repository_are_refused(tmp_path):
    source,target,registry,targets,reviews=setup(tmp_path)
    actual=tmp_path/'actual';actual.mkdir();registry.symlink_to(actual,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):D.plan(source,targets,reviews,registry=registry)
    with pytest.raises(ValueError,match='outside'):D.plan(source,targets,reviews,registry=target/'registry')


def test_legacy_bootstrap_requires_baseline_and_inventory_provenance(tmp_path):
    source,target,registry,targets,reviews=setup(tmp_path)
    (target/'obsolete.py').write_text('legacy local variant');(target/'obsolete.py').chmod(0o600)
    (target/'notes.py').write_text('unmanaged')
    baseline=tmp_path/'baseline.json';baseline.write_text(json.dumps({'obsolete.py':hashlib.sha256(b'baseline variant').hexdigest()}))
    inventory=tmp_path/'inventory.json';inventory.write_text(json.dumps([{'target':str(target),'platform':'codex','hashes':{'obsolete.py':D.sha(target/'obsolete.py')}}]))
    row={'target':str(target),'platform':'codex','identity':R.stable(targets[0]['identity']),
         'source_manifest':{'path':str(baseline),'sha256':D.sha(baseline)},'inventory':{'path':str(inventory),'sha256':D.sha(inventory)},
         'reviewed':True,'reason':'Supervisor reviewed eligible baseline resource and local modification',
         'files':{'obsolete.py':{'sha256':D.sha(target/'obsolete.py'),'mode':0o600,'ownership_source':'baseline manifest obsolete.py'}}}
    prior={'schema_version':1,'targets':[row]}
    with pytest.raises(ValueError,match='local legacy difference'):D.plan(source,targets,reviews,registry=registry,prior_managed=prior)
    row['files']['obsolete.py']['local_difference_reason']='Authorized local legacy variant with unchanged ownership'
    manifest=D.plan(source,targets,reviews,registry=registry,prior_managed=prior)
    records=D.apply(manifest,tmp_path/'backup')
    assert not (target/'obsolete.py').exists() and (target/'notes.py').read_text()=='unmanaged'
    deleted=next(a for a in records if a['action']=='delete')
    assert Path(deleted['backup']).read_text()=='legacy local variant'
    assert Path(deleted['backup']).stat().st_mode & 0o777 == 0o600
    assert D.verify(manifest)['verified']


def test_forged_prior_ownership_cannot_delete_unmanaged_resource(tmp_path):
    source,target,registry,targets,reviews=setup(tmp_path)
    (target/'custom.py').write_text('unmanaged')
    manifest=D.plan(source,targets,reviews,registry=registry)
    forged={'sha256':D.sha(target/'custom.py'),'mode':(target/'custom.py').stat().st_mode & 0o777}
    manifest['targets'][0]['prior_owned']['custom.py']=forged
    manifest['targets'][0]['actions'].append({'path':'custom.py','action':'delete','target':forged,'source':None})
    with pytest.raises(ValueError,match='prior ownership'):D.apply(manifest,tmp_path/'refused')
    assert (target/'custom.py').read_text()=='unmanaged'


def test_verify_rejects_mode_drift_after_completed_install(tmp_path):
    source,target,registry,targets,reviews=setup(tmp_path)
    manifest=D.plan(source,targets,reviews,registry=registry);D.apply(manifest,tmp_path/'backup')
    (target/'owned.py').chmod(0o600)
    with pytest.raises(ValueError,match='hash/mode'):D.verify(manifest)


def test_roles_share_transaction_backup_and_completion_without_becoming_owned(tmp_path):
    source,target,registry,targets,reviews=setup(tmp_path)
    role_source=tmp_path/'role-source.md';role_source.write_text('new role')
    role_target=tmp_path/'roles/role.md';role_target.parent.mkdir();role_target.write_text('old role');role_target.chmod(0o600)
    from al_distribution_roles import plan_roles
    identity=D.repo_identity(role_target.parent);rules=D.instructions(role_target.parent,identity)
    manifest=D.plan(source,targets,reviews,registry=registry)
    manifest['role_changes']=plan_roles([{'source':str(role_source),'target':str(role_target),'protocol_reason':'required separate review context',
                                        'instruction_review':{'reviewed':True,'instructions':rules}}])
    records=D.apply(manifest,tmp_path/'backup');role=next(x for x in records if x['kind']=='role')
    assert role_target.read_text()=='new role' and Path(role['backup']).read_text()=='old role'
    assert Path(role['backup']).stat().st_mode & 0o777 == 0o600
    record=json.loads(R.record_path(registry,str(target),'codex').read_text())
    assert set(record['files'])=={'owned.py'}
    assert D.verify(manifest)['verified']
