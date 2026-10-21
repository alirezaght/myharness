"""Prepare an actual approved disposable run with one explicitly selected runtime."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys


def main():
    source,platform,repository,artifacts=map(Path,sys.argv[1:])
    platform=str(platform)
    scripts=source/platform/'skills/al/scripts'
    sys.path[:0]=[str(scripts),str(source/platform/'skills/al/tests')]
    os.chdir(repository)
    runtime=repository/('.'+platform)/'al-runtime'
    os.environ['AL_RUNTIME_DIR']=str(runtime)
    if platform=='claude':os.environ['CLAUDE_PROJECT_DIR']=str(repository)
    import al_state
    from evidence_helpers import start_implementation,plan_and_run_broad,complete_review
    session='terminal-fixture'
    def cli(*args):
        sys.argv=['al_state.py','--session',session,*args]
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            try:al_state.main();code=0
            except SystemExit as exc:code=exc.code
        return code,out.getvalue(),err.getvalue()
    cli.state=lambda:json.loads((runtime/(session+'.json')).read_bytes())
    artifacts.mkdir(parents=True,exist_ok=True)
    start_implementation(cli,artifacts)
    for phase in ('FINALIZATION','VERIFICATION'):
        result=cli('phase',phase);assert result[0]==0,str(result)[-4000:]
    assert plan_and_run_broad(cli)==0
    complete_review(cli,artifacts,blocking=False)
    for args in [('phase','FINAL_VERIFICATION'),('approved','--with-notes','0')]:
        result=cli(*args);assert result[0]==0,str(result)[-4000:]
    state=cli.state()
    print(json.dumps({'session':session,'run':state['run_id'],'state':str(runtime/(session+'.json'))}))


if __name__=='__main__':main()
