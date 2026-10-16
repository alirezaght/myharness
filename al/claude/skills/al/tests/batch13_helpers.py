"""Public gate fixtures use only disposable repositories and a local glab stub."""
import json
import os
from pathlib import Path
import subprocess
import sys
import al_ledger as L
from hook_regression_helpers import invoke


def commit(repo):
    subprocess.run(['git','add','--all'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','-c','user.name=test','-c','user.email=test@example.invalid','commit','-qm','boundary fixture'],cwd=repo,check=True,capture_output=True)


def start(cli,repo):
    (repo/'Makefile').write_text('all:\n\ttrue\n');commit(repo)
    invoke(cli,'start','--spec','Materialize and validate this disposable gate boundary safely.')
    return json.loads(cli('show')[1])


def manifest(cli,tmp,argv,category='contracts',remote=False,cwd='.'):
    paths=['Makefile','svc/app.py'];file=tmp/'paths.json';file.write_text(json.dumps(paths))
    m=json.loads(invoke(cli,'gates','discover','--paths-file',str(file))[1]);rule=next(r for r in m['rules'] if r['path']=='Makefile')
    for gate in m['gates']:
        gate.update(applicability='NOT_APPLICABLE',required=False,reason='No obligation exists in this isolated fixture.',argv=[],source=dict(rule),execution_kind='local',scope=m['paths'],units=m['units'],cwd='.')
    gate=next(g for g in m['gates'] if g['category']==category)
    gate.update(id='boundary',applicability='APPLICABLE',required=True,reason=None,argv=argv,cwd=cwd)
    if remote:
        gate.update(execution_kind='remote',remote={'provider':'gitlab','project_id':42,'job_name':'verify'})
        gate['source']['job']='verify'
    file=tmp/'gates.json';file.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(file))


def stub(tmp,monkeypatch,response,launched=None):
    bindir=tmp/'bin';bindir.mkdir();path=tmp/'response.bin';path.write_bytes(response)
    script=bindir/'glab';script.write_text('#!'+sys.executable+'\nimport os,sys\nfrom pathlib import Path\n'+
        ("Path("+repr(str(launched))+ ").write_text('launched')\n" if launched else '')+
        "sys.stdout.buffer.write(Path(os.environ['AL_BOUND_RESPONSE']).read_bytes())\n")
    script.chmod(0o755);monkeypatch.setenv('PATH',str(bindir)+os.pathsep+os.environ['PATH']);monkeypatch.setenv('AL_BOUND_RESPONSE',str(path))


def body(state):return {'id':9,'name':'verify','pipeline':{'id':11,'project_id':42},'commit':{'id':state['identity']['merge_base']},'status':'success','web_url':None}


def finished(cli,status,materialized=True):
    state=json.loads(cli('show')[1]);entry=state['evidence']['entries'][-1]
    assert entry['status']==status and entry['ended_at'] and entry['log_sha256']==L.file_sha(entry['log'])
    assert not any(e['status']=='RUNNING' for e in state['evidence']['entries'])
    association=state['gate_runs'][-1]
    assert association['evidence']==entry['id'] and association['manifest']==state['gate_manifests'][-1]['sha256']
    assert (association['fingerprint'] is not None)==materialized
    return entry,Path(entry['log']).read_bytes()
