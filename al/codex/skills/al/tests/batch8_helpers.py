"""Sourced CI fixtures with concrete job declarations and public CLI setup."""
import json
import sys
from conftest import _git
import al_artifacts as A
from hook_regression_helpers import invoke
from test_hook_batch7 import _planning


def setup(cli,repo,tmp,definitions,paths):
    for name,text in definitions.items():
        file=repo/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
    _git(repo,'add','.');_git(repo,'commit','-qm','seed CI job identities')
    _planning(cli)
    file=tmp/'paths.json';file.write_text(json.dumps(paths))
    invoke(cli,'gates','discover','--paths-file',str(file))
    state=json.loads(cli('show')[1]);return A.read_json(state['gate_manifests'][-1])


def executable_ci(manifest):
    for gate in manifest['gates']:
        if gate['category']=='ci':
            gate.update(applicability='APPLICABLE',argv=[sys.executable,'-c','print("local check passed")'],reason='Explicit local verification associated with the sourced job')
    return manifest


def plan(cli,tmp,manifest,ok=True):
    file=tmp/'planned.json';file.write_text(json.dumps(manifest));return invoke(cli,'gates','plan','--file',str(file),ok=ok)


def two_jobs():
    return {'.gitlab-ci.yml': 'web-test:\n  script: echo web\n  rules:\n    - changes: [apps/web/**/*]\nadmin-test:\n  script: echo admin\n  rules:\n    - changes: [apps/admin/**/*]\n'}
