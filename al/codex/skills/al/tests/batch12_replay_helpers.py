"""Real selected-package replay fixtures; commands stay structured input data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PACKAGE=Path(__file__).resolve().parents[1]
SEARCH="rg 'git reset > cp --fix' ."


def fixture():
    return json.loads((PACKAGE/'reference/replay/harmless-searches.json').read_text())


def selected(tmp,name,append=None,missing=False):
    package=tmp/name;shutil.copytree(PACKAGE/'scripts',package/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
    shell=package/'scripts/al_shell.py'
    if missing:shell.unlink()
    elif append:
        with shell.open('a') as stream:stream.write('\n'+append+'\n')
    return package


def reject():
    return "_prior=check\ndef check(command,policy,inherited_environment=()):\n    if command=="+repr(SEARCH)+":return ['selected guard regression']\n    return _prior(command,policy,inherited_environment)"


def run(tmp,candidate,case=None,baseline=None):
    input_path=tmp/'case.json';input_path.write_text(json.dumps(case if case is not None else fixture()))
    output=tmp/'report.json'
    argv=[sys.executable,str(PACKAGE/'scripts/al_replay.py'),'--fixtures',str(input_path),'--candidate',str(candidate),'--output',str(output)]
    if baseline:argv+=['--baseline',str(baseline)]
    result=subprocess.run(argv,cwd=tmp,capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(PACKAGE/'scripts')},timeout=60)
    assert output.is_file(),result.stdout+result.stderr
    return result.returncode,json.loads(output.read_text())


def policy_rows(report):return [r for r in report['cases'][0]['candidate']['outcomes'] if r['kind']=='policy']
