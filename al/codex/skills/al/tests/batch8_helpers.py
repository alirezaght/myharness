"""Sourced CI fixtures with concrete job declarations and public CLI setup."""
import json
from conftest import _git
import al_artifacts as A
from hook_regression_helpers import invoke
from test_hook_batch7 import _planning


def setup(cli,repo,tmp,definitions,paths):
    ignored=repo/'.gitignore'
    lines=ignored.read_text().splitlines() if ignored.exists() else []
    if '__pycache__/' not in lines:ignored.write_text('\n'.join([*lines,'__pycache__/']).lstrip('\n')+'\n')
    # The apps fixtures use literal package manifests so sourced json.tool jobs
    # validate actual unit inputs rather than substituting a print command.
    for path in paths:
        parts=path.split('/')
        if len(parts)>1 and parts[0]=='apps':
            manifest=repo/parts[0]/parts[1]/'package.json'
            manifest.parent.mkdir(parents=True,exist_ok=True)
            if not manifest.exists():manifest.write_text('{"name":"'+parts[1]+'"}\n')
    for name,text in definitions.items():
        file=repo/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
    _git(repo,'add','.');_git(repo,'commit','-qm','seed CI job identities')
    _planning(cli)
    file=tmp/'paths.json';file.write_text(json.dumps(paths))
    invoke(cli,'gates','discover','--paths-file',str(file))
    state=json.loads(cli('show')[1]);return A.read_json(state['gate_manifests'][-1])


def executable_ci(manifest):
    """Return discovery unchanged; fixture execution must come from sourced CI."""
    return manifest


def plan(cli,tmp,manifest,ok=True):
    file=tmp/'planned.json';file.write_text(json.dumps(manifest));return invoke(cli,'gates','plan','--file',str(file),ok=ok)


def two_jobs():
    return {'.gitlab-ci.yml': 'web-test:\n  script: python3 -m json.tool apps/web/package.json\n  rules:\n    - changes: [apps/web/**/*]\nadmin-test:\n  script: python3 -m json.tool apps/admin/package.json\n  rules:\n    - changes: [apps/admin/**/*]\n'}


def force_applicable_ci(manifest):
    """Negative-test forgery: deliberately replace unresolved sourced semantics."""
    import sys
    for gate in manifest['gates']:
        if gate['category'] == 'ci' and gate['applicability'] == 'UNRESOLVED':
            gate.update(applicability='APPLICABLE', required=True,
                        argv=[sys.executable, '-c', 'print("forged verification")'])
    return manifest
