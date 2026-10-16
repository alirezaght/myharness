"""Current public planning and owned cancellation fixtures."""
import json
import os
from pathlib import Path
import signal
import sys
import time
import al_artifacts as A
import al_gate_discovery as D
import al_task as T
from hook_regression_helpers import invoke
from batch13_helpers import start


def state(cli):return json.loads(cli('show')[1])


def plan(cli,tmp,data):
    p=tmp/'manifest.json';p.write_text(json.dumps(data));invoke(cli,'gates','plan','--file',str(p))


def context(st,**changes):
    data={'schema_version':1,'profile':'merge_request_quality','session_id':st['session_id'],'run_id':st['run_id'],'task_hash':T.task_hash(st),'selection':{'label':'Explicit data-only MR selection'}}
    data.update(changes)
    return {'profile':data['profile'],'source':A.snapshot_bytes(st['session_id'],json.dumps(data,sort_keys=True).encode(),'ci-context','owned profile selection')}


def resolved(st,paths,ctx=None):
    m=D.discover(st,paths,ctx)
    for g in m['gates']:
        if g['id']=='imports-unresolved':continue
        if g['category']=='lint-imports' and g['applicability']!='NOT_APPLICABLE' or g['id'].startswith('ci-'):
            g.update(applicability='APPLICABLE',argv=[sys.executable,'-c','print("owned sourced verification")'],required=True)
        else:g.update(applicability='NOT_APPLICABLE',required=False,reason='No such obligation exists in the owned fixture',source=m['rules'][0] if m['rules'] else None)
    return m


def amendment(cli,tmp,id='A1'):
    p=tmp/'amendment.json';p.write_text(json.dumps({'id':id,'text':'Amend the accepted specification while preserving all verification gates.','source':'owned user instruction'}))
    invoke(cli,'task','amend','--file',str(p))


def prepare_context(cli,repo,tmp):
    (repo/'.gitlab-ci.yml').write_text("verify:\n  script: echo verify\n  rules:\n    - if: '$CI_MERGE_REQUEST_ID'\n")
    start(cli,repo);invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    st=state(cli);ctx=context(st);m=resolved(st,['svc/app.py'],ctx);plan(cli,tmp,m)
    return ctx,m


def selector(repo):
    source=Path(__file__).resolve().parents[4]/'ci/scripts/import_review'
    target=repo/'ci/scripts/import_review';target.mkdir(parents=True,exist_ok=True)
    for p in source.glob('*.py'):(target/p.name).write_bytes(p.read_bytes())


def consumer_setup(cli,repo,tmp,additional_paths=()):
    selector(repo)
    for name,text in [('packages/shared/src/common.py','def value(): return 1\n'),('services/a/src/app.py','import common\n')]:
        p=repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    start(cli,repo);invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
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
