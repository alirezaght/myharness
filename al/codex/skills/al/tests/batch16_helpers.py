"""Self-contained public writer fixtures with independently sourced obligations."""
import hashlib
import json
import os
import subprocess
import sys
import al_gate_inventory as V
import al_store
from hook_regression_helpers import invoke,manifest_for
from launcher_context import admit_planning


def git(repo,*args):
    subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True,env={**os.environ,'GIT_AUTHOR_NAME':'fixture','GIT_AUTHOR_EMAIL':'fixture@example.invalid','GIT_COMMITTER_NAME':'fixture','GIT_COMMITTER_EMAIL':'fixture@example.invalid'})


def state(cli):return json.loads(cli('show')[1])


def manifest(cli,repo,paths):
    st=state(cli);inv=V.inventory(st,paths)
    source={'path':'Makefile','sha256':hashlib.sha256((repo/'Makefile').read_bytes()).hexdigest()}
    m=manifest_for(st,repo,anticipated_paths=paths,category='loc',argv=[sys.executable,'-c',"print('gate passed')"],source=source)
    return m


def plan(cli,tmp,m):
    p=tmp/'manifest.json';p.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(p))


def setup(cli,repo,tmp,paths=('docs/readme.md',)):
    (repo/'Makefile').write_text('verify:\n\t@true\n')
    (repo/'docs/existing.md').write_text('tracked sibling\n');git(repo,'add','.');git(repo,'commit','-qm','fixture rules')
    invoke(cli,'start','--spec','Require current and prospective repository gates before admitting each writer.')
    invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    m=manifest(cli,repo,paths);plan(cli,tmp,m);invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    return m


def writer(cli,path,identifier='writer',ok=True):return invoke(cli,'writer','start','--id',identifier,'--role','al-engineer','--files',path,ok=ok)


def members(st):
    root=al_store.RUNTIME_DIR/st['session_id']/'artifacts'
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
