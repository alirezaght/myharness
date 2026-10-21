"""Disposable real old-public-workflow, current policy, and installed validator."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from al.installer.bootstrap import bootstrap_cli
from al.installer.issuer_provenance import capture_issuer,retain_issuer
from al.installer.registry import canonical
from al.tests.old_issuer_input import materialize,verify
from al.tests.old_issuer_review import complete

ROOT=Path(__file__).resolve().parents[2]


def setup_tools(directory):
    tools=directory/'tools';tools.mkdir();home=directory/'tool-home';home.mkdir()
    uv=shutil.which('uv');ruff=shutil.which('ruff') or str(ROOT/'.venv/bin/ruff')
    if not uv or not Path(ruff).is_file():raise RuntimeError('compatibility test requires real uv and Ruff tools')
    shutil.copy2(ruff,tools/'ruff')
    (tools/'pytest').write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -I -m pytest "$@"\n');(tools/'pytest').chmod(0o755)
    installed=Path.home()/'.local/share/uv/python'
    versions=sorted(p for p in installed.glob('cpython-3.12.*') if p.is_dir() and not p.is_symlink())
    if not versions:raise RuntimeError('offline compatibility test requires already installed managed CPython 3.12')
    python=directory/'managed-python';python.mkdir();shutil.copytree(versions[-1],python/versions[-1].name,symlinks=True)
    env={k:v for k,v in os.environ.items() if not k.startswith(('AL_','PYTHON','UV_','GIT_'))}
    env.update(HOME=str(home),PATH=str(tools)+os.pathsep+os.environ['PATH'],UV_CACHE_DIR=str(directory/'uv-cache'),
               UV_PYTHON_INSTALL_DIR=str(python),UV_PYTHON_DOWNLOADS='never',UV_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1')
    return env


class Workflow:
    def __init__(self,directory,platform):
        self.directory=directory;self.platform=platform;self.env=setup_tools(directory)
        from al.tests.pytest_locked_fixture import wheelhouse
        self.issuer_home=directory/'issuer-home';self.issuer,self.issuer_files=materialize(self.issuer_home,platform)
        self.repo=directory/'repository';self.repo.mkdir();self.pytest_wheels=wheelhouse(self.repo/'fixture-wheels');self.artifacts=directory/'artifacts';self.artifacts.mkdir()
        self.runtime=self.repo/('.'+platform)/'al-runtime';self.env['AL_RUNTIME_DIR']=str(self.runtime)
        if platform=='claude':self.env['CLAUDE_PROJECT_DIR']=str(self.repo)
        self.session='old-issued-fixture';self.transcript=[]
        self.create_project()
        self.cli('start','--spec','Verify all configured checks with the same original issuing authority.')
        self.cli('spec-gate','SPEC_READY');self.cli('classify','STANDARD','--designer','no','--implementers','al-engineer')
        self.run=self.state()['run_id']
        self.capture()
        self.boot=bootstrap_cli(ROOT/'al',directory/'bin/alctl',directory/'installer-state')

    def command(self,argv,*,allow=False):
        index=len(self.transcript)+1;result=subprocess.run(list(map(str,argv)),cwd=self.repo,env=self.env,capture_output=True,text=True,timeout=180)
        stdout=self.artifacts/(str(index)+'-stdout.txt');stderr=self.artifacts/(str(index)+'-stderr.txt')
        stdout.write_text(result.stdout);stderr.write_text(result.stderr)
        self.transcript.append({'argv':list(map(str,argv)),'exit':result.returncode,'stdout':str(stdout),'stderr':str(stderr),
                                'stdout_sha256':hashlib.sha256(stdout.read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256(stderr.read_bytes()).hexdigest()})
        (self.artifacts/'transcript.json').write_text(json.dumps(self.transcript,indent=2))
        if not allow:assert result.returncode==0,str(argv)+': '+result.stdout[-2500:]+result.stderr[-2500:]
        return result

    def cli(self,*args,allow=False):
        verify(self.issuer_home,self.platform,self.issuer_files)
        return self.command([sys.executable,self.issuer/'scripts/al_state.py','--session',self.session,*args],allow=allow)

    def state(self):return json.loads((self.runtime/(self.session+'.json')).read_bytes())

    def put(self,name,text):
        path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)

    def create_project(self):
        workflow=(ROOT/'al/tests/fixtures/conventional-quality.yml').read_text()
        self.put('pyproject.toml','[project]\nname="ordinary-compatibility"\nversion="0.1.0"\nrequires-python=">=3.12"\ndependencies=[]\n[build-system]\nrequires=["uv_build>=0.10,<0.11"]\nbuild-backend="uv_build"\n[dependency-groups]\ndev=["pytest>=8"]\n[tool.uv]\npackage=false\nenvironments=["sys_platform != \'win32\'"]\nfind-links=[WHEELHOUSE]\n[tool.uv.build-backend]\nmodule-root=""\nmodule-name="svc"\n[tool.pytest.ini_options]\ntestpaths=["tests"]\n'.replace('WHEELHOUSE',json.dumps('fixture-wheels')))
        self.put('.python-version','3.12\n');self.put('svc/__init__.py','');self.put('svc/app.py','VALUE = 1\n')
        self.put('tests/__init__.py','');self.put('tests/test_value.py','from svc.app import VALUE\n\n\ndef test_value_is_supported():\n    assert VALUE in (1, 2)\n')
        self.put('.github/workflows/quality.yml',workflow)
        self.put('.gitignore','.venv/\ndist/\n__pycache__/\n.pytest_cache/\n.ruff_cache/\n.codex/\n.claude/\n')
        self.command(['uv','lock','--offline'])
        self.command(['git','init','-q']);self.command(['git','config','user.name','Fixture']);self.command(['git','config','user.email','fixture@example.test'])
        self.command(['git','add','.']);self.command(['git','commit','-qm','original conventional project'])

    def capture(self):
        files={};roles={}
        for name,row in self.issuer_files.items():
            path=Path(name)
            if path.is_relative_to(self.issuer):files[path.relative_to(self.issuer).as_posix()]=row
            else:roles[path.name]=row
        invocation={'schema_version':1,'kind':'al-issuer-invocation-provenance',
          'known_supervisor_invocation':{'argv':[sys.executable,str(self.issuer/'scripts/al_state.py'),'--session',self.session]},
          'run':{'repository':str(self.repo),'session':self.session,'run':self.run},
          'platforms':{self.platform:{'original_installation':str(self.issuer),'files':files,'roles':roles}}}
        path=self.artifacts/'fixture-invocation.json';path.write_bytes(canonical(invocation))
        captured=capture_issuer(self.issuer,platform=self.platform,repository=self.repo,session=self.session,run=self.run,
             invocation_provenance={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},execution_profile=True)
        self.binding=retain_issuer(captured,self.directory/'retained-issuer')
        self.binding_file=self.artifacts/'issuer-binding.json';self.binding_file.write_bytes(canonical(self.binding))
        self.binding_sha=hashlib.sha256(self.binding_file.read_bytes()).hexdigest()

    def candidate(self,command,*args,allow=False):
        return self.command([self.boot['destination'],command,'--repository',self.repo,'--session',self.session,'--run',self.run,
            '--platform',self.platform,*args],allow=allow)

    def proof(self,command,*args,allow=False):
        return self.candidate(command,'--issuer-binding',self.binding_file,'--issuer-binding-sha256',self.binding_sha,*args,allow=allow)

    def verification(self):
        state=self.state();source='.github/workflows/quality.yml';sha=hashlib.sha256((self.repo/source).read_bytes()).hexdigest()
        gate={'id':'old-quality','category':'ci','applicability':'APPLICABLE','required':True,'units':['.'],
              'argv':['sh','-eu','-c','uv python install\nuv sync --locked\nuv run ruff check .\nuv run ruff format --check .\nuv run pytest\nuv build'],
              'scope':['.'],'env_names':['HOME','PATH','UV_CACHE_DIR','UV_PYTHON_INSTALL_DIR','UV_PYTHON_DOWNLOADS','UV_OFFLINE'],
              'cwd':'.','source':{'path':source,'sha256':sha,'job':'quality'},'execution_kind':'local'}
        gates=[gate]+[{'id':c,'category':c,'applicability':'NOT_APPLICABLE','required':False,'units':['.'],'argv':[],
                      'scope':['.'],'cwd':'.','source':gate['source'],'execution_kind':'local','reason':'No corresponding declaration in this conventional fixture.'} for c in ('lint-imports','loc','contracts','pre-commit')]
        self.initial={'schema_version':1,'base_oid':state['identity']['merge_base'],'paths':['.'],'units':['.'],
                      'rules':[{'path':source,'sha256':sha,'job':None},
                               {'path':'pyproject.toml','sha256':hashlib.sha256((self.repo/'pyproject.toml').read_bytes()).hexdigest(),'job':None}],'gates':gates}
        path=self.artifacts/'original-manifest.json';path.write_text(json.dumps(self.initial))
        self.cli('gates','plan','--file',path);self.original_ref=self.state()['gate_manifests'][-1]
        self.original_raw=Path(self.original_ref['stored_path']).read_bytes()
        self.cli('gates','run','--stage','base');self.cli('phase','IMPLEMENTATION')
        self.cli('writer','start','--id','old-engineer','--role','al-engineer','--files','svc/app.py')
        self.put('svc/app.py','VALUE = 2\n');self.cli('writer','finish','--id','old-engineer','--outcome','success')
        before=(self.runtime/(self.session+'.json')).read_bytes()
        refused=self.cli('gates','plan','--file',path,allow=True)
        assert refused.returncode!=0 and 'do not permit replacement planning' in refused.stderr
        assert (self.runtime/(self.session+'.json')).read_bytes()==before
        self.cli('phase','FINALIZATION');self.cli('phase','VERIFICATION')
        self.plan_broad()

    def plan_broad(self,scope='.'):
        self.cli('evidence','plan','--area','actual-pytest','--tier','broad','--kind','test','--scope',scope,
                 '--env','PATH','--','pytest','-q','tests')

    def broad(self):
        self.cli('evidence','run','--area','actual-pytest')

    def replacement(self):
        result=self.proof('replan-proof');value=json.loads(result.stdout)
        self.replan=self.artifacts/'current-policy-proof.json';self.replan.write_text(result.stdout)
        self.proposal=self.artifacts/'current-proposal.json';self.proposal.write_text(json.dumps(value['proposal']))
        assert value['proposal']['service_coverage']['assessment']['outcome']!='incomplete'
        assert value['previous_manifest_digest']==self.original_ref['sha256']
        self.cli('gates','plan','--file',self.proposal)
        assert Path(self.original_ref['stored_path']).read_bytes()==self.original_raw
        state=self.state();assert len(state['gate_manifests'])==2
        self.replacement_ref=state['gate_manifests'][-1]
        stored=json.loads(Path(self.replacement_ref['stored_path']).read_bytes())
        assert stored==value['proposal']
        self.cli('gates','run','--stage','base');self.cli('gates','run','--stage','candidate')
        self.broad()
        readiness=['--expected-manifest',self.replacement_ref['sha256'],'--replan-proof',self.replan,
                   '--replan-proof-sha256',hashlib.sha256(self.replan.read_bytes()).hexdigest()]
        self.plan_broad('svc')
        refused=self.proof('compatibility-readiness',*readiness,allow=True)
        assert refused.returncode!=0 and 'prior broader required plan changed' in refused.stdout,refused.stdout[-2000:]
        self.plan_broad()
        old=self.env['UV_PYTHON_DOWNLOADS'];self.env['UV_PYTHON_DOWNLOADS']='automatic'
        try:
            refused=self.proof('compatibility-readiness',*readiness,allow=True)
            assert refused.returncode!=0 and 'proposal environment changed' in refused.stdout,refused.stdout[-2000:]
        finally:self.env['UV_PYTHON_DOWNLOADS']=old
        area='current-policy-readiness'
        self.cli('evidence','plan','--area',area,'--tier','broad','--kind','check','--scope','.',
                 '--env','HOME,PATH,UV_CACHE_DIR,UV_PYTHON_INSTALL_DIR,UV_PYTHON_DOWNLOADS,UV_OFFLINE','--',
                 self.boot['destination'],'compatibility-readiness','--repository',self.repo,'--session',self.session,
                 '--run',self.run,'--platform',self.platform,'--issuer-binding',self.binding_file,
                 '--issuer-binding-sha256',self.binding_sha,'--expected-manifest',self.replacement_ref['sha256'],
                 '--replan-proof',self.replan,'--replan-proof-sha256',hashlib.sha256(self.replan.read_bytes()).hexdigest(),'--self-area',area)
        self.cli('evidence','run','--area',area)
        from al.tests.old_issuer_proof import retain_readiness
        retain_readiness(self,self.state()['evidence']['entries'][-1])
        return value

    def approve(self):
        from al.installer.issuer_provenance import verify_issuer
        live=verify_issuer(self.binding['root'],self.binding,observe_live=True)
        (self.artifacts/'preapproval-live-issuer.json').write_text(json.dumps(live))
        complete(self.cli,self.state,self.artifacts)
        self.cli('phase','FINAL_VERIFICATION');self.cli('approved','--with-notes','0')
        assert self.state()['status']=='APPROVED' and self.state()['phase']=='DONE'

    def terminal(self,name):
        state_path=self.runtime/(self.session+'.json');before=state_path.read_bytes();index=(self.repo/'.git/index').read_bytes()
        result=self.candidate('source-proof');proof=json.loads(result.stdout)
        (self.artifacts/(name+'-source-proof.json')).write_text(result.stdout)
        assert state_path.read_bytes()==before and (self.repo/'.git/index').read_bytes()==index
        assert proof['approval']['fingerprint']==self.state()['approval']['fingerprint']
        return proof
