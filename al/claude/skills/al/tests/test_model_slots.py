"""Claude model-slot policy (no slug/agent_type) across planning and review fallbacks."""
import json
import al_artifacts as A
import al_models as M
from hook_regression_helpers import begin,invoke,report_data


def test_planning_attempt_fallback_binds_the_claude_fallback_slot(cli,repo,tmp_path):
    invoke(cli,'start','--spec','Bind planning attempts to the approved Claude fallback slot.')
    invoke(cli,'spec-gate','SPEC_READY')
    invoke(cli,'model-unavailable',M.POLICY['al-architect']['primary']['model'],'--reason','Provider unavailable')
    invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')  # issues the planning attempt
    state=json.loads(cli('show')[1])
    attempt=A.read_json(state['planning_authorship']['attempt'])
    slot=M.POLICY['al-architect']['fallback']
    assert (attempt['role'],attempt['model'],attempt['effort'])==('al-architect',slot['model'],slot['effort'])
    assert 'slug' not in slot and 'agent_type' not in slot


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


