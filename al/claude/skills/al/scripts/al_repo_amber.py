"""Amber's reviewed workspace, CI inventory and architecture-check chain."""
import ast
import hashlib
import json
from pathlib import Path
import re
from al_ci_yaml import load,Unsupported
import al_repo_python as P
from al_repository_facts import gap,within,safe
from al_discovery_inputs import recipe

ID='amber';VERSION=1
SELECTOR='ci/scripts/import_review/checker.py'
SELECTOR_AST='019c1a084a318b0a844add61bfe77cf21aa564a4896c6b1219e40fec1ca7d914'
HELPER='''set -uo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
service="${1:?usage: fast-check-lint-imports.sh services/<name>}"
cd "${root}/${service}" || exit 2
rc=0
PYTHONPATH=src uv run --extra dev --frozen lint-imports --no-logo || rc=1
uv run --extra dev --frozen ruff check src --config "${root}/packages/tooling/ruff/architecture.toml" || rc=1
exit "${rc}"'''


def unsupported(data):
    if isinstance(data,dict):return '__unsupported__' in data or any(unsupported(v) for v in data.values())
    if isinstance(data,list):return any(unsupported(v) for v in data)
    return False


def yaml(scan,path):
    try:
        data=load(scan.text(path,required=True,rule=True))
        if unsupported(data):raise Unsupported('unsupported partial declaration')
        return data
    except (Unsupported,ValueError,RecursionError) as exc:scan.gaps.append(gap('unsupported-declaration',path,str(exc)));return {}


def observe(scan):
    facts=P.observation(scan);facts['enumeration']['boundaries']+=['apps/*','packages/**','ci/manifest.yml']
    manifest=yaml(scan,'ci/manifest.yml');workspace=yaml(scan,'pnpm-workspace.yaml')
    patterns=workspace.get('packages',[])
    if not isinstance(patterns,list) or any(p not in {'apps/*','services/*','packages/*','packages/**','docs/architecture/likec4'} for p in patterns):
        scan.gaps.append(gap('unsupported-workspace','pnpm-workspace.yaml','workspace membership expression unsupported'))
    declared={};names=P.candidates(scan,nested=True)
    for section in ('services','apps'):
        rows=manifest.get(section,{})
        if not isinstance(rows,dict):scan.gaps.append(gap('invalid-ci-units','ci/manifest.yml','unit map required'));continue
        for role,data in rows.items():
            if not isinstance(data,dict):scan.gaps.append(gap('invalid-ci-unit','ci/manifest.yml','literal unit mapping required'));continue
            slug=data.get('slug',role.replace('_','-'));path=data.get('package_dir',('services/'+slug+'-service') if section=='services' else 'apps/'+slug)
            try:safe(path)
            except (ValueError,TypeError):scan.gaps.append(gap('unsafe-ci-unit','ci/manifest.yml','unsafe package_dir'));continue
            names.add(path);declared.setdefault(path,[]).append((role,data.get('language')))
    # Containers are not units when nested manifests identify their members and
    # no immediate source/manifest exists. Everything else must be classified.
    for path in sorted(names):
        if any(other!=path and within(path,other) for other in names) and not any(path+'/'+n in scan.files for n in ('pyproject.toml','package.json','go.mod')) and not any(p.startswith(path+'/src/') for p in scan.files):
            facts['enumeration']['exclusions'].append({'path':path,'reason':'workspace container','provenance':['pnpm-workspace.yaml']});continue
        roles=declared.get(path,[])
        before=len(facts['units']);P.add_unit(scan,facts,path,role=roles[0][0] if roles else None,language=roles[0][1] if roles else None)
        if len(facts['units'])==before:continue
        unit=facts['units'][-1];unit['roles']=[r for r,_ in roles]
        if 'python' not in unit['languages']:
            facts['enumeration']['exclusions'].append({'path':path,'reason':'non-Python language '+','.join(unit['languages']),'provenance':unit['provenance']})
        elif path.startswith('services/'):
            # Amber requires BOTH import-linter and Ruff, not generic checks.
            ids={b['id'] for b in facts['checks']['bindings'] if path in b['units']}
            facts['checks']['bindings']=[b for b in facts['checks']['bindings'] if b['id'] not in ids]
            facts['checks']['obligations']=[b for b in facts['checks']['obligations'] if path not in b['units']]
            facts['checks']['gaps']=[g for g in facts['checks']['gaps'] if g.get('unit')!=path]
            amber_check(scan,facts,unit)
    if SELECTOR in scan.files:
        try:
            fn=next(n for n in ast.parse(scan.text(SELECTOR,rule=True)).body if isinstance(n,ast.FunctionDef) and n.name=='affected_services')
            if hashlib.sha256(ast.dump(fn,include_attributes=False).encode()).hexdigest()!=SELECTOR_AST:raise ValueError('unrecognized service selection policy')
            if any(p.startswith('packages/') or p.startswith('ci/scripts/import_review/') or p=='ci/import-review-baseline.json' for p in scan.paths):
                facts['policy_selection']=sorted(u['id'] for u in facts['units'] if u['path'].startswith('services/') and 'python' in u['languages'])
        except (SyntaxError,StopIteration,ValueError) as exc:scan.gaps.append(gap('unsupported-selection',SELECTOR,str(exc)))
    for p in scan.files:
        if p.startswith('ci/scripts/import_review/') and p.endswith('.py') or p=='ci/import-review-baseline.json':scan.rules.add(p)
    return facts


