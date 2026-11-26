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


def source_witnesses(scan, children=()):
    """Bounded ownership witnesses, not installed-package or check declarations."""
    children=set(children)-{'.'}
    for p in [*scan.members,*scan.base,*scan.paths]:
        parts=PurePosixPath(p).parts
        if len(parts)>=2 and parts[0] in {'services','packages','apps'} and (len(parts)>=3 or p.endswith('/')):
            children.add('/'.join(parts[:2]))
    for p in set(scan.files)|set(scan.base):
        parent=PurePosixPath(p).parent.as_posix()
        if parent!='.' and PurePosixPath(p).name in WORKSPACE_MANIFESTS:children.add(parent)
    witnesses=[]
    for snapshot,files in [('current',scan.files),('base',scan.base)]:
        paths=[]
        for p in files:
            parts=PurePosixPath(p).parts;name=parts[-1]
            if (not p.endswith('.py') or any(part in {'test','tests','__pycache__'} for part in parts)
                    or name=='conftest.py' or name.startswith('test_') or name.endswith('_test.py')
                    or any(within(root,p) for root in children|set(scan.opaque))):continue
            paths.append(p)
        for p in sorted(paths)[:4]:
            witnesses.append({'path':p,'snapshot':snapshot,'sha256':digest_bytes(files[p])})
    return witnesses


def candidates(scan,nested=False):
    names=set()
    for p in [*scan.members,*scan.base,*scan.paths]:
        parts=Path(p).parts
        # A unit is a directory: a file directly under services/, packages/ or apps/ is not one.
        if len(parts)>=2 and parts[0] in {'services','packages','apps'} and (len(parts)>=3 or p.endswith('/')):names.add('/'.join(parts[:2]))
        if nested and len(parts)>2 and parts[0] in {'packages','apps'} and Path(p).name in {'pyproject.toml','package.json','go.mod'}:names.add(str(Path(p).parent))
    if any(p in scan.files or p in scan.base for p in ('pyproject.toml','package.json')):names.add('.')
    elif source_witnesses(scan,names):names.add('.')
    return {name for name in names if not any(within(o,name) for o in scan.opaque)}


WORKSPACE_MANIFESTS=('package.json','pyproject.toml','go.mod')


def tooling_root(path):
    """The directory that names a manifest-less tooling root: `scripts`, `services/x/alembic`, or `.` for root files."""
    parts=PurePosixPath(path).parts[:-1]
    if not parts:return '.'
    return '/'.join(parts[:3 if parts[0] in {'services','packages','apps'} else 1])


def expand_members(pattern,files):
    """Expand a workspace glob (`dir/*` or `dir/**`) to the directories in the tree that hold a manifest."""
    parts=PurePosixPath(pattern).parts
    if any(part not in {'*','**'} and '*' in part for part in parts) or '**' in parts[:-1]:
        raise ValueError('unsupported workspace member '+pattern)
    found=set()
    for path in files:
        if PurePosixPath(path).name not in WORKSPACE_MANIFESTS:continue
        directory=PurePosixPath(path).parent.parts
        if parts[-1]=='**':
            matched=len(directory)>len(parts)-1 and directory[:len(parts)-1]==parts[:-1]
        else:
            matched=len(directory)==len(parts) and all(p=='*' or p==d for p,d in zip(parts,directory))
        if matched and 'node_modules' not in directory:found.add('/'.join(directory))
    return found


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


def layout_clear(scan,facts,path):
    """A manifest cannot prove ownership across an unresolved source boundary."""
    if any(within(path,child) or within(child,path) for child in scan.opaque):return False
    return not any(g.get('unit')==path or within(path,g.get('path') or '.')
                   for g in [*facts['enumeration']['gaps'],*scan.gaps])


def owned_children(scan,facts,path):
    children=[]
    for unit in facts['units']:
        child=unit['path'];manifest=child+'/pyproject.toml'
        if child!=path and within(path,child) and manifest in scan.files and manifest in unit['provenance'] and layout_clear(scan,facts,child):children.append(child)
    return sorted(children)


