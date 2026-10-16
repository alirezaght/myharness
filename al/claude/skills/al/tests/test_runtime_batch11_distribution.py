"""Reviewed subset adoption adds provenance without dropping established ownership."""
import json
from pathlib import Path
import pytest
import al_distribution as D
import al_distribution_registry as R
from test_distribution_registry import setup


def adoption(tmp,source,target,targets,files):
    names=['owned.py','obsolete.py']
    baseline=tmp/'baseline.json';baseline.write_text(json.dumps({n:D.sha(target/n) for n in names}))
    inventory=tmp/'inventory.json';inventory.write_text(json.dumps([{'target':str(target),'platform':'codex','hashes':{n:D.sha(target/n) for n in names}}]))
    row={'target':str(target),'platform':'codex','identity':R.stable(targets[0]['identity']),
         'source_manifest':{'path':str(baseline),'sha256':D.sha(baseline)},
         'inventory':{'path':str(inventory),'sha256':D.sha(inventory)},'reviewed':True,
         'reason':'Reviewed subset adoption with explicit provenance',
         'files':{n:{'sha256':D.sha(target/n),'mode':(target/n).stat().st_mode&0o777,'ownership_source':'reviewed installation'} for n in files}}
    return {'schema_version':1,'targets':[row]}


def installed(tmp):
    source,target,registry,targets,reviews=setup(tmp)
    (source/'obsolete.py').write_text('obsolete source');(source/'obsolete.py').chmod(0o600)
    first=D.plan(source,targets,reviews,registry=registry);D.apply(first,tmp/'first')
    return source,target,registry,targets,reviews


def test_subset_adoption_deletes_unmentioned_owned_file_and_preserves_backup_and_unmanaged(tmp_path):
    source,target,registry,targets,reviews=installed(tmp_path)
    prior=adoption(tmp_path,source,target,targets,['owned.py']);(source/'obsolete.py').unlink()
    (target/'notes.txt').write_text('unrelated')
    manifest=D.plan(source,targets,reviews,registry=registry,prior_managed=prior)
    t=manifest['targets'][0];assert set(t['prior_owned'])=={'owned.py','obsolete.py'}
    assert t['bootstrap']['files']==prior['targets'][0]['files'] and t['registry']['generation']==1 and t['registry']['sha256']
    actions=D.apply(manifest,tmp_path/'second');deleted=next(a for a in actions if a['action']=='delete')
    assert not (target/'obsolete.py').exists() and (target/'notes.txt').read_text()=='unrelated'
    assert Path(deleted['backup']).read_text()=='obsolete source' and Path(deleted['backup']).stat().st_mode&0o777==0o600
    assert D.verify(manifest)['verified']


def test_subset_adoption_cannot_hide_unmentioned_owned_drift(tmp_path):
    source,target,registry,targets,reviews=installed(tmp_path);prior=adoption(tmp_path,source,target,targets,['owned.py'])
    (source/'obsolete.py').unlink();(target/'obsolete.py').write_text('local drift')
    record=R.record_path(registry,str(target),'codex');before=record.read_bytes()
    with pytest.raises(ValueError,match='obsolete managed resource drift'):D.plan(source,targets,reviews,registry=registry,prior_managed=prior)
    assert record.read_bytes()==before and (target/'obsolete.py').read_text()=='local drift'


def test_overlapping_changed_adoption_requires_rationale_and_revalidates_provenance(tmp_path):
    source,target,registry,targets,reviews=installed(tmp_path);(target/'owned.py').write_text('reviewed local change')
    prior=adoption(tmp_path,source,target,targets,['owned.py']);(source/'obsolete.py').unlink()
    with pytest.raises(ValueError,match='per-file rationale'):D.plan(source,targets,reviews,registry=registry,prior_managed=prior)
    prior['targets'][0]['files']['owned.py']['local_difference_reason']='User reviewed this changed registered preimage'
    manifest=D.plan(source,targets,reviews,registry=registry,prior_managed=prior)
    provenance=Path(prior['targets'][0]['inventory']['path']);provenance.write_text('changed')
    with pytest.raises(ValueError,match='provenance artifact changed'):D.apply(manifest,tmp_path/'refused')
    assert (target/'obsolete.py').exists() and (target/'owned.py').read_text()=='reviewed local change'


def test_failed_subset_transaction_retains_all_prior_ownership_pending(tmp_path,monkeypatch):
    source,target,registry,targets,reviews=installed(tmp_path);prior=adoption(tmp_path,source,target,targets,['owned.py'])
    (source/'obsolete.py').unlink();(source/'owned.py').write_text('replacement');manifest=D.plan(source,targets,reviews,registry=registry,prior_managed=prior)
    original=D.atomic
    def fail(path,raw,mode):
        if path==target/'owned.py':raise OSError('interrupt before installation')
        return original(path,raw,mode)
    monkeypatch.setattr(D,'atomic',fail)
    with pytest.raises(OSError):D.apply(manifest,tmp_path/'failed')
    data=json.loads(R.record_path(registry,str(target),'codex').read_text())
    assert set(data['files'])=={'owned.py','obsolete.py'} and data['pending_transaction']
    journal=json.loads((tmp_path/'failed/distribution-journal.json').read_text());assert journal['status']=='FAILED'
    with pytest.raises(ValueError,match='unresolved transaction'):D.plan(source,targets,reviews,registry=registry,prior_managed=prior)


def test_apply_independently_rejects_forged_reduced_prior_ownership(tmp_path):
    source,target,registry,targets,reviews=installed(tmp_path)
    prior=adoption(tmp_path,source,target,targets,['owned.py']);(source/'obsolete.py').unlink()
    manifest=D.plan(source,targets,reviews,registry=registry,prior_managed=prior);t=manifest['targets'][0]
    t['unmanaged']['obsolete.py']=t['prior_owned'].pop('obsolete.py')
    t['actions']=[a for a in t['actions'] if a['path']!='obsolete.py']
    record=R.record_path(registry,str(target),'codex');before=record.read_bytes()
    with pytest.raises(ValueError,match='prior ownership'):D.apply(manifest,tmp_path/'forged')
    assert record.read_bytes()==before and (target/'obsolete.py').read_text()=='obsolete source'
