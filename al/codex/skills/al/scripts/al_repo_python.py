"""Literal conventional Python layouts and sourced architecture verification."""
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tomllib
from al_repository_facts import digest,gap,safe,within,SOURCE_SUFFIXES

ID='python';VERSION=1


def toml(scan,path,required=False):
    text=scan.text(path,required=required,rule=True)
    if not text:return {}
    try:return tomllib.loads(text)
    except (ValueError,RecursionError) as exc:
        scan.gaps.append(gap('invalid-manifest',path,str(exc)));return {}


def observation(scan):
    return {'units':[],'enumeration':{'boundaries':['.','services/*','packages/*'],'declarations':[], 'observed_members':[], 'absent_paths':[], 'exclusions':[], 'gaps':[]},'checks':{'obligations':[],'bindings':[],'gaps':[]},'policy_selection':[]}


def candidates(scan,nested=False):
    names=set()
    for p in [*scan.members,*scan.base,*scan.paths]:
        parts=Path(p).parts
        if len(parts)>=2 and parts[0] in {'services','packages','apps'}:names.add('/'.join(parts[:2]))
        if nested and len(parts)>2 and parts[0] in {'packages','apps'} and Path(p).name in {'pyproject.toml','package.json','go.mod'}:names.add(str(Path(p).parent))
    if 'pyproject.toml' in scan.files or 'pyproject.toml' in scan.base:names.add('.')
    return {name for name in names if not any(within(o,name) for o in scan.opaque)}


def compatible(requirement,name,version):
    match=re.fullmatch(re.escape(name)+r'(?:\[[A-Za-z0-9_,.-]+\])?\s*(.*)',requirement,re.I)
    if not match or not re.fullmatch(r'\d+(?:\.\d+)*',str(version)):return False
    actual=tuple(int(v) for v in version.split('.'));actual=actual+(0,)*(4-len(actual))
    expression=match[1]
    if not expression:return True
    for part in expression.split(','):
        clause=re.fullmatch(r'\s*(>=|<=|==|!=|>|<|~=)\s*(\d+(?:\.\d+)*)\s*',part)
        if not clause:return False
        raw=tuple(int(v) for v in clause[2].split('.'));value=raw+(0,)*(4-len(raw));op=clause[1]
        if op=='~=':
            index=max(0,len(raw)-2);upper=list(value);upper[index]+=1;upper[index+1:]=[0]*(len(upper)-index-1)
            if not value<=actual<tuple(upper):return False
        elif not {'>=':actual>=value,'<=':actual<=value,'==':actual==value,'!=':actual!=value,'>':actual>value,'<':actual<value}[op]:return False
    return True


