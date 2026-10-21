"""Generic disposable environment-action CI fixtures, with real check execution."""
import json
from pathlib import Path
from batch8_helpers import setup,plan
from hook_regression_helpers import invoke


def job(action='prepare',script='python3 -m compileall -q svc',extra=''):
    return 'validate:\n  stage: test\n  environment: {name: staging, action: '+action+'}\n'+extra+'  script: '+script+'\n'


def planned(cli,repo,tmp,files):
    manifest=setup(cli,repo,tmp,files,['svc/app.py'])
    for gate in manifest['gates']:
        if gate['category'] not in {'ci','lint-imports'} and not gate.get('obligation_ids'):
            gate.update(applicability='NOT_APPLICABLE',required=False,source=manifest['rules'][0],reason='No matching declaration in this disposable fixture')
    plan(cli,tmp,manifest);return manifest


def state(cli):return json.loads(cli('show')[1])


def run(cli,stage):return cli('gates','run','--stage',stage)


def entries(cli,gate):
    return [entry for entry in state(cli)['evidence']['entries'] if entry.get('gate_id')==gate or entry.get('area','').endswith('-'+gate)]


def required_ci(manifest):return next(g for g in manifest['gates'] if g['category']=='ci' and g.get('source',{}).get('job')=='validate')
