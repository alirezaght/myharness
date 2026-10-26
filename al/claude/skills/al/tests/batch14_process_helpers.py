"""Owned child/grandchild gates with no remote service or real hook execution."""
import json
import os
from pathlib import Path
import sys
import al_ledger as L
from batch14_helpers import state,plan
from batch13_helpers import start
from hook_regression_helpers import manifest_for

PROGRAM='''import json,os,subprocess,sys,time
from pathlib import Path
if os.environ.get('AL_B14_MODE')=='wait':
 child=subprocess.Popen([sys.executable,'-c',"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); target=Path(os.environ['AL_B14_GRAND']); staged=target.with_suffix('.pending'); staged.write_text(str(os.getpid())); staged.replace(target); time.sleep(60)"])
 while not Path(os.environ['AL_B14_GRAND']).exists():time.sleep(.01)
 target=Path(os.environ['AL_B14_CHILD']); staged=target.with_suffix('.pending'); staged.write_text(str(os.getpid())); staged.replace(target)
 print('owned gate running',flush=True)
 time.sleep(60)
else:
 print(json.dumps({'id':9,'name':'verify','pipeline':{'id':11,'project_id':42},'commit':{'id':os.environ['AL_B14_REV']},'status':'success'}))
'''


def prepare(cli,repo,tmp,monkeypatch,remote,mode='wait'):
    (repo/'scripts').mkdir(exist_ok=True);script=repo/'scripts/owned_gate.py';script.write_text(PROGRAM)
    if remote:(repo/'.gitlab-ci.yml').write_text('verify:\n  script: echo verify\n  rules:\n    - when: never\n')
    st=start(cli,repo)
    child=tmp/'child.pid';grand=tmp/'grand.pid'
    for name,value in {'AL_B14_MODE':mode,'AL_B14_CHILD':str(child),'AL_B14_GRAND':str(grand),'AL_B14_REV':st['identity']['merge_base']}.items():monkeypatch.setenv(name,value)
    if remote:
        bin=tmp/'bin';bin.mkdir();glab=bin/'glab';glab.write_text('#!'+sys.executable+'\n'+PROGRAM);glab.chmod(0o755)
        monkeypatch.setenv('PATH',str(bin)+os.pathsep+os.environ['PATH']);argv=['glab','api','projects/42/jobs/9']
    else:argv=[sys.executable,'scripts/owned_gate.py']
    m=manifest_for(st,repo,anticipated_paths=['svc/app.py','scripts/owned_gate.py'],category='ci' if remote else 'contracts',argv=argv)
    g=next(g for g in m['gates'] if g['id']==('ci' if remote else 'contracts'))
    g['id']='owned-boundary'
    if remote:
        g.update(execution_kind='remote',remote={'provider':'gitlab','project_id':42,'job_name':'verify'},source=next(r for r in m['rules'] if r['path']=='.gitlab-ci.yml')|{'job':'verify'})
    plan(cli,tmp,m)
    return argv,child,grand


def finalized(cli):
    st=state(cli);entry=st['evidence']['entries'][-1]
    assert entry['status']=='INFRA' and entry['interrupted'] is True and entry['exit']!=0
    assert entry['ended_at'] and Path(entry['log']).is_file() and L.file_sha(entry['log'])==entry['log_sha256']
    assert not any(e['status']=='RUNNING' for e in st['evidence']['entries'])
    runs=[r for r in st['gate_runs'] if r['evidence']==entry['id']]
    assert len(runs)==1 and runs[0]['stage']==entry['gate_stage'] and runs[0]['manifest']==entry['gate_manifest']
    assert b'Gate cancelled:' in Path(entry['log']).read_bytes()
    return entry
