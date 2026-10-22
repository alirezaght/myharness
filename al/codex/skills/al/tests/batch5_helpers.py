"""Public workflow setup for retry and historical-finding regressions."""
import json
import shutil
from pathlib import Path
from hook_regression_helpers import begin, invoke, report_data, save_report, CATEGORIES
from test_hook_batch6 import _manifest
from launcher_context import admit_planning


def checker(repo):
    real=Path(__file__).parent/'fixtures/repository/scripts'
    names=('check-loc.py','check-loc.allowlist')
    for name in names:
        if not (real/name).is_file():raise FileNotFoundError('package LOC fixture missing: '+str(real/name))
    scripts=repo/'scripts';scripts.mkdir(exist_ok=True)
    for name in names:shutil.copy2(real/name,scripts/name)


def state(cli):return json.loads(cli('show')[1])


def writing(cli,tmp):
    invoke(cli,'start','--spec','Reconcile failed writers before retrying their work.')
    invoke(cli,'spec-gate','SPEC_READY')
    invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    file=tmp/'gates.json';file.write_text(json.dumps(_manifest(state(cli))))
    invoke(cli,'gates','plan','--file',str(file));invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')


def comparison(cli,tmp,invocation,resolutions=None):
    plan=tmp/(invocation+'.md');plan.write_text('Explicitly resolve every prior blocker.\n')
    invoke(cli,'review-compare-start','--plan',str(plan));invoke(cli,'review-packet','--stage','compare')
    return save_report(cli,tmp,'compare',invocation,prior_resolutions=resolutions)


def second_round(cli,tmp,repo):
    begin(cli,tmp,repo)
    finding={'id':'F-fresh','source':'AC-01','severity':'BLOCKING','description':'Source still requires a verified fix.'}
    invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    fresh=save_report(cli,tmp,'fresh','fresh-one',findings=[finding]);invoke(cli,'review-fresh','--file',str(fresh))
    unresolved={'id':'F-fresh','source':'AC-01','status':'unresolved','rationale':'Still present.'}
    first=comparison(cli,tmp,'compare-one',[unresolved]);data=json.loads(first.read_text());data['status']='CHANGES_REQUIRED';first.write_text(json.dumps(data))
    invoke(cli,'review-result','--file',str(first));invoke(cli,'correction','--route','al-engineer');invoke(cli,'phase','FINALIZATION')
    invoke(cli,'writer','start','--id','correction','--role','al-engineer','--files','svc/app.py')
    (repo/'svc/app.py').write_text('verified_fix = True\n');invoke(cli,'writer','finish','--id','correction','--outcome','success')
    invoke(cli,'phase','VERIFICATION');invoke(cli,'gates','run','--stage','candidate');invoke(cli,'evidence','run','--area','unit','--fresh')
    invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    fresh=save_report(cli,tmp,'fresh','fresh-two');invoke(cli,'review-fresh','--file',str(fresh))
    return {'id':'F-fresh','source':'AC-01','status':'fixed','rationale':'Verified source correction.',
            'locations':['svc/app.py:1'],'evidence':[state(cli)['evidence']['entries'][-1]['id']]}
