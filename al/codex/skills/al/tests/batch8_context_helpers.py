"""Create an explicit data-only context through the existing artifact API."""
import json
import al_artifacts as A
import al_task as T
import al_gate_discovery as D
from batch8_helpers import setup,executable_ci,plan
from hook_regression_helpers import invoke,finish_review


def declaration(st,**changes):
    data={'schema_version':1,'profile':'merge_request_quality','session_id':st['session_id'],
          'run_id':st['run_id'],'task_hash':T.task_hash(st),'selection':{'label':'Explicit MR quality planning scenario'}}
    data.update(changes)
    return {'profile':data['profile'],'source':A.snapshot_bytes(st['session_id'],json.dumps(data,sort_keys=True).encode(),'ci-context','explicit planning scenario selection')}


def prepared(cli,repo,tmp,files,paths,**changes):
    setup(cli,repo,tmp,files,paths);st=json.loads(cli('show')[1]);context=declaration(st,**changes)
    return st,context,D.discover(st,paths,context)


def approval(cli,tmp,m):
    import sys
    for g in m['gates']:
        if g['category'] not in {'ci','lint-imports'} and not g.get('obligation_ids'):
            g.update(applicability='NOT_APPLICABLE',required=False,source=m['rules'][0],reason='No such obligation in the disposable repository')
    plan(cli,tmp,executable_ci(m));invoke(cli,'gates','run','--stage','base')
    invoke(cli,'phase','IMPLEMENTATION');invoke(cli,'writer','start','--id','engineer','--role','al-engineer','--files','svc/app.py')
    invoke(cli,'writer','finish','--id','engineer','--outcome','success');invoke(cli,'phase','VERIFICATION');invoke(cli,'gates','run','--stage','candidate')
    invoke(cli,'evidence','plan','--area','unit','--tier','broad','--kind','check','--scope','svc/app.py','--',sys.executable,'-c','print("verified")')
    invoke(cli,'evidence','run','--area','unit');finish_review(cli,tmp);invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')
