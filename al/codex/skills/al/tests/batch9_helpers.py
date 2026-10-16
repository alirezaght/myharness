"""Public CLI setup for selected Alembic changes and complete rollout reports."""
import json
import sys
from batch8_helpers import setup,plan
from hook_regression_helpers import invoke,report_data,save_report
from test_runtime_batch4 import compare_start

PATH='services/bid-service/alembic/versions/002_bid_duration.py'
DIMENSIONS=('expand_contract','deployment_order','aggregator_environment','consumer_deployment','dlq_redrive')


def rollout_report():
    return {'applicable':True,'reason':'Selected Alembic revision needs compatibility and deployment review',
            **{name:{'status':'NOT_APPLICABLE','reason':'This disposable migration has no such deployment dependency'} for name in DIMENSIONS}}


def review_candidate(cli,repo,tmp,deleted=False):
    m=setup(cli,repo,tmp,{'Makefile':'lint-imports:\n\tpython3 -c "print(\'services/$(SVC)-service\')"\n',PATH:'revision = 1\n'},[PATH])
    for g in m['gates']:
        if g['category']!='lint-imports':g.update(applicability='NOT_APPLICABLE',required=False,argv=[],source=m['rules'][0],reason='No such obligation in this disposable repository')
    plan(cli,tmp,m);invoke(cli,'gates','run','--stage','base');invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','engineer','--role','al-engineer','--files',PATH)
    if deleted:(repo/PATH).unlink()
    else:(repo/PATH).write_text('revision = 2\n')
    invoke(cli,'writer','finish','--id','engineer','--outcome','success');invoke(cli,'phase','VERIFICATION');invoke(cli,'gates','run','--stage','candidate')
    invoke(cli,'evidence','plan','--area','unit','--tier','broad','--kind','check','--scope',PATH,'--',sys.executable,'-c','print("verified")')
    invoke(cli,'evidence','run','--area','unit');invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    file=save_report(cli,tmp,'fresh','alembic-fresh');invoke(cli,'review-fresh','--file',str(file));compare_start(cli,tmp)
    report=report_data(cli,'compare','alembic-compare');report['assessments']['rollout']=rollout_report()
    return report