def amber_check(scan,facts,unit):
    path=unit['path'];make=scan.text('Makefile',required=True,rule=True);body=recipe(make,'lint-imports')
    expected='pnpm -w lint:imports services/$(SVC)-service'
    commands=[line.strip().lstrip('@') for line in body.splitlines() if line.strip()]
    valid=len(commands)==2 and commands[0]=='test -n "$(SVC)" || { echo "SVC is required, e.g. make $@ SVC=shortlist"; exit 1; }' and commands[1]==expected
    valid=valid and not re.search(r'^(?:-?include|sinclude|override|export|unexport|SVC\s*[:?+]?=|SHELL\s*[:?+]?=|\.ONESHELL|\.SHELLFLAGS)',make,re.M)
    try:package=json.loads(scan.text('package.json',required=True,rule=True));valid=valid and package['scripts']['lint:imports']=='bash ci/scripts/fast-check-lint-imports.sh'
    except (ValueError,KeyError,TypeError):valid=False
    helper='ci/scripts/fast-check-lint-imports.sh';text=scan.text(helper,required=True,rule=True)
    normalized='\n'.join(line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    valid=valid and normalized==HELPER
    configs=['packages/tooling/ruff/architecture.toml']
    architecture=P.toml(scan,configs[0],required=True)
    extend=architecture.get('extend')
    if isinstance(extend,str) and '/' not in extend and '..' not in extend:configs.append('packages/tooling/ruff/'+extend);P.toml(scan,configs[-1],required=True)
    elif extend:valid=False
    valid=valid and bool(architecture.get('lint',{}).get('select'))
    config=P.toml(scan,path+'/pyproject.toml',required=True)
    valid=valid and bool(config.get('tool',{}).get('importlinter',{}).get('root_packages'))
    paths=['Makefile','package.json','pnpm-workspace.yaml',helper,'ci/manifest.yml',path+'/pyproject.toml',*configs]
    paths += [p for p in (path+'/uv.lock','pnpm-lock.yaml') if p in scan.files]
    if valid and path.split('/')[-1].endswith('-service'):
        P.binding(scan,facts,unit,['make','lint-imports','SVC='+path.split('/')[-1][:-8]],paths,'lint-imports chain',env_names=('PATH','UV_PROJECT_ENVIRONMENT','UV_PYTHON'))
    else:
        id='imports-'+path.replace('/','-')
        facts['checks']['obligations'].append({'id':id,'category':'lint-imports','units':[path]})
        facts['checks']['gaps'].append(gap('unsupported-architecture-chain',helper,'Amber requires the reviewed Make/package/helper/import-linter/Ruff failure chain',path,'lint-imports'))
