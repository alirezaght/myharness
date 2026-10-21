"""Bounded application fixtures with real sourced manifest verification."""
import json
from conftest import _git


def seed_ui(repo, path):
    unit = '/'.join(path.split('/')[:2])
    target = repo / path;target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('export const initial = true;\n')
    (repo / unit / 'package.json').write_text(json.dumps({'name': unit.split('/')[-1]}) + '\n')
    (repo / '.gitlab-ci.yml').write_text('application-verify:\n  script: python3 -m json.tool ' + unit + '/package.json\n')
    _git(repo, 'add', '.');_git(repo, 'commit', '-qm', 'declare bounded application and sourced verification')
