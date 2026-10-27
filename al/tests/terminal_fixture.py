"""Prepare an actual approved disposable run with one explicitly selected runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    source,platform,repository,artifacts,installer_state=map(Path,sys.argv[1:])
    platform=str(platform)
    scripts=source/platform/'skills/al/scripts'
    sys.path[:0]=[str(scripts),str(source/platform/'skills/al/tests')]
    os.chdir(repository)
    runtime=repository/('.'+platform)/'al-runtime'
    if platform=='claude':os.environ['CLAUDE_PROJECT_DIR']=str(repository)
    artifacts.mkdir(parents=True,exist_ok=True)
    # Every command runs through the real managed launcher, pinned where the
    # installed read-only validator later looks up the run binding.
    launcher=artifacts/'bin/alctl'
    boot=subprocess.run([sys.executable,'-I','-S','-B',str(source/'alctl.py'),'bootstrap','--source-root',str(source),
                         '--destination',str(launcher),'--state-root',str(installer_state)],
                        capture_output=True,text=True,timeout=60)
    assert boot.returncode==0,boot.stdout[-2000:]+boot.stderr[-2000:]
    environment={k:v for k,v in os.environ.items() if k not in {'AL_RUNTIME_DIR','PYTHONPATH','PYTHONHOME'}}
    session='terminal-fixture'
    def cli(*args):
        result=subprocess.run([str(launcher),'run','--repository',str(repository),'--platform',platform,
                               '--session',session,'--',*args],env=environment,capture_output=True,text=True,timeout=120)
        return (0 if result.returncode==0 else result.stderr.strip() or result.returncode),result.stdout,result.stderr
    cli.state=lambda:json.loads((runtime/(session+'.json')).read_bytes())
    result=cli('start','--spec','Verify a scoped implementation and preserve its repository evidence.')
    assert result[0]==0,str(result)[-4000:]
    # Local manifest computation must see the same pinned resource identities as the run.
    from launcher_context import bind_pinned_resources,current_protocol,admit_planning
    transaction=json.loads(next((installer_state/'al-run-pins/runs').glob('*.json')).read_bytes())
    bind_pinned_resources(installer_state/'al-run-pins',transaction['pin'],platform)
    os.environ['AL_RUNTIME_DIR']=str(runtime)
    from evidence_helpers import plan_na_gates,plan_and_run_broad,complete_review
    cli=current_protocol(cli)
    for args in (('spec-gate','SPEC_READY'),('classify','STANDARD','--designer','no','--implementers','al-engineer')):
        result=cli(*args);assert result[0]==0,str(result)[-4000:]
    plan_na_gates(cli,artifacts)
    admit_planning(cli,artifacts)
    for phase in ('IMPLEMENTATION','FINALIZATION','VERIFICATION'):
        result=cli('phase',phase);assert result[0]==0,str(result)[-4000:]
    assert plan_and_run_broad(cli)==0
    complete_review(cli,artifacts,blocking=False)
    for args in [('phase','FINAL_VERIFICATION'),('approved','--with-notes','0')]:
        result=cli(*args);assert result[0]==0,str(result)[-4000:]
    state=cli.state()
    print(json.dumps({'session':session,'run':state['run_id'],'state':str(runtime/(session+'.json'))}))


if __name__=='__main__':main()