def add_unit(scan,facts,path,role=None,language=None):
    safe(path);prefix='' if path=='.' else path+'/'
    config_path=prefix+'pyproject.toml';config=toml(scan,config_path)
    files=[p for p in scan.files if within(path,p)]
    languages=[]
    if any(p.endswith('.py') and '/tests/' not in '/'+p for p in files) or config:languages.append('python')
    if prefix+'package.json' in scan.files:languages.append('javascript');scan.rules.add(prefix+'package.json')
    if prefix+'go.mod' in scan.files:languages.append('go');scan.rules.add(prefix+'go.mod')
    if language and language not in languages:languages.append(language)
    if not languages:
        facts['enumeration']['gaps'].append(gap('unclassified-unit',path,'unit directory has no supported language/manifest',path));return
    roots=[];contexts=[];deps=[];external=[]
    if 'python' in languages:
        if not config:facts['enumeration']['gaps'].append(gap('missing-manifest',config_path,'Python unit requires a literal source manifest',path))
        tool=config.get('tool',{});setuptools=tool.get('setuptools',{});uv=tool.get('uv',{})
        backend=config.get('build-system',{}).get('build-backend')
        if backend and backend not in {'setuptools.build_meta','setuptools.build_meta:__legacy__','uv_build'}:
            facts['enumeration']['gaps'].append(gap('unsupported-build-layout',config_path,'unsupported build/source declaration: '+str(backend),path))
        if any(key in tool for key in ('hatch','poetry','pdm','flit')):
            facts['enumeration']['gaps'].append(gap('unsupported-source-declaration',config_path,'build-specific source declarations require a bundled adapter',path))
        raw=setuptools.get('packages',{});find=raw.get('find',{}) if isinstance(raw,dict) else {}
        declared=find.get('where')
        if declared is None:declared=list(setuptools.get('package-dir',{}).values()) or None
        if declared is None:
            build=uv.get('build-backend',{})
            declared=[build.get('module-root','src' if prefix+'src/' in scan.members else '.')]
        if not isinstance(declared,list) or not declared or any(not isinstance(r,str) or '*' in r or '$' in r for r in declared):
            facts['enumeration']['gaps'].append(gap('unsupported-source-root',config_path,'source roots must be finite literal paths',path));declared=[]
        for r in declared:
            try:root=PurePosixPath(prefix,r).as_posix();safe(root)
            except (ValueError,TypeError):facts['enumeration']['gaps'].append(gap('unsafe-source-root',config_path,'source root escapes repository',path));continue
            roots.append(root)
            if root!='.' and root+'/' not in scan.members:
                facts['enumeration']['gaps'].append(gap('missing-source-root',root,'declared source directory is absent',path))
        if prefix+'setup.py' in scan.files:facts['enumeration']['gaps'].append(gap('executable-layout',prefix+'setup.py','executable setup.py is unsupported',path))
        outside=set()
        for p in files:
            if path=='.' and p.startswith(('services/','packages/','apps/')):continue
            if not p.endswith('.py') or any(within(r,p) for r in roots):continue
            relative=p[len(prefix):];top=relative.split('/')[0]
            if top=='tests':
                facts['enumeration']['exclusions'].append({'path':prefix+'tests','reason':'test input; not installed Python source','provenance':[config_path]})
            elif top not in {'setup.py'}:outside.add(prefix+top)
        for p in sorted(outside):facts['enumeration']['gaps'].append(gap('undeclared-source-root',p,'Python source outside conventional declared roots',path))
        for name,value in uv.get('sources',{}).items():
            if isinstance(value,dict) and isinstance(value.get('path'),str):
                # Resolve literal local dependencies without following filesystem links.
                parts=[]
                for part in PurePosixPath(prefix,value['path']).parts:
                    if part=='..':
                        if not parts:facts['enumeration']['gaps'].append(gap('external-local-dependency',config_path,'local dependency escapes repository',path));break
                        parts.pop()
                    elif part!='.':parts.append(part)
                else:deps.append('/'.join(parts) or '.')
            elif not isinstance(value,dict) or set(value)-{'index','marker','extra'}:facts['enumeration']['gaps'].append(gap('unsupported-dependency-source',config_path,'dynamic or unsupported dependency source',path))
        declared_deps=config.get('project',{}).get('dependencies',[])
        lock=toml(scan,prefix+'uv.lock') if prefix+'uv.lock' in scan.files else {}
        for package in lock.get('package',[]):
            name=package.get('name');source=package.get('source',{})
            override=uv.get('sources',{}).get(name,{})
            external_source=isinstance(override,dict) and not set(override)-{'index','marker','extra'}
            if source.get('registry') and name and external_source and any(isinstance(d,str) and compatible(d,name,package.get('version','')) for d in declared_deps):external.append(name)
        contexts=[{'source_root':r,'local_dependencies':sorted(deps)} for r in sorted(set(roots))]
    unit={'id':path,'path':path,'languages':sorted(languages),'source_roots':sorted(set(roots)), 'module_contexts':contexts,'provenance':[config_path] if config else [prefix+('go.mod' if 'go' in languages else 'package.json')], 'roles':[role] if role else [],'external_distributions':sorted(external),'python_requirement':config.get('project',{}).get('requires-python')}
    facts['units'].append(unit);facts['enumeration']['observed_members'].append(path)
    if config:facts['enumeration']['declarations'].append(config_path)
    if role or path=='.' or path.startswith(('services/','apps/')):checks(scan,facts,unit,config)


def binding(scan,facts,unit,argv,paths,selector,env_names=(),kind='local',cwd='.'):
    chain=[{'path':p,'sha256':digest_bytes(scan.files[p]),'selector':selector,'semantics':'required architecture verification'} for p in sorted(set(paths)) if p in scan.files]
    scan.rules.update(p['path'] for p in chain)
    id='imports-'+unit['id'].replace('/','-').replace('.','root')
    row={'id':id,'category':'lint-imports','units':[unit['id']],'required':True,'argv':argv,'cwd':cwd,'execution_kind':kind,'env_names':list(env_names),'sources':[], 'required_scope':sorted(set([unit['path'],*paths])), 'source_chain':chain,'obligation_ids':[id]}
    row['execution_signature']=digest(row)
    facts['checks']['obligations'].append({'id':id,'category':'lint-imports','units':[unit['id']]});facts['checks']['bindings'].append(row)
    return row