def add_unit(scan,facts,path,role=None,language=None,*,children=()):
    safe(path);prefix='' if path=='.' else path+'/'
    config_path=prefix+'pyproject.toml';config=toml(scan,config_path)
    witnesses=source_witnesses(scan,children) if path=='.' and config_path not in scan.files and config_path not in scan.base else []
    inferred=bool(witnesses) and config_path not in scan.files and config_path not in scan.base
    files=[p for p in scan.files if within(path,p) and not any(within(child,p) for child in children)]
    languages=[]
    python_sources=sorted(p for p in files if p.endswith('.py') and '/tests/' not in '/'+p)
    if python_sources or config or inferred:languages.append('python')
    if prefix+'package.json' in scan.files:languages.append('javascript');scan.rules.add(prefix+'package.json')
    if prefix+'go.mod' in scan.files:languages.append('go');scan.rules.add(prefix+'go.mod')
    if language and language not in languages:languages.append(language)
    if not languages:
        declared=any(prefix+name in scan.files or prefix+name in scan.base for name in WORKSPACE_MANIFESTS)
        if children and not declared and not any(p.endswith(SOURCE_SUFFIXES) for p in files) and layout_clear(scan,facts,path):
            facts['enumeration']['exclusions'].append({'path':path,'reason':'manifested child source ownership container','provenance':[child+'/pyproject.toml' for child in children]})
            return
        facts['enumeration']['gaps'].append(gap('unclassified-unit',path,'unit directory has no supported language/manifest',path));return
    roots=[];contexts=[];deps=[];external=[];packages=None
    if 'python' in languages:
        if not config and not inferred:
            reason='Python unit requires a literal source manifest'
            if python_sources:
                witness=python_sources[0]
                reason+='; observed Python source '+json.dumps(witness[:160])+(' (path truncated)' if len(witness)>160 else '')
            facts['enumeration']['gaps'].append(gap('missing-manifest',config_path,reason,path))
        tool=config.get('tool',{});setuptools=tool.get('setuptools',{});uv=tool.get('uv',{})
        backend=config.get('build-system',{}).get('build-backend')
        if backend and backend not in {'setuptools.build_meta','setuptools.build_meta:__legacy__','uv_build','hatchling.build'}:
            facts['enumeration']['gaps'].append(gap('unsupported-build-layout',config_path,'unsupported build/source declaration: '+str(backend),path))
        if any(key in tool for key in ('poetry','pdm','flit')) or ('hatch' in tool and backend!='hatchling.build'):
            facts['enumeration']['gaps'].append(gap('unsupported-source-declaration',config_path,'build-specific source declarations require a bundled adapter',path))
        raw=setuptools.get('packages',{});find=raw.get('find',{}) if isinstance(raw,dict) else {}
        declared=find.get('where')
        if declared is None:declared=list(setuptools.get('package-dir',{}).values()) or None
        if declared is None:
            build=uv.get('build-backend',{})
            declared=['.'] if inferred else [build.get('module-root','src' if prefix+'src/' in scan.members else '.')]
        if not isinstance(declared,list) or not declared or any(not isinstance(r,str) or '*' in r or '$' in r for r in declared):
            facts['enumeration']['gaps'].append(gap('unsupported-source-root',config_path,'source roots must be finite literal paths',path));declared=[]
        if backend=='hatchling.build':
            from al_repo_hatch import packages as hatch_packages
            roots,packages=hatch_packages(scan,facts,path,config);declared=[]
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
            if not p.endswith('.py') or any(within(r,p) for r in (packages if packages is not None else roots)):continue
            relative=p[len(prefix):];top=relative.split('/')[0]
            if top=='tests':
                facts['enumeration']['exclusions'].append({'path':prefix+'tests','reason':'test input; not installed Python source','provenance':[config_path]})
            elif top not in {'setup.py'}:outside.add(p if packages is not None else prefix+top)
        # Migrations, scripts and entry points beside a service's package are its tooling.
        for p in sorted(outside):facts['enumeration']['exclusions'].append({'path':p,'reason':'service tooling outside declared source roots','provenance':[config_path]})
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
    if inferred:
        unit['source_witnesses']=witnesses
        unit['provenance']=sorted({w['path'] for w in witnesses}|{p for p in ('package.json','go.mod') if p in scan.files or p in scan.base})
    if packages is not None:unit['python_packages']=packages
    facts['units'].append(unit);facts['enumeration']['observed_members'].append(path)
    if config:facts['enumeration']['declarations'].append(config_path)
    if role or path=='.' or path.startswith(('services/','apps/')):
        if 'python' in languages:checks(scan,facts,unit,config)
        if 'javascript' in languages:
            import al_package_checks
            al_package_checks.observe(scan,facts,unit)
    return unit


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


