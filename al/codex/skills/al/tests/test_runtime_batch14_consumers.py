"""Consumer obligations change independently of implementation body fingerprints."""
import json
from pathlib import Path
import pytest
import al_gate_currency as C
import al_ledger as L
from hook_regression_helpers import invoke
from batch14_helpers import consumer_setup,state,resolved,plan
from test_service_coverage import unit


def test_body_only_edit_keeps_rules_current_but_invalidates_candidate_evidence(cli,repo,tmp_path,state_file):
    m=consumer_setup(cli,repo,tmp_path)
    invoke(cli,'gates','run','--stage','candidate');st=state(cli);entry=st['evidence']['entries'][-1]
    (repo/'packages/shared/src/common.py').write_text('def value(): return 2\n')
    assert C.check(state(cli))['current'] is True
    assert L.entry_verdict(state(cli),entry)[0]=='STALE'
    assert 'packages/shared/src/common.py' not in {r['path'] for r in m['rules']}
    before=state_file().read_bytes();p=tmp_path/'current.json';p.write_text(json.dumps(m))
    invoke(cli,'gates','plan','--file',str(p),ok=False);assert state_file().read_bytes()==before
    invoke(cli,'phase','VERIFICATION')


@pytest.mark.parametrize('change',['consumer','unknown','rule'])
def test_changed_obligations_allow_guarded_replacement_without_fake_interruption(cli,repo,tmp_path,state_file,change):
    m=consumer_setup(cli,repo,tmp_path);old=state(cli);completed=old['completed_phases'][:]
    if change=='consumer':
        assert not (repo/'services/new-consumer').exists()
        unit(repo,'services/new-consumer','import common\n')
    elif change=='unknown':
        (repo/'packages/shared/src/common.py').write_text('def load(name):\n    return __import__(name)\n')
        (repo/'services/a/src/app.py').write_text('def load(name):\n    return __import__(name)\n')
    else:(repo/'Makefile').write_text('all:\n\ttrue # changed policy\n')
    if change=='unknown':
        # A content edit keeps currency; the full inventory (planning, review start) sees the change.
        import al_artifacts as A
        import al_gate_inventory as V
        current=state(cli);assert C.check(current)['current'] and V.gaps(current,A.read_json(current['gate_manifests'][-1]))
    else:
        assert C.check(state(cli))['current'] is False
        before=state_file().read_bytes();invoke(cli,'phase','VERIFICATION',ok=False);assert state_file().read_bytes()==before
    m=resolved(state(cli),['packages/shared/src/common.py']);plan(cli,tmp_path,m)
    st=state(cli);assert st['phase']=='IMPLEMENTATION' and st['completed_phases']==completed
    assert st['gate_recovery']['superseded_manifest']==old['gate_manifests'][-1]['sha256']
    if change=='unknown':
        assessment=m['service_coverage']['assessment']
        assert set(assessment['selected_services'])<={'services/a','services/b'} and assessment['dependency_uncertainties']
        invoke(cli,'gates','run','--stage','base');invoke(cli,'phase','VERIFICATION')
        invoke(cli,'gates','run','--stage','candidate',ok=False)
        failed=[e for e in state(cli)['evidence']['entries'] if e['area']=='gate-candidate-imports-services-a']
        assert failed[-1]['status']=='FAIL'
        assert 'nonliteral dynamic import' in Path(failed[-1]['log']).read_text()
        invoke(cli,'review-start',ok=False);invoke(cli,'approved',ok=False)
    elif change=='consumer':
        invoke(cli,'gates','run','--stage','base',ok=False)
        failed=[e for e in state(cli)['evidence']['entries'] if e['area']=='gate-base-imports-services-new-consumer']
        assert failed[-1]['status']=='FAIL'
        invoke(cli,'gates','decision','--id','imports-services-new-consumer','--action','fix',
               '--source','User explicitly approved the in-scope new consumer and its absent baseline check',
               '--reason','The new service is absent in the pinned base; retain failed base proof and verify the real candidate check.')
        invoke(cli,'phase','VERIFICATION');invoke(cli,'gates','run','--stage','candidate')
        assert not state(cli).get('gate_recovery')
    else:
        invoke(cli,'gates','run','--stage','base');invoke(cli,'phase','VERIFICATION')
        assert not state(cli).get('gate_recovery')


@pytest.mark.parametrize('phase',['IMPLEMENTATION','FINALIZATION'])
@pytest.mark.parametrize('operation',['writer','check'])
def test_active_operations_block_ordinary_replacement_atomically(cli,repo,tmp_path,state_file,operation,phase):
    consumer_setup(cli,repo,tmp_path,['services/a/src/app.py'] if operation=='writer' else ())
    if phase=='FINALIZATION':invoke(cli,'phase','FINALIZATION')
    st=state(cli)
    if operation=='writer':invoke(cli,'writer','start','--id','live','--role','al-engineer','--files','services/a/src/app.py')
    else:
        import al_evidence as E
        import sys
        E._create(st['session_id'],st['run_id'],area='pending',kind='check',tier='focused',argv=[sys.executable,'-c','print(1)'],cwd='.',scope=['docs'],env_names=[])
    (repo/'Makefile').write_text('changed policy\n')
    m=resolved(state(cli),['packages/shared/src/common.py']);p=tmp_path/'replacement.json';p.write_text(json.dumps(m))
    before=state_file().read_bytes();invoke(cli,'gates','plan','--file',str(p),ok=False);assert state_file().read_bytes()==before


def test_manifest_cannot_drop_scanned_consumer_inputs_from_gate_scope(cli,repo,tmp_path,state_file):
    consumer_setup(cli,repo,tmp_path);m=resolved(state(cli),['packages/shared/src/common.py'])
    invoke(cli,'phase','VERIFICATION')
    next(g for g in m['gates'] if g['category']=='lint-imports')['scope']=['services/a']
    p=tmp_path/'missing-inputs.json';p.write_text(json.dumps(m));before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(p),ok=False);assert state_file().read_bytes()==before


@pytest.mark.parametrize('corrupt',[{},None,False,[], '', ['corrupt'], 'corrupt'])
def test_present_corrupt_recovery_marker_is_never_replaced(cli,repo,tmp_path,state_file,corrupt):
    consumer_setup(cli,repo,tmp_path)
    (repo/'Makefile').write_text('changed policy\n')
    m=resolved(state(cli),['packages/shared/src/common.py']);p=tmp_path/'replacement.json';p.write_text(json.dumps(m))
    st=state(cli);st['gate_recovery']=corrupt;state_file().write_text(json.dumps(st))
    before=state_file().read_bytes()
    result=invoke(cli,'gates','plan','--file',str(p),ok=False)
    assert 'gate recovery' in str(result).lower()
    assert state_file().read_bytes()==before