def digest_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def checks(scan,facts,unit,config):
    prefix='' if unit['path']=='.' else unit['path']+'/'
    tool=config.get('tool',{});arch=tool.get('al',{}).get('architecture')
    paths=[prefix+'pyproject.toml']+[p for p in (prefix+'uv.lock',) if p in scan.files]
    if isinstance(arch,dict) and set(arch)=={'forbidden'} and isinstance(arch['forbidden'],list) and arch['forbidden'] and all(isinstance(v,str) and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',v) for v in arch['forbidden']):
        from al_runtime_resources import resource_identity,resource_bytes
        runner=resource_identity('al_architecture_check.py')
        row=binding(scan,facts,unit,[sys.executable,'-I',str(runner),'--unit',unit['id']],paths,'tool.al.architecture')
        row['runner_resource']={'name':'al_architecture_check.py','version':1,'sha256':digest_bytes(resource_bytes('al_architecture_check.py'))};row['execution_signature']=digest({k:v for k,v in row.items() if k!='execution_signature'})
    elif isinstance(tool.get('importlinter'),dict) and tool['importlinter'].get('root_packages'):
        binding(scan,facts,unit,['uv','run','--frozen','lint-imports','--no-logo'],paths,'tool.importlinter',env_names=('PATH','PYTHONPATH','UV_PROJECT_ENVIRONMENT','UV_PYTHON'),cwd=unit['path'])
    else:
        declared_architecture=any(key in tool for key in ('importlinter',)) or 'architecture' in tool.get('al',{})
        id=('imports-' if declared_architecture else 'verification-')+unit['id'].replace('/','-').replace('.','root')
        facts['checks']['obligations'].append({'id':id,'category':'lint-imports' if declared_architecture else 'ci','units':[unit['id']]})
        facts['checks']['gaps'].append(gap('missing-architecture-check' if declared_architecture else 'missing-verification-check',prefix+'pyproject.toml','declared architecture policy has no supported executable binding' if declared_architecture else 'service has no discoverable required verification check',unit['id'],'lint-imports'))


def workspace_members(scan,facts):
    patterns=[]
    root=toml(scan,'pyproject.toml') if 'pyproject.toml' in scan.files else {}
    workspace=root.get('tool',{}).get('uv',{}).get('workspace',{})
    if workspace:
        if not isinstance(workspace,dict) or set(workspace)-{'members'}:
            facts['enumeration']['gaps'].append(gap('unsupported-workspace','pyproject.toml','only literal uv workspace members are supported'))
        elif isinstance(workspace.get('members'),list):patterns.extend(workspace['members'])
        else:facts['enumeration']['gaps'].append(gap('unsupported-workspace','pyproject.toml','workspace members must be a literal list'))
    if 'pnpm-workspace.yaml' in scan.files:
        from al_ci_yaml import load,Unsupported
        try:
            value=load(scan.text('pnpm-workspace.yaml',rule=True))
            if set(value)!={'packages'} or not isinstance(value['packages'],list):raise Unsupported('literal workspace package list required')
            patterns.extend(value['packages'])
        except (Unsupported,ValueError) as exc:facts['enumeration']['gaps'].append(gap('unsupported-workspace','pnpm-workspace.yaml',str(exc)))
    if 'package.json' in scan.files:
        try:
            data=json.loads(scan.text('package.json',rule=True));members=data.get('workspaces',[])
            if isinstance(members,dict):members=members.get('packages')
            if not isinstance(members,list):raise ValueError('literal workspace list required')
            patterns.extend(members)
        except (ValueError,AttributeError) as exc:facts['enumeration']['gaps'].append(gap('unsupported-workspace','package.json',str(exc)))
    explicit=set()
    for pattern in patterns:
        if pattern in {'services/*','packages/*'}:continue
        try:
            safe(pattern)
            if '*' in pattern or not pattern.startswith(('services/','packages/')):raise ValueError('unsupported workspace member '+str(pattern))
            explicit.add(pattern)
        except (TypeError,ValueError) as exc:facts['enumeration']['gaps'].append(gap('unsupported-workspace','.',str(exc)))
    if patterns:facts['enumeration']['declarations'].append({'workspace_members':sorted(map(str,patterns))})
    return root,explicit


def observe(scan):
    facts=observation(scan);root,declared=workspace_members(scan,facts);names=candidates(scan)|declared
    # A uv monorepo manifest without a project is a bounded container, not a
    # fabricated root service. Its child units still require manifests/checks.
    if '.' in names and not root.get('project') and root.get('tool',{}).get('uv',{}).get('workspace') and not any(p.endswith('.py') and not p.startswith(('services/','packages/','tests/')) for p in scan.files):
        names.remove('.')
        facts['enumeration']['exclusions'].append({'path':'.','reason':'literal uv workspace container','provenance':['pyproject.toml']})
    for p in scan.files:
        if Path(p).name=='pyproject.toml' and str(Path(p).parent) not in names and p!='pyproject.toml':
            facts['enumeration']['gaps'].append(gap('unsupported-unit-layout',p,'Python manifest outside supported root/services/packages layout'))
        if p.endswith(SOURCE_SUFFIXES) and not any(within(unit,p) for unit in names):
            facts['enumeration']['gaps'].append(gap('unsupported-source-root',p,'source-bearing root has no supported manifest/ownership boundary'))
    for path in sorted(names):
        add_unit(scan,facts,path)
        if path.startswith('apps/') and any(u['id']==path and 'python' in u['languages'] for u in facts['units']):facts['enumeration']['gaps'].append(gap('unsupported-unit-layout',path,'generic Python apps layout needs a bundled repository adapter',path))
    return facts
