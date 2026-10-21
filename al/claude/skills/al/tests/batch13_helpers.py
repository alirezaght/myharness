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
    ignored=repo/'.gitignore'
    lines=ignored.read_text().splitlines() if ignored.exists() else []
    if '__pycache__/' not in lines:ignored.write_text('\n'.join([*lines,'__pycache__/']).lstrip('\n')+'\n')
    (repo/'Makefile').write_text('all:\n\ttrue\n');commit(repo)
    invoke(cli,'start','--spec','Materialize and validate this disposable gate boundary safely.')
    return cli.state()


def manifest(cli,tmp,argv,category='contracts',remote=False,cwd='.',anticipated=()):
    paths=list(dict.fromkeys(['Makefile','svc/app.py',*anticipated]));file=tmp/'paths.json';file.write_text(json.dumps(paths))
    m=json.loads(invoke(cli,'gates','discover','--paths-file',str(file))[1]);rule=next(r for r in m['rules'] if r['path']=='Makefile')
    # Keep all independently discovered service bindings and unresolved required
    # checks. Only categories proven absent in this fixture may be declared N/A.
    absent={
        'loc': not (Path.cwd()/'scripts/check-loc.py').is_file(),
        'pre-commit': not any((Path.cwd()/path).is_file() for path in ('.husky/pre-commit','.githooks/pre-commit')),
        'contracts': not any('contract' in key and ('gen' in key or 'build' in key)
                             for package in [Path.cwd()/'package.json',*(Path.cwd()/unit/'package.json' for unit in m['units'])]
                             if package.is_file() for key in json.loads(package.read_text()).get('scripts',{})),
        'ci': not (Path.cwd()/'.gitlab-ci.yml').is_file(),
    }
    for discovered in m['gates']:
        if discovered['category'] in absent and absent[discovered['category']]:
            discovered.update(applicability='NOT_APPLICABLE',required=False,
                              reason='Independently inspected fixture has no '+discovered['category']+' obligation.',
                              source=rule)
    gate={'id':'boundary','category':'contracts','applicability':'APPLICABLE','required':True,
          'reason':'Disposable gate-runner boundary fixture','units':m['units'],'argv':argv,
          'scope':m['paths'],'source':dict(rule),'execution_kind':'local','cwd':cwd,'env_names':[],'sources':[]}
    if remote:
        gate.update(execution_kind='remote',remote={'provider':'gitlab','project_id':42,'job_name':'verify'})
        gate['source']['job']='verify'
    m['gates'].append(gate)
    file=tmp/'gates.json';file.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(file))


def stub(tmp,monkeypatch,response,launched=None):
    bindir=tmp/'bin';bindir.mkdir();path=tmp/'response.bin';path.write_bytes(response)
    script=bindir/'glab';script.write_text('#!'+sys.executable+'\nimport os,sys\nfrom pathlib import Path\n'+
        ("Path("+repr(str(launched))+ ").write_text('launched')\n" if launched else '')+
        "sys.stdout.buffer.write(Path(os.environ['AL_BOUND_RESPONSE']).read_bytes())\n")
    script.chmod(0o755);monkeypatch.setenv('PATH',str(bindir)+os.pathsep+os.environ['PATH']);monkeypatch.setenv('AL_BOUND_RESPONSE',str(path))


def body(state):return {'id':9,'name':'verify','pipeline':{'id':11,'project_id':42},'commit':{'id':state['identity']['merge_base']},'status':'success','web_url':None}


def finished(cli,status,materialized=True):
    state=cli.state();entry=state['evidence']['entries'][-1]
    assert entry['status']==status and entry['ended_at'] and entry['log_sha256']==L.file_sha(entry['log'])
    assert not any(e['status']=='RUNNING' for e in state['evidence']['entries'])
    association=state['gate_runs'][-1]
    assert association['evidence']==entry['id'] and association['manifest']==state['gate_manifests'][-1]['sha256']
    assert (association['fingerprint'] is not None)==materialized
    return entry,Path(entry['log']).read_bytes()


def assert_incomplete_admission(cli):
    """A standalone gate receipt cannot authorize incomplete service coverage."""
    import al_artifacts as A
    import al_store
    state=json.loads(cli('show')[1])
    manifest=A.read_json(state['gate_manifests'][-1])
    assert manifest['service_coverage']['assessment']['outcome']=='incomplete'
    invoke(cli,'spec-gate','SPEC_READY')
    invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    before=al_store.state_path(state['session_id']).read_bytes()
    code,out,err=invoke(cli,'phase','IMPLEMENTATION',ok=False)
    assert 'gate' in str(code)+out+err
    assert al_store.state_path(state['session_id']).read_bytes()==before
    invoke(cli,'approved',ok=False)
