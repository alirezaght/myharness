"""Declared environment and immutable stage-attempt boundary regressions."""
import json
import sys
import pytest
import al_artifacts as A
import al_models as M
from hook_regression_helpers import begin,invoke,report_data
from test_hook_batch6 import _manifest,_source


def plan(cli,repo,tmp_path,names):
    state=json.loads(cli('show')[1])
    m=_manifest(state,source=_source(repo,'svc/app.py'),active='loc',argv=[sys.executable,'-c','print("checked")'])
    next(g for g in m['gates'] if g['id']=='loc')['env_names']=names
    path=tmp_path/'manifest.json';path.write_text(json.dumps(m))
    return path


@pytest.mark.parametrize('names',['VAR',['VAR','VAR'],[''],['bad-name'],[1],[[]]])
def test_invalid_environment_declarations_preserve_state(cli,repo,tmp_path,state_file,names):
    begin(cli,tmp_path,repo);path=plan(cli,repo,tmp_path,names)
    before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(path),ok=False)
    assert state_file().read_bytes()==before


def test_env_alias_is_rejected(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);path=plan(cli,repo,tmp_path,[])
    m=json.loads(path.read_text());next(g for g in m['gates'] if g['id']=='loc')['env']=['VAR'];path.write_text(json.dumps(m))
    before=state_file().read_bytes();invoke(cli,'gates','plan','--file',str(path),ok=False)
    assert state_file().read_bytes()==before


def test_unset_empty_and_changed_environment_invalidate_both_stages(cli,repo,tmp_path,monkeypatch):
    begin(cli,tmp_path,repo);monkeypatch.delenv('AL_VARIANT',raising=False)
    invoke(cli,'gates','plan','--file',str(plan(cli,repo,tmp_path,['AL_VARIANT'])))
    for stage in ('base','candidate'):invoke(cli,'gates','run','--stage',stage)
    status=json.loads(cli('gates','status')[1])
    assert {k:v for k,v in status.items() if k!='evidence'}=={'base':[],'candidate':[],'recovery':[],'baseline_exceptions':[],'unverified':[]}
    assert status['evidence'] and all(row['status']=='PASS' for row in status['evidence'])
    monkeypatch.setenv('AL_VARIANT','')
    assert all(json.loads(cli('gates','status')[1])[k] for k in ('base','candidate','recovery'))
    for stage in ('base','candidate'):invoke(cli,'gates','run','--stage',stage)
    monkeypatch.setenv('AL_VARIANT','private-test-environment-value')
    assert all(json.loads(cli('gates','status')[1])[k] for k in ('base','candidate','recovery'))
    assert 'private-test-environment-value' not in cli('show')[1]


def test_environment_change_cannot_authorize_failed_baseline(cli,repo,tmp_path,monkeypatch,state_file):
    begin(cli,tmp_path,repo);monkeypatch.setenv('AL_VARIANT','before')
    path=plan(cli,repo,tmp_path,['AL_VARIANT']);m=json.loads(path.read_text())
    next(g for g in m['gates'] if g['id']=='loc')['argv']=[sys.executable,'-c','raise SystemExit(1)'];path.write_text(json.dumps(m))
    invoke(cli,'gates','plan','--file',str(path))
    invoke(cli,'gates','run','--stage','base','--id','imports-root')
    invoke(cli,'gates','run','--stage','base','--id','loc',ok=False)
    invoke(cli,'gates','decision','--id','loc','--action','fix','--source','user','--reason','Fix this baseline')
    assert not json.loads(cli('gates','status')[1])['base']
    monkeypatch.setenv('AL_VARIANT','after')
    assert json.loads(cli('gates','status')[1])['base']
    before=state_file().read_bytes()
    invoke(cli,'gates','decision','--id','loc','--action','fix','--source','user','--reason','Old failure',ok=False)
    assert state_file().read_bytes()==before


def test_fallback_before_packet_binds_current_approved_policy(cli,repo,tmp_path):
    begin(cli,tmp_path,repo);invoke(cli,'review-start')
    primary=M.POLICY['al-architect']['primary']['model']
    invoke(cli,'model-unavailable',primary,'--reason','Provider unavailable')
    _,out,_=invoke(cli,'review-packet','--stage','fresh');packet=json.loads(out)['content']
    binding=packet['attempt'];slot=M.POLICY['al-architect']['fallback']
    assert (binding['role'],binding['model'],binding['effort'])==('al-architect',slot['model'],slot['effort'])
    assert binding['attempt_id'] and binding['policy_digest'] and binding['fallback_event_reference']
    assert A.read_json(binding['fallback_event_reference'])['reason']=='Provider unavailable'
    data=report_data(cli,'fresh','fresh-fallback');data['attempt_id']=binding['attempt_id']
    path=tmp_path/'fresh.json';path.write_text(json.dumps(data));invoke(cli,'review-fresh','--file',str(path))


def test_packet_replacement_archives_abandoned_attempt_and_cannot_replace_saved(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    first=json.loads(cli('show')[1])['review'];old=report_data(cli,'fresh','abandoned')
    primary=M.POLICY['al-architect']['primary']['model']
    invoke(cli,'model-unavailable',primary,'--reason','Provider became unavailable')
    path=tmp_path/'fresh.json';path.write_text(json.dumps(old));before=state_file().read_bytes()
    invoke(cli,'review-fresh','--file',str(path),ok=False);assert state_file().read_bytes()==before
    invoke(cli,'review-packet','--stage','fresh','--replace-unavailable-attempt')
    review=json.loads(cli('show')[1])['review'];assert review['abandoned_attempts'][0]['attempt']==first['attempts']['fresh']
    before=state_file().read_bytes();invoke(cli,'review-fresh','--file',str(path),ok=False)
    assert state_file().read_bytes()==before
    data=report_data(cli,'fresh','replacement');data['attempt_id']=A.read_json(first['attempts']['fresh'])['attempt_id']
    path.write_text(json.dumps(data));invoke(cli,'review-fresh','--file',str(path),ok=False)
    assert state_file().read_bytes()==before
    data.pop('attempt_id');path.write_text(json.dumps(data));invoke(cli,'review-fresh','--file',str(path))
    before=state_file().read_bytes();invoke(cli,'review-packet','--stage','fresh','--replace-unavailable-attempt',ok=False)
    assert state_file().read_bytes()==before


@pytest.mark.parametrize('change',[{'model':'unsupported'},{'effort':'low'},{'policy_digest':'0'*64},{'attempt_id':'abandoned'}])
def test_report_cannot_forge_attempt_provenance(cli,repo,tmp_path,state_file,change):
    begin(cli,tmp_path,repo);invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    data=report_data(cli,'fresh','forged');data.update(change);path=tmp_path/'report.json';path.write_text(json.dumps(data))
    before=state_file().read_bytes();invoke(cli,'review-fresh','--file',str(path),ok=False)
    assert state_file().read_bytes()==before


def test_replacement_without_unavailability_refuses_without_state_write(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    before=state_file().read_bytes()
    invoke(cli,'review-packet','--stage','fresh','--replace-unavailable-attempt',ok=False)
    assert state_file().read_bytes()==before
