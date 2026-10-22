"""Public CLI setup for selected Alembic changes and complete rollout reports."""
import json
import sys
import al_store
from batch8_helpers import setup,plan
from hook_regression_helpers import invoke,report_data,save_report,manifest_for
from test_runtime_batch4 import compare_start
from launcher_context import admit_planning

PATH='services/bid-service/alembic/versions/002_bid_duration.py'
DIMENSIONS=('expand_contract','deployment_order','aggregator_environment','consumer_deployment','dlq_redrive')


def rollout_report():
    return {'applicable':True,'reason':'Selected Alembic revision needs compatibility and deployment review',
            **{name:{'status':'NOT_APPLICABLE','reason':'This disposable migration has no such deployment dependency'} for name in DIMENSIONS}}


def review_candidate(cli,repo,tmp,deleted=False):
    config='[project]\nname="bid-service"\ndependencies=[]\n[tool.setuptools.packages.find]\nwhere=["src","alembic"]\n[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n'
    m=setup(cli,repo,tmp,{'services/bid-service/pyproject.toml':config,'services/bid-service/src/app.py':'VALUE = 1\n',PATH:'revision = 1\n'},[PATH])
    for g in m['gates']:
        if g['category']!='lint-imports':g.update(applicability='NOT_APPLICABLE',required=False,argv=[],source=m['rules'][0],reason='No such obligation in this disposable repository')
    plan(cli,tmp,m);invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','engineer','--role','al-engineer','--files',PATH)
    if deleted:(repo/PATH).unlink()
    else:(repo/PATH).write_text('revision = 2\n')
    invoke(cli,'writer','finish','--id','engineer','--outcome','success')
    if deleted:
        st=json.loads(cli('show')[1]);completed=st['completed_phases'][:]
        path=al_store.state_path(st['session_id']);before=path.read_bytes()
        result=invoke(cli,'phase','VERIFICATION',ok=False)
        assert 'service coverage requirements changed' in str(result)
        assert path.read_bytes()==before
        replacement=manifest_for(st,repo,anticipated_paths=(PATH,))
        plan(cli,tmp,replacement);invoke(cli,'gates','run','--stage','base')
        assert json.loads(cli('show')[1])['completed_phases']==completed
    invoke(cli,'phase','VERIFICATION');invoke(cli,'gates','run','--stage','candidate')
    invoke(cli,'evidence','plan','--area','unit','--tier','broad','--kind','check','--scope',PATH,'--',sys.executable,'-c','print("verified")')
    invoke(cli,'evidence','run','--area','unit');invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    file=save_report(cli,tmp,'fresh','alembic-fresh');invoke(cli,'review-fresh','--file',str(file));compare_start(cli,tmp)
    report=report_data(cli,'compare','alembic-compare');report['assessments']['rollout']=rollout_report()
    return report
