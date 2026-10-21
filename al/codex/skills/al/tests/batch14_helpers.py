"""Current public planning and owned cancellation fixtures."""
import json
import os
from pathlib import Path
import signal
import time
import al_artifacts as A
import al_gate_discovery as D
import al_task as T
from hook_regression_helpers import invoke
from test_service_coverage import seed, start as start_coverage
from batch13_helpers import start as start_public


def state(cli):return json.loads(cli('show')[1])


def plan(cli,tmp,data):
    p=tmp/'manifest.json';p.write_text(json.dumps(data));invoke(cli,'gates','plan','--file',str(p))


def context(st,**changes):
    data={'schema_version':1,'profile':'merge_request_quality','session_id':st['session_id'],'run_id':st['run_id'],'task_hash':T.task_hash(st),'selection':{'label':'Explicit data-only MR selection'}}
    data.update(changes)
    return {'profile':data['profile'],'source':A.snapshot_bytes(st['session_id'],json.dumps(data,sort_keys=True).encode(),'ci-context','owned profile selection')}


def resolved(st,paths,ctx=None):
    manifest=D.discover(st,paths,ctx)
    root=Path.cwd()
    contracts=any('contract' in name and ('gen' in name or 'build' in name)
                  for package in [root/'package.json',*(root/unit/'package.json' for unit in manifest['units'])]
                  if package.is_file() for name in json.loads(package.read_text()).get('scripts',{}))
    absent={
        'loc':not (root/'scripts/check-loc.py').is_file(),
        'pre-commit':not any((root/path).is_file() for path in ('.husky/pre-commit','.githooks/pre-commit')),
        'contracts':not contracts,
        'ci':not (root/'.gitlab-ci.yml').is_file(),
    }
    source=manifest['rules'][0] if manifest['rules'] else None
    for gate in manifest['gates']:
        category=gate['category']
        if gate['applicability']=='UNRESOLVED' and category in absent and absent[category]:
            gate.update(applicability='NOT_APPLICABLE',required=False,
                        reason='Independently inspected fixture has no '+category+' obligation.',source=source)
    return manifest


def amendment(cli,tmp,id='A1'):
    p=tmp/'amendment.json';p.write_text(json.dumps({'id':id,'text':'Amend the accepted specification while preserving all verification gates.','source':'owned user instruction'}))
    invoke(cli,'task','amend','--file',str(p))


def prepare_context(cli,repo,tmp):
    (repo/'.gitlab-ci.yml').write_text("verify:\n  script: python3 -m compileall -q svc\n  rules:\n    - if: '$CI_MERGE_REQUEST_ID'\n")
    start_public(cli,repo);invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    st=state(cli);ctx=context(st);m=resolved(st,['svc/app.py'],ctx);plan(cli,tmp,m)
    return ctx,m


def consumer_setup(cli,repo,tmp,additional_paths=()):
    seed(repo)
    start_coverage(cli)
    m=resolved(state(cli),['packages/shared/src/common.py',*additional_paths]);plan(cli,tmp,m);invoke(cli,'gates','run','--stage','base');invoke(cli,'phase','IMPLEMENTATION')
    return m


def wait_for(predicate,timeout=6):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate():return
        time.sleep(.02)
    raise AssertionError('owned process condition timed out')


def alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False


def clean_process(proc,pids=()):
    if proc.poll() is None:proc.kill();proc.communicate(timeout=4)
    for pid in pids:
        try:os.killpg(pid,signal.SIGKILL)
        except ProcessLookupError:pass
