"""Opt-in declared repository contract: literal units, a hash-bound architecture chain and
AL-computed selection rules. Repository code is never read, executed or recognized as policy."""
import fnmatch
import json
import re
from pathlib import PurePosixPath
import al_repo_python as P
from al_repository_facts import gap,safe,within

ID='contract';VERSION=1
CONTRACT='.al/repository.json'
KIND='al-repository-contract-v1'
KEYS={'schema_version','kind','units','containers','architecture_check','selection','independent_languages'}
CHECK_KEYS={'argv','unit_name','applies_to','chain','env_names'}
LANGUAGES={'python','go','javascript'}
ENV=re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


def _path(value):
    if not isinstance(value,str) or not value or value.startswith('/') or '..' in PurePosixPath(value).parts:return False
    try:safe(value)
    except (ValueError,TypeError):return False
    return True


def _strings(value,allow_empty=False):
    return isinstance(value,list) and (allow_empty or value) and all(isinstance(v,str) and v for v in value)


def validate(data):
    """Every problem with a contract; any problem makes the contract unusable (fail closed)."""
    if not isinstance(data,dict):return ['contract must be a JSON object']
    problems=['unknown contract field: '+k for k in sorted(set(data)-KEYS)]
    if data.get('schema_version')!=1 or type(data.get('schema_version')) is not int:problems.append('unsupported contract schema_version')
    if data.get('kind')!=KIND:problems.append('unsupported contract kind')
    units=data.get('units')
    if not isinstance(units,list) or not units:problems.append('units must be a non-empty list')
    else:
        seen=set()
        for unit in units:
            if not isinstance(unit,dict) or set(unit)-{'path','role','language'} or not _path(unit.get('path')):
                problems.append('invalid unit declaration: '+json.dumps(unit)[:200]);continue
            if unit['path'] in seen:problems.append('duplicate unit: '+unit['path'])
            seen.add(unit['path'])
            if 'role' in unit and (not isinstance(unit['role'],str) or not unit['role']):problems.append('invalid unit role: '+unit['path'])
            if 'language' in unit and unit['language'] not in LANGUAGES:problems.append('unsupported unit language: '+unit['path'])
    if 'containers' in data and (not _strings(data['containers'],True) or not all(_path(p) for p in data['containers'])):
        problems.append('containers must be literal repository paths')
    if 'independent_languages' in data and (not _strings(data['independent_languages'],True) or set(data['independent_languages'])-(LANGUAGES-{'python'})):
        problems.append('independent_languages must name non-Python languages')
    check=data.get('architecture_check')
    if check is not None:
        if not isinstance(check,dict) or set(check)-CHECK_KEYS or not {'argv','applies_to','chain'}<=set(check):
            problems.append('invalid architecture_check fields')
        else:
            if not _strings(check['argv']) or any(re.search(r'\{(?!unit\}|unit_name\})',a) for a in check['argv']):
                problems.append('architecture_check argv must be literal strings with only {unit}/{unit_name}')
            if check['applies_to']!='python-services':problems.append('unsupported architecture_check applies_to')
            if not _strings(check['chain']) or not all(_path(p) for p in check['chain']):problems.append('architecture_check chain must list literal repository files')
            name=check.get('unit_name',{})
            if not isinstance(name,dict) or set(name)-{'strip_suffix'} or not isinstance(name.get('strip_suffix',''),str):
                problems.append('invalid architecture_check unit_name')
            if not isinstance(check.get('env_names',[]),list) or not all(isinstance(n,str) and ENV.match(n) for n in check.get('env_names',[])):
                problems.append('invalid architecture_check env_names')
    selection=data.get('selection',[])
    if not isinstance(selection,list) or any(not isinstance(rule,dict) or set(rule)!={'changed','selects'} or rule['selects']!='python-services'
                                             or not _strings(rule['changed']) or not all(_path(g) for g in rule['changed']) for rule in selection):
        problems.append('selection rules must map literal globs to python-services')
    return problems


def contract(scan):
    text=scan.text(CONTRACT,required=True,rule=True)
    if not text:return None
    try:data=json.loads(text)
    except ValueError as exc:
        scan.gaps.append(gap('unsupported-contract',CONTRACT,'invalid JSON: '+str(exc)));return None
    problems=validate(data)
    for problem in problems:scan.gaps.append(gap('unsupported-contract',CONTRACT,problem))
    return None if problems else data


def observe(scan):
    facts=P.observation(scan);facts['enumeration']['boundaries'].append(CONTRACT)
    data=contract(scan)
    if data is None:return facts
    declared={u['path']:u for u in data['units']};containers=set(data.get('containers',[]))
    independent=set(data.get('independent_languages',[]));check=data.get('architecture_check')
    for path in sorted((P.candidates(scan,nested=True)|set(declared))-containers):
        unit_declaration=declared.get(path,{})
        if path in declared and not any(within(path,p) for p in [*scan.files,*scan.members]):
            facts['enumeration']['gaps'].append(gap('contract-unit-missing',CONTRACT,'declared unit is absent: '+path,path));continue
        before=len(facts['units'])
        P.add_unit(scan,facts,path,role=unit_declaration.get('role'),language=unit_declaration.get('language'))
        if len(facts['units'])==before:continue
        unit=facts['units'][-1];unit['roles']=[unit_declaration['role']] if 'role' in unit_declaration else []
        if unit['roles'] and set(unit['languages'])<=independent:unit['independent']=True
        if 'python' not in unit['languages']:
            facts['enumeration']['exclusions'].append({'path':path,'reason':'non-Python language '+','.join(unit['languages']),'provenance':unit['provenance']})
        elif check and path.startswith('services/'):
            ids={b['id'] for b in facts['checks']['bindings'] if path in b['units']}
            facts['checks']['bindings']=[b for b in facts['checks']['bindings'] if b['id'] not in ids]
            facts['checks']['obligations']=[o for o in facts['checks']['obligations'] if path not in o['units']]
            facts['checks']['gaps']=[g for g in facts['checks']['gaps'] if g.get('unit')!=path]
            architecture(scan,facts,unit,check)
    for path in sorted(containers):
        facts['enumeration']['exclusions'].append({'path':path,'reason':'declared workspace container','provenance':[CONTRACT]})
    if any(fnmatch.fnmatchcase(p,glob) for rule in data.get('selection',[]) for glob in rule['changed'] for p in scan.paths):
        facts['policy_selection']=sorted(u['id'] for u in facts['units'] if u['path'].startswith('services/') and 'python' in u['languages'])
    return facts


def architecture(scan,facts,unit,check):
    path=unit['path'];name=path.split('/')[-1];suffix=check.get('unit_name',{}).get('strip_suffix','')
    chain=[CONTRACT,*check['chain'],path+'/pyproject.toml',*[p for p in (path+'/uv.lock',) if p in scan.files]]
    present=all(scan.text(p,required=True,rule=True) for p in check['chain'])
    if suffix and not name.endswith(suffix):present=False
    if present:
        value=name[:-len(suffix)] if suffix else name
        argv=[a.replace('{unit_name}',value).replace('{unit}',path) for a in check['argv']]
        P.binding(scan,facts,unit,argv,chain,'repository contract architecture chain',env_names=tuple(check.get('env_names',[])))
    else:
        identifier='imports-'+path.replace('/','-')
        facts['checks']['obligations'].append({'id':identifier,'category':'lint-imports','units':[path]})
        facts['checks']['gaps'].append(gap('unsupported-architecture-chain',CONTRACT,'declared architecture chain is incomplete for '+path,path,'lint-imports'))