def linter_selection(config):
    """`uv run` installs the default groups only: name the extra or group that declares import-linter."""
    def declares(requirements):
        return isinstance(requirements,list) and any(isinstance(r,str) and re.match(r'import[-_]linter\b',r,re.I) for r in requirements)
    if declares(config.get('project',{}).get('dependencies')):return []
    groups=config.get('dependency-groups',{})
    for name in sorted(groups) if isinstance(groups,dict) else ():
        if declares(groups[name]):return [] if name=='dev' else ['--group',name]
    extras=config.get('project',{}).get('optional-dependencies',{})
    for name in sorted(extras) if isinstance(extras,dict) else ():
        if declares(extras[name]):return ['--extra',name]
    return []


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
        argv=['uv','run','--frozen',*linter_selection(config),'lint-imports','--no-logo']
        binding(scan,facts,unit,argv,paths,'tool.importlinter',env_names=('PATH','PYTHONPATH','UV_PROJECT_ENVIRONMENT','UV_PYTHON'),cwd=unit['path'])
    else:
        declared_architecture=any(key in tool for key in ('importlinter',)) or 'architecture' in tool.get('al',{})
        id=('imports-' if declared_architecture else 'verification-')+unit['id'].replace('/','-').replace('.','root')
        facts['checks']['obligations'].append({'id':id,'category':'lint-imports' if declared_architecture else 'ci','units':[unit['id']]})
        witness=unit.get('source_witnesses',[{'path':prefix+'pyproject.toml'}])[0]['path']
        facts['checks']['gaps'].append(gap('missing-architecture-check' if declared_architecture else 'missing-verification-check',witness,'declared architecture policy has no supported executable binding' if declared_architecture else 'service has no discoverable required verification check',unit['id'],'lint-imports'))


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
    explicit=set();excluded=set()
    for pattern in patterns:
        if pattern in {'services/*','packages/*'}:continue
        try:
            negated=isinstance(pattern,str) and pattern.startswith('!')
            literal=pattern[1:] if negated else pattern
            safe(literal.replace('**','x').replace('*','x'))
            if not literal.startswith(('services/','packages/','apps/')):
                # Docs or build tooling declared as a workspace member is not a service.
                if not negated:facts['enumeration']['exclusions'].append({'path':literal,'reason':'workspace member outside services/packages/apps: tooling, not a service','provenance':['workspace declaration']})
                continue
            members=expand_members(literal,scan.files) if '*' in literal else {literal}
            (excluded if negated else explicit).update(members)
        except (TypeError,ValueError) as exc:facts['enumeration']['gaps'].append(gap('unsupported-workspace','.',str(exc)))
    explicit-=excluded
    if patterns:facts['enumeration']['declarations'].append({'workspace_members':sorted(map(str,patterns))})
    return root,explicit


def supported_app_context(scan,facts,unit):
    """Positive literal evidence, never merely an apps/ directory name."""
    path=unit['path'];config=toml(scan,path+'/pyproject.toml')
    if not config or not unit['source_roots']:return False
    tool=config.get('tool',{});setuptools=tool.get('setuptools',{})
    packages=setuptools.get('packages',{})
    explicit=isinstance(packages,dict) and bool(packages.get('find',{}).get('where')) or bool(setuptools.get('package-dir')) or 'module-root' in tool.get('uv',{}).get('build-backend',{})
    explicit=explicit or bool(unit.get('python_packages'))
    if not explicit:return False
    gaps=[*facts['enumeration']['gaps'],*scan.gaps]
    return not any(g.get('unit')==path or within(path,g.get('path','.')) for g in gaps)


def observe(scan):
    facts=observation(scan);root,declared=workspace_members(scan,facts);names=candidates(scan)|declared
    # A uv monorepo manifest without a project is a bounded container, not a
    # fabricated root service. Its child units still require manifests/checks.
    if '.' in names and not root.get('project') and root.get('tool',{}).get('uv',{}).get('workspace') and not any(p.endswith('.py') and not p.startswith(('services/','packages/','apps/','tests/')) for p in scan.files):
        names.remove('.')
        facts['enumeration']['exclusions'].append({'path':'.','reason':'literal uv workspace container','provenance':['pyproject.toml']})
    tooling=set()
    for p in scan.files:
        if Path(p).name=='pyproject.toml' and str(Path(p).parent) not in names and p!='pyproject.toml':
            if p.startswith(('services/','packages/','apps/')):
                facts['enumeration']['gaps'].append(gap('unsupported-unit-layout',p,'Python manifest outside supported root/services/packages layout'))
            else:tooling.add(tooling_root(p))
        if p.endswith(SOURCE_SUFFIXES) and not any(within(unit,p) for unit in names):
            tooling.add(tooling_root(p))
    # Manifest-less source outside every unit (scripts, importers, CI helpers) is tooling: one
    # recorded exclusion per root, outside service coverage, instead of a gap per file.
    for root in sorted(tooling):
        facts['enumeration']['exclusions'].append({'path':root,'reason':'manifest-less tooling root outside service coverage','provenance':['repository layout']})
    # Only already admitted children with valid current manifests can own descendant bytes.
    for path in sorted(names,key=lambda p:(-len(PurePosixPath(p).parts),p)):
        unit=add_unit(scan,facts,path,children=owned_children(scan,facts,path))
        if path.startswith('apps/') and unit and 'python' in unit['languages'] and not supported_app_context(scan,facts,unit):facts['enumeration']['gaps'].append(gap('unsupported-unit-layout',path,'generic Python apps layout needs a bundled repository adapter',path))
    facts['units'].sort(key=lambda unit:unit['id'])
    facts['enumeration']['observed_members'].sort()
    unique=[]
    for item in facts['enumeration']['exclusions']:
        if item not in unique:unique.append(item)
    facts['enumeration']['exclusions']=unique
    return facts
