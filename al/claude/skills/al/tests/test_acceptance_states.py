"""Diagnostic assessment cannot grant formal approval or provider results."""
import json
from pathlib import Path
import al_acceptance as Acceptance
from evidence_helpers import start_implementation,plan_and_run_broad


def test_code_assessment_is_separate_and_stales_with_source(cli,repo,tmp_path,state_file):
    start_implementation(cli,tmp_path)
    assert plan_and_run_broad(cli)==0
    code,out,err=cli('acceptance','packet');assert code==0,(out,err)
    packet=json.loads(out)
    st=json.loads(state_file().read_bytes());eid=st['evidence']['entries'][-1]['id']
    value={'schema_version':1,'kind':'code-assessment-v1','binding':packet['binding'],'reviewer':'independent code reviewer',
           'criteria':[{'id':i,'status':'SATISFIED','rationale':'The current executable check supports the code conclusion.',
                        'locations':['svc/app.py:1'],'evidence':[eid]} for i in packet['criteria']]}
    file=tmp_path/'assessment.json';file.write_text(json.dumps(value))
    result=cli('acceptance','assess','--file',str(file));assert result[0]==0,result
    view=json.loads(cli('acceptance','status')[1])
    assert view['code_assessment']['status']=='CODE_ACCEPTABLE'
    assert view['code_assessment']['authorizes_approval'] is False
    assert view['business_acceptance']['status']=='NOT_APPLICABLE'
    assert view['overall_status']=='RUNNING'
    before=state_file().read_bytes();assert cli('approved')[0]!=0;assert state_file().read_bytes()==before
    (repo/'svc/app.py').write_text('x = 99\n')
    assert json.loads(cli('acceptance','status')[1])['code_assessment']['status']=='STALE'
    assert cli('acceptance','assess','--file',str(file))[0]!=0


def test_required_business_decision_is_task_bound_and_does_not_approve(cli,repo,tmp_path,state_file):
    task={'original_request':'Require a recorded owner decision before final acceptance.',
          'accepted_spec':'Implement a scoped feature and require the declared owner to accept its current source.',
          'acceptance':{'source':'user','text':'accepted specification'},'constraints':[],'ticket':{'present':False},
          'criteria':['AC-01'],'invariants':[],
          'business_acceptance':{'id':'owner-acceptance','required':True,'source':'product owner','description':'Owner accepts the delivered behavior.'}}
    file=tmp_path/'task.json';file.write_text(json.dumps(task))
    result=cli('start','--package-file',str(file));assert result[0]==0,result
    view=json.loads(cli('acceptance','status')[1]);assert view['business_acceptance']['status']=='NOT_RUN'
    assert view['provider_verification']['status']=='BLOCKED'
    decision={'schema_version':1,'kind':'business-acceptance-v1','binding':view['binding'],'requirement_id':'owner-acceptance',
              'source':'wrong owner','decision':'ACCEPTED','rationale':'Observed the delivered behavior.'}
    file=tmp_path/'decision.json';file.write_text(json.dumps(decision));before=state_file().read_bytes()
    assert cli('acceptance','business','--file',str(file))[0]!=0 and state_file().read_bytes()==before
    decision['source']='product owner';file.write_text(json.dumps(decision))
    assert cli('acceptance','business','--file',str(file))[0]==0
    assert json.loads(cli('acceptance','status')[1])['business_acceptance']['status']=='ACCEPTED'
    (repo/'svc/app.py').write_text('x = 10\n')
    assert json.loads(cli('acceptance','status')[1])['business_acceptance']['status']=='STALE'
    assert Acceptance.completion_gaps(json.loads(state_file().read_bytes()))


def test_provider_failure_outranks_another_blocked_provider():
    assert Acceptance._aggregate([{'status':'BLOCKED'},{'status':'FAIL'}])=='FAIL'
    assert Acceptance._aggregate([{'status':'PASS'},{'status':'BLOCKED'}])=='BLOCKED'
