"""Real terminal-query refusal at moving snapshot/origin/data boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from al.installer.bootstrap import bootstrap_cli
from al.installer.validator_boot import BOOT
from al.tests.conftest import copy_al_source


def prepare(tmp_path,platform):
    source=copy_al_source(tmp_path/'trusted/al')
    repo=tmp_path/'repository';(repo/'svc').mkdir(parents=True)
    (repo/'svc/app.py').write_text('value = 1\n')
    (repo/'pyproject.toml').write_text('[project]\nname="terminal-fixture"\ndependencies=[]\n[tool.setuptools.packages.find]\nwhere=["svc"]\n[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n')
    (repo/'.gitignore').write_text('.codex/al-runtime/\n.claude/al-runtime/\n__pycache__/\n')
    environment=dict(os.environ,GIT_AUTHOR_NAME='t',GIT_AUTHOR_EMAIL='t@t',GIT_COMMITTER_NAME='t',GIT_COMMITTER_EMAIL='t@t',PYTHONDONTWRITEBYTECODE='1')
    for argv in [('init','-q'),('add','.'),('commit','-qm','fixture')]:
        subprocess.run(['git',*argv],cwd=repo,env=environment,check=True,capture_output=True,timeout=10)
    helper=Path(__file__).with_name('terminal_fixture.py')
    result=subprocess.run([sys.executable,'-I','-S','-B',str(helper),str(source),platform,str(repo),str(tmp_path/'artifacts'),str(tmp_path/'installer-state')],
                           cwd=repo,env=environment,capture_output=True,text=True,timeout=300)  # ~40 real launcher commands
    assert result.returncode==0,result.stderr[-4000:]+result.stdout[-1000:]
    state=json.loads(result.stdout)
    launcher=tmp_path/'bin/alctl';bootstrap=bootstrap_cli(source,launcher,tmp_path/'installer-state')
    # Queries run from the same execution environment that produced the approval.
    # terminal_fixture drives its launcher without AL_RUNTIME_DIR/PYTHONPATH/PYTHONHOME.
    state['environment']={k:v for k,v in environment.items() if k not in {'AL_RUNTIME_DIR','PYTHONPATH','PYTHONHOME'}}
    return source,repo,state,bootstrap


@pytest.mark.parametrize('platform',['codex','claude'])
def test_actual_query_refuses_post_child_snapshot_origin_and_target_drift(tmp_path,platform):
    source,repo,state,bootstrap=prepare(tmp_path,platform)
    snapshot=Path(bootstrap['snapshot']);saved=json.loads(Path(state['state']).read_bytes())
    targets={
        'snapshot':snapshot/platform/'skills/al/scripts/al_store.py',
        'origin-metadata':snapshot/'origin.json',
        'original-resource':source/platform/'skills/al/scripts/al_architecture_check.py',
        'state':Path(state['state']),
        'candidate':repo/'svc/app.py',
        'artifact':Path(saved['review']['comparison']['stored_path']),
        'new-untracked':repo/'svc/new.py',
    }
    request={'mode':'snapshot','root':str(snapshot),'expected':bootstrap['validator_binding']}
    for kind,path in targets.items():
        before=path.read_bytes() if path.exists() else None;artifact=tmp_path/(kind+'-completed-child-proof.json')
        # Trusted test host installs the real protected snapshot context, then
        # changes one actual owned input after the real child has succeeded.
        driver=BOOT+'''
request=json.loads(sys.argv[1]);captured=_boot(request)
utility=_utility(captured,'validator_loader.py');utility.install_closed_loader(captured,'codex',installers=True)
import installer.terminal_source as terminal
real=terminal.subprocess.run
mutated=[]
def run(argv,*args,**kwargs):
 result=real(argv,*args,**kwargs)
 if terminal.CHILD in argv:
  assert result.returncode==0,result.stderr[-1000:]+result.stdout[-1000:]
  Path(sys.argv[7]).write_bytes(result.stdout)
  path=Path(sys.argv[6]);path.write_bytes((path.read_bytes() if path.exists() else b'')+b'\\n')
  mutated.append(True)
 return result
terminal.subprocess.run=run
try:
 terminal.terminal_source(sys.argv[2],session=sys.argv[3],run=sys.argv[4],platform=sys.argv[5])
except ValueError as exc:
 assert mutated,str(exc)
 print(json.dumps({'refused':True,'reason':str(exc)}))
else:raise AssertionError('moving terminal query returned approval proof')
'''
        try:
            result=subprocess.run([sys.executable,'-I','-S','-B','-c',driver,json.dumps(request),str(repo),state['session'],state['run'],platform,str(path),str(artifact)],
                                   cwd=repo,env=state['environment'],capture_output=True,text=True,timeout=120)
            assert result.returncode==0,kind+': '+result.stderr[-2500:]+result.stdout[-1000:]
            assert json.loads(result.stdout)['refused']
            proof=json.loads(artifact.read_bytes())
            assert proof['validator']['origin_digest']==bootstrap['validator_binding']['origin_digest']
        finally:
            if before is None:path.unlink(missing_ok=True)
            else:path.write_bytes(before)

    state_path=Path(state['state']);original_state=state_path.read_bytes()
    suite=next(e for e in saved['evidence']['entries'] if e['area']=='suite')
    baseline_files={repo/'svc/app.py':(repo/'svc/app.py').read_bytes(),
                    Path(saved['review']['comparison']['stored_path']):Path(saved['review']['comparison']['stored_path']).read_bytes(),
                    Path(suite['log']):Path(suite['log']).read_bytes()}
    for kind in ('stale-review','stale-evidence','candidate','altered-argv'):
        value=json.loads(original_state)
        if kind=='stale-review':
            path=Path(saved['review']['comparison']['stored_path']);path.write_bytes(baseline_files[path]+b'\n')
        elif kind=='stale-evidence':
            path=Path(suite['log']);path.write_bytes(baseline_files[path]+b'\n')
        elif kind=='candidate':(repo/'svc/app.py').write_text('value = 2\n')
        else:
            import hashlib
            ref=value['gate_manifests'][-1]
            manifest=json.loads(Path(ref['stored_path']).read_bytes())
            gate=next(g for g in manifest['gates'] if g['id']=='imports-root')
            gate['argv']=[sys.executable,'-c','print("forged")']
            raw=json.dumps(manifest).encode();sha=hashlib.sha256(raw).hexdigest()
            forged=Path(ref['stored_path']).with_name(sha);forged.write_bytes(raw)
            ref.update(id=sha,sha256=sha,stored_path=str(forged))
            state_path.write_text(json.dumps(value))
        try:
            result=subprocess.run([bootstrap['destination'],'source-proof','--repository',str(repo),
                                   '--session',state['session'],'--run',state['run'],'--platform',platform],
                                  cwd=repo,capture_output=True,text=True,timeout=120)
            assert result.returncode!=0,kind+': stale terminal source was approved'
            if kind=='altered-argv':assert 'corrupt coverage gate binding: imports-root' in result.stdout,result.stdout[-2000:]
            assert state_path.read_bytes()==(json.dumps(value).encode() if kind=='altered-argv' else original_state)
        finally:
            state_path.write_bytes(original_state)
            for path,raw in baseline_files.items():path.write_bytes(raw)
