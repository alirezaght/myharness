"""Repository-authored package checks: sourced execution and semantic proof are separate."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex

from al_repository_facts import digest,gap,safe,within
import al_package_profiles as Profiles

CONVENTIONAL={'test':'test','lint':'lint','check':'check','typecheck':'typecheck'}
ENV=['PATH','CI','npm_config_offline','npm_config_audit','npm_config_fund','NODE_OPTIONS','NODE_PATH','npm_config_script_shell','npm_config_ignore_scripts','npm_config_if_present','NPM_CONFIG_SCRIPT_SHELL','NPM_CONFIG_IGNORE_SCRIPTS','npm_config_userconfig','NPM_CONFIG_USERCONFIG','npm_config_globalconfig','NPM_CONFIG_GLOBALCONFIG']
PROFILE_KEYS={'schema_version','kind','unit','script','body_sha256','intent','manager','closure','semantic_coverage','execution','selected_by'}


def hashed(scan,path):
    if path not in scan.files:raise ValueError('required package input unavailable: '+path)
    scan.rules.add(path)
    return {'path':path,'sha256':scan.files.records[path]['sha256']}


def data(scan,path):
    text=scan.text(path,required=True,rule=True)
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('package input must be a literal object')
    return value


def _parents(unit):
    parts=PurePosixPath(unit).parts
    return ['.']+['/'.join(parts[:i]) for i in range(1,len(parts)+1)] if unit!='.' else ['.']


def joined(unit,name):return (PurePosixPath(unit)/name).as_posix()


def manager(scan,unit):
    candidates=[];inputs=[];reasons=[];manifests={}
    for parent in _parents(unit):
        path=joined(parent,'package.json')
        if path not in scan.files:continue
        value=data(scan,path);manifests[parent]=value;inputs.append(hashed(scan,path))
        locks=[(name,m) for name,m in [('package-lock.json','npm'),('pnpm-lock.yaml','pnpm')] if joined(parent,name) in scan.files]
        if len(locks)>1:reasons.append('ambiguous package-manager lockfiles: '+parent)
        for name,m in locks:candidates.append((parent,m,joined(parent,name),value))
    if len(candidates)!=1:reasons.append('one governing package lock is required')
    row={'name':None,'version':None,'cwd':None,'lock':None,'inputs':inputs,'versions':{},'members':{p:v['name'] for p,v in manifests.items() if isinstance(v.get('name'),str)},'reason':'unambiguous sourced lock and workspace membership'}
    if reasons or not candidates:return row,reasons
    parent,m,lock,manifest=candidates[0]
    declaration=manifest.get('packageManager');local=manifests.get(unit,{}).get('packageManager')
    match=re.fullmatch(r'(npm|pnpm)@(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)',declaration or '')
    if declaration and (not match or match[1]!=m):reasons.append('packageManager identity contradicts governing lock or is unsupported')
    if local and local!=declaration:reasons.append('member and workspace packageManager identities conflict')
    row.update(name=m,version=match[2] if match else None,cwd=parent,lock=lock)
    inputs.append(hashed(scan,lock))
    if m=='npm':
        value=data(scan,lock);packages=value.get('packages')
        if not isinstance(packages,dict):
            reasons.append('unsupported npm lock membership');packages={}
        if '' not in packages:reasons.append('unsupported npm lock membership')
        elif unit!=parent:
            relative=PurePosixPath(unit).relative_to(PurePosixPath(parent)).as_posix() if parent!='.' else unit
            if relative not in packages:reasons.append('member absent from governing lock')
        versions={}
        for path,entry in (packages or {}).items():
            if isinstance(entry,dict) and '/node_modules/' in '/'+path and isinstance(entry.get('version'),str):
                name=path.rsplit('node_modules/',1)[-1]
                if name in Profiles.TOOLS.values() and re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?',entry['version']):versions[name]=entry['version']
        row['versions']=versions
    else:
        from al_ci_yaml import load
        value=load(scan.text(lock,rule=True))
        if not isinstance(value,dict):raise ValueError('unsupported pnpm lock shape')
        importers=value.get('importers',{})
        if not isinstance(importers,dict):raise ValueError('unsupported pnpm importer map')
        relative='.' if parent==unit else PurePosixPath(unit).relative_to(PurePosixPath(parent)).as_posix() if parent!='.' else unit
        importer=importers.get(relative)
        if not isinstance(importer,dict):reasons.append('member absent from governing pnpm lock')
        versions={}
        for group in ('dependencies','devDependencies','optionalDependencies'):
            entries=(importer or {}).get(group,{})
            if not isinstance(entries,dict):raise ValueError('unsupported pnpm dependency map')
            for name,entry in entries.items():
                version=entry.get('version') if isinstance(entry,dict) else entry
                if name in Profiles.TOOLS.values() and isinstance(version,str) and re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?',version):versions[name]=version
        row['versions']=versions
    if parent!=unit:
        declaration=manifest.get('workspaces',[])
        if isinstance(declaration,dict):declaration=declaration.get('packages',[])
        workspace=joined(parent,'pnpm-workspace.yaml')
        if m=='pnpm':
            if workspace not in scan.files:reasons.append('workspace declaration unavailable')
            else:
                from al_ci_yaml import load
                declaration=load(scan.text(workspace,rule=True)).get('packages',[]);inputs.append(hashed(scan,workspace))
        from al_repo_python import expand_members
        members=set()
        if not isinstance(declaration,list):reasons.append('unsupported literal workspace membership')
        else:
            for pattern in declaration:
                if not isinstance(pattern,str) or pattern.startswith('!'):reasons.append('unsupported workspace selection closure');continue
                full=joined(parent,pattern)
                try:members.update((member for member in expand_members(full,scan.files) if joined(member,'package.json') in scan.files) if '*' in full else [full])
                except ValueError:reasons.append('unsupported workspace selection closure')
            if unit not in members:reasons.append('member absent from declared workspace')
            for member in sorted(members):
                member_manifest=joined(member,'package.json')
                if member_manifest in scan.files:
                    inputs.append(hashed(scan,member_manifest));member_value=data(scan,member_manifest)
                    if isinstance(member_value.get('name'),str):row['members'][member]=member_value['name']
                else:reasons.append('declared workspace member manifest unavailable')
    # Manager configuration can change lifecycle, shell, registry and workspace
    # semantics. Never read secret-bearing npmrc bytes or pretend absent support.
    for parent in _parents(unit):
        if (scan.root/joined(parent,'.npmrc')).exists():reasons.append('unsupported package-manager configuration input')
    row['inputs']=sorted({r['path']:r for r in inputs}.values(),key=lambda r:r['path'])
    return row,reasons


def closure(scripts,key):
    import al_shell
    commands=[];sources=[];reasons=[];visiting=set()
    def visit(name,depth=0):
        if depth>8 or name in visiting:reasons.append('recursive package-script closure');return
        body=scripts.get(name)
        if not isinstance(body,str) or not body.strip():reasons.append('missing or nonliteral package script');return
        visiting.add(name);sources.append({'selector':'scripts.'+name,'sha256':hashlib.sha256(body.encode()).hexdigest()})
        try:
            tokens,substitutions=al_shell.tokenize(body)
            if substitutions or any(kind=='op' and value not in {'&&','\n'} for kind,value in tokens) or any('$' in value or '`' in value for _,value in tokens):raise ValueError('unsupported package execution closure')
            nodes=al_shell.parse(body)
            authored=[[]]
            for kind,value in tokens:
                if kind=='op':
                    if authored[-1]:authored.append([])
                else:authored[-1].append(value)
            authored=[args for args in authored if args]
            if len(authored)!=len(nodes):raise ValueError('authored command boundary is not qualified')
            if not nodes or len(nodes)>32:raise ValueError('bounded package execution closure required')
            from al_ci_inventory import side_effect
            for node,args in zip(nodes,authored):
                # The guard AST normalizes executable paths. Keep that guard,
                # but never use its basename as authored semantic identity.
                exe=PurePosixPath(args[0]).name
                if node['redirects'] or node.get('environment') or side_effect(node['argv']) or exe in {'rm','mv','cp','git','eval','exec','source','cd','sh','bash','zsh','npx','curl','wget'}:
                    raise ValueError('unsupported or side-effecting package execution closure')
                if args[0] in {'npm','pnpm'}:
                    from al_package_policy import invocation,inspect
                    parsed=invocation(exe,args[1:])
                    if parsed['kind']!='script' or parsed['argv'] or inspect(parsed,'shared'):raise ValueError('unsupported nested package invocation')
                    for nested in ('pre'+parsed['script'],parsed['script'],'post'+parsed['script']):
                        if nested in scripts:visit(nested,depth+1)
                else:
                    if any(a in {'--fix','--write','--update-snapshots','--updateSnapshot','-u'} for a in args):raise ValueError('mutating package check flags')
                    if any(a.split('=',1)[0] in {'--watch','--watchAll','-w'} and not a.endswith('=false') for a in args) or exe=='vitest' and not ('run' in args or '--run' in args):
                        raise ValueError('watch or interactive package invocation')
                    commands.append(args)
        except (ValueError,al_shell.ShellError):reasons.append('unsupported, mutating, interactive or nonliteral execution closure')
        visiting.remove(name)
    # These are actual package lifecycle invocations; they are never disabled.
    for name in ('pre'+key,key,'post'+key):
        if name in scripts:visit(name)
    return {'status':'PENDING' if reasons else 'READY','sources':sources,'commands':commands,'reasons':sorted(set(reasons))}


def selected_custom(ci,unit,manager_name,member_name=None):
    selected={}
    for job in (ci or {}).get('obligations',[]):
        execution=job.get('execution') or {}
        for requirement in execution.get('verification_chain',{}).get('requirements',[]):
            args=requirement.get('argv',[])
            if not args or args[0] not in {'npm','pnpm'} or manager_name is not None and args[0]!=manager_name:continue
            tail=args[1:];cwd=requirement.get('cwd','.');selection=None;i=0;unsupported=False
            while i<len(tail) and tail[i].startswith('-'):
                option,equals,value=tail[i].partition('=');i+=1
                if option not in {'--prefix','--dir','-C','--workspace','-w','--filter','-F'}:unsupported=True;break
                if not equals:
                    if i>=len(tail):unsupported=True;break
                    value=tail[i];i+=1
                if option in {'--prefix','--dir','-C'}:cwd=PurePosixPath(value).as_posix()
                else:selection=value
            if unsupported:continue
            if selection is not None:
                if selection not in {unit,'./'+unit,member_name}:continue
                cwd=unit
            if cwd!=unit:continue
            tail=tail[i:]
            if tail[:1]==['run']:tail=tail[1:]
            if not tail or tail[0].startswith('-'):continue
            from al_package_policy import MUTATING
            if tail[0] in MUTATING:continue
            selected.setdefault(tail[0],[]).append({'path':job['source_path'],'sha256':job['source_sha256'],'selector':job['job'],'supported_invocation':len(tail)==1})
    return selected


def observe(scan,facts,unit,ci=None):
    path=unit['path'];manifest=joined(path,'package.json')
    try:
        package=data(scan,manifest);scripts=package.get('scripts',{})
        if not isinstance(scripts,dict) or len(scripts)>512:raise ValueError('unsupported package scripts map')
    except (ValueError,OSError,TypeError) as exc:
        facts['checks']['gaps'].append(gap('package-discovery-unavailable',manifest,type(exc).__name__,path,'ci'));return
    try:managed,manager_reasons=manager(scan,path)
    except (ValueError,OSError,TypeError,AttributeError):
        inputs=[hashed(scan,manifest)]
        for parent in _parents(path):
            for name in ('package.json','package-lock.json','pnpm-lock.yaml','pnpm-workspace.yaml'):
                p=joined(parent,name)
                if p in scan.files:inputs.append(hashed(scan,p))
        managed={'name':None,'version':None,'cwd':None,'lock':None,'inputs':sorted({r['path']:r for r in inputs}.values(),key=lambda r:r['path']),'versions':{},'members':{path:package['name']} if isinstance(package.get('name'),str) else {},'reason':'manager/lock input unavailable'}
        manager_reasons=['malformed or unreadable package-manager/lock input']
    custom=selected_custom(ci,path,managed['name'],managed['members'].get(path));inventory=facts['checks'].setdefault('package_scripts',[])
    qualified=[]
    for key,body in sorted(scripts.items()):
        if not isinstance(key,str) or not isinstance(body,str):
            facts['checks']['gaps'].append(gap('package-script-nonliteral',manifest,'literal script keys and bodies required',path,'ci'));continue
        if key.startswith(('pre','post')) and key[3 if key.startswith('pre') else 4:] in scripts:continue
        closed=closure(scripts,key)
        configs=[];config_paths={}
        def config_text(name):
            for parent in reversed(_parents(path)):
                p=joined(parent,name)
                if p in scan.files:
                    configs.append(hashed(scan,p));config_paths[name]=p;return scan.text(p,rule=True)
                if (scan.root/p).exists():raise ValueError('configuration bytes are not supported by capture')
            return None
        try:
            semantics=Profiles.classify(closed['commands'],path,managed['versions'],config_text)
            if managed['name']=='pnpm' and any(ref['selector']!='scripts.'+key for ref in closed['sources']):
                semantics['status']='UNVERIFIED';semantics['reasons'].append('pnpm lifecycle auto-hook execution is not qualified')
            from al_package_contracts import config as configuration
            for parent in _parents(path):
                config_manifest=joined(parent,'package.json')
                if config_manifest not in scan.files:continue
                declared=data(scan,config_manifest)
                for tool,key_name in (('eslint','eslintConfig'),('jest','jest')):
                    if key_name in declared and any(args[0]==tool for args in closed['commands']):
                        configs.append(hashed(scan,config_manifest));configuration(tool,json.dumps(declared[key_name]))
            companions={'jest':('babel.config.js','babel.config.cjs','babel.config.mjs','babel.config.ts','babel.config.cts','.babelrc','.babelrc.json','.babelrc.js','.babelrc.cjs','.babelrc.mjs'),
                        'vitest':('babel.config.js','.babelrc'), 'eslint':('.eslintignore',)}
            unsupported={'eslint':('eslint.config.ts','eslint.config.mts','eslint.config.cts','.eslintrc','.eslintrc.js','.eslintrc.cjs','.eslintrc.yaml','.eslintrc.yml'),
                         'jest':('jest.config.ts','jest.config.cts','jest.config.mts'),
                         'vitest':('vitest.config.mjs','vitest.config.cjs','vitest.config.mts','vitest.config.cts','vite.config.mjs','vite.config.cjs','vite.config.mts','vite.config.cts')}
            for command in closed['commands']:
                for name in unsupported.get(command[0],()):
                    if any((scan.root/joined(parent,name)).exists() for parent in _parents(path)):raise ValueError('unqualified automatic configuration variant')
            for command in closed['commands']:
                for name in companions.get(command[0],()):
                    if any((scan.root/joined(parent,name)).exists() for parent in _parents(path)):raise ValueError('unqualified companion configuration')
            for record in semantics['records']:record['config']=[config_paths.get(name,joined(path,name)) for name in record['config']]
            if any(record['profile']=='typescript' and any(PurePosixPath(name).parent.as_posix()!=path for name in record['config']) for record in semantics['records']):
                semantics['status']='UNVERIFIED';semantics['records']=[]
                semantics['reasons'].append('TypeScript source scope requires member-local configuration')
        except ValueError:semantics={'status':'UNVERIFIED','records':[],'kind':CONVENTIONAL.get(key,'check'),'reasons':['configuration lacks positive finite qualification']}
        configs=sorted({r['path']:r for r in configs}.values(),key=lambda r:r['path'])
        intent=CONVENTIONAL.get(key,semantics['kind'])
        execution_reasons=manager_reasons+closed['reasons']
        from al_shell import check
        if managed['name'] and check(shlex.join([managed['name'],'run',key]),'shared'):execution_reasons.append('existing package command policy refuses invocation')
        profile={'schema_version':1,'kind':'package-script','unit':path,'script':key,'body_sha256':hashlib.sha256(body.encode()).hexdigest(),'intent':intent,
                 'manager':managed,'closure':{'sources':closed['sources'],'reasons':closed['reasons'],'inputs':configs},'semantic_coverage':semantics,
                 'execution':{'status':'PENDING' if execution_reasons else 'READY','reasons':sorted(set(execution_reasons))},'selected_by':custom.get(key,[])}
        inventory.append(profile)
        if key not in CONVENTIONAL and key not in custom and semantics['status']!='SUPPORTED':continue
        identifier='package-'+digest([path,key])[:16]
        facts['checks']['obligations'].append({'id':identifier,'category':'ci','units':[path]})
        if execution_reasons:
            facts['checks']['gaps'].append(gap('package-execution-pending',manifest,'; '.join(sorted(set(execution_reasons))),path,'ci'));continue
        row=binding(profile)
        facts['checks']['bindings'].append(row)
        if semantics['status']=='SUPPORTED':qualified.append(row)
        else:facts['checks']['gaps'].append(gap('package-semantic-unverified',manifest,'execution is discoverable; '+('; '.join(semantics['reasons']) or 'semantic coverage is unverified'),path,'ci'))
    verification='verification-'+path.replace('/','-').replace('.','root')
    if not any(o['id']==verification for o in facts['checks']['obligations']):facts['checks']['obligations'].append({'id':verification,'category':'ci','units':[path]})
    # Node syntax is file-scoped. Other qualified checks describe their declared
    # member boundary; mixed-language completeness is independently retained.
    source_paths={p for p in scan.files if within(path,p) and p.endswith(('.js','.jsx','.mjs','.cjs','.ts','.tsx'))}
    covered=set()
    for row in qualified:
        for record in row['package_script']['semantic_coverage']['records']:
            if record['profile']=='node-syntax':covered.update(record['targets'])
            elif record['profile']=='typescript':
                import fnmatch
                config=json.loads(scan.text(record['config'][0],rule=True))
                options=config.get('compilerOptions',{});files=config.get('files');includes=config.get('include',['**/*']);excludes=config.get('exclude',[])
                def matches(relative,patterns):
                    return any(isinstance(pattern,str) and (fnmatch.fnmatchcase(relative,pattern) or fnmatch.fnmatchcase(relative,pattern.replace('**/','')) or relative.startswith(pattern.rstrip('/')+'/')) for pattern in patterns)
                for source in source_paths:
                    relative=PurePosixPath(source).relative_to(PurePosixPath(path)).as_posix()
                    language=source.endswith(('.ts','.tsx')) or bool(options.get('allowJs') and options.get('checkJs'))
                    selected=relative in files if isinstance(files,list) else matches(relative,includes) and not matches(relative,excludes)
                    if language and selected:covered.add(source)
            else:covered.update(p for p in source_paths if any(within(target,p) for target in record['targets']))
    if source_paths and source_paths<=covered and set(unit['languages'])=={'javascript'}:
        qualified[0]['obligation_ids'].append(verification)
        qualified[0]['execution_signature']=digest({k:v for k,v in qualified[0].items() if k!='execution_signature'})
    elif not any(g.get('unit')==path and g['code']=='missing-verification-check' for g in facts['checks']['gaps']):
        facts['checks']['gaps'].append(gap('missing-verification-check',manifest,'package scripts do not establish complete member/language verification',path,'ci'))


def binding(profile):
    path=profile['unit'];key=profile['script'];managed=profile['manager'];manifest=joined(path,'package.json')
    sources=sorted({r['path']:r for r in [*managed['inputs'],*profile['closure']['inputs'],*[{k:ref[k] for k in ('path','sha256')} for ref in profile['selected_by']]]}.values(),key=lambda r:r['path'])
    primary=next(r for r in sources if r['path']==manifest)
    chain=[{**primary,'selector':'scripts.'+key,'semantics':'repository-authored package script; not a cloud CI job'}]
    chain += [{**r,'selector':'package-script dependency','semantics':'locked package execution input'} for r in sources if r['path']!=manifest]
    identifier='package-'+digest([path,key])[:16]
    row={'id':identifier,'category':'ci','units':[path],'required':True,'argv':[managed['name'],'run',key],'cwd':path,'execution_kind':'local','env_names':ENV[:],'sources':[],
         'required_scope':sorted(set([path,*[r['path'] for r in sources]])),'source_chain':chain,'obligation_ids':[identifier],'package_script':profile}
    row['execution_signature']=digest(row)
    return row


def identifier_not_bound(profile,ids):return 'package-'+digest([profile['unit'],profile['script']])[:16] not in ids


def select_ci(facts,ci):
    """Enrich existing captured profiles, never re-read/guess a custom script body."""
    checks=facts['checks'];ids={o['id'] for o in checks['obligations']}
    for profile in checks.get('package_scripts',[]):
        selected=selected_custom(ci,profile['unit'],profile['manager']['name'],profile['manager']['members'].get(profile['unit'])).get(profile['script'])
        if not selected:continue
        profile['selected_by']=selected
        if identifier_not_bound(profile,ids) and any(not ref['supported_invocation'] for ref in selected):
            profile['execution']={'status':'PENDING','reasons':sorted(set(profile['execution']['reasons']+['original CI forwarding requires a distinct execution profile']))}
        identifier='package-'+digest([profile['unit'],profile['script']])[:16]
        for row in checks['bindings']:
            if row['id']==identifier:
                obligations=row['obligation_ids'];fresh=binding(profile);fresh['obligation_ids']=obligations
                fresh['execution_signature']=digest({k:v for k,v in fresh.items() if k!='execution_signature'});row.update(fresh)
        if identifier in ids:continue
        ids.add(identifier);checks['obligations'].append({'id':identifier,'category':'ci','units':[profile['unit']]})
        if profile['execution']['status']=='READY':checks['bindings'].append(binding(profile))
        else:checks['gaps'].append(gap('package-execution-pending',joined(profile['unit'],'package.json'),'; '.join(profile['execution']['reasons']),profile['unit'],'ci'))
        if profile['semantic_coverage']['status']!='SUPPORTED':checks['gaps'].append(gap('package-semantic-unverified',joined(profile['unit'],'package.json'),'CI-selected script execution does not establish semantic coverage',profile['unit'],'ci'))


def validate_profile(value):
    def obj(item,keys):
        if not isinstance(item,dict) or set(item)!=set(keys):raise ValueError('invalid closed package-script field set')
    def strings(items):
        if not isinstance(items,list) or len(items)>4096 or any(not isinstance(v,str) for v in items):raise ValueError('invalid package-script strings')
    def reference(ref):
        obj(ref,{'path','sha256'});safe(ref['path'])
        if not re.fullmatch('[0-9a-f]{64}',str(ref['sha256'])):raise ValueError('invalid package-script source hash')
    obj(value,PROFILE_KEYS)
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['kind']!='package-script':raise ValueError('unsupported package-script profile')
    safe(value['unit'])
    if not isinstance(value['script'],str) or not value['script'] or not re.fullmatch('[0-9a-f]{64}',str(value['body_sha256'])):raise ValueError('invalid package-script identity')
    if value['intent'] not in {'test','lint','typecheck','check'}:raise ValueError('invalid package-script intent')
    manager=value['manager'];obj(manager,{'name','version','cwd','lock','inputs','versions','members','reason'})
    if manager['name'] not in {None,'npm','pnpm'}:raise ValueError('unsupported package manager')
    for key in ('cwd','lock'):
        if manager[key] is not None:safe(manager[key])
    if manager['version'] is not None and not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',manager['version']):raise ValueError('unsupported manager version')
    if not isinstance(manager['inputs'],list) or not isinstance(manager['versions'],dict) or not isinstance(manager['members'],dict):raise ValueError('invalid package-script provenance')
    for ref in manager['inputs']:reference(ref)
    closed=value['closure'];obj(closed,{'sources','reasons','inputs'});strings(closed['reasons'])
    if not isinstance(closed['inputs'],list) or not isinstance(closed['sources'],list):raise ValueError('invalid closure sources')
    for ref in closed['inputs']:reference(ref)
    for ref in closed['sources']:
        obj(ref,{'selector','sha256'})
        if not isinstance(ref['selector'],str) or not re.fullmatch('[0-9a-f]{64}',str(ref['sha256'])):raise ValueError('invalid lifecycle source')
    semantic=value['semantic_coverage'];obj(semantic,{'status','records','kind','reasons'});strings(semantic['reasons'])
    if semantic['status'] not in {'SUPPORTED','UNVERIFIED'} or semantic['kind'] not in {'test','lint','typecheck','check'}:raise ValueError('invalid package semantics')
    if not isinstance(semantic['records'],list):raise ValueError('invalid semantic records')
    for record in semantic['records']:
        obj(record,{'profile','kind','targets','tool','version','config'});strings(record['targets']);strings(record['config'])
        if record['profile'] not in {'node-test','node-syntax','typescript','eslint','vitest','jest'}:raise ValueError('unsupported package semantic profile')
        for path in record['targets']:safe(path)
    execution=value['execution'];obj(execution,{'status','reasons'});strings(execution['reasons'])
    if execution['status'] not in {'READY','PENDING'}:raise ValueError('invalid package execution status')
    if not isinstance(value['selected_by'],list):raise ValueError('invalid package selection sources')
    for ref in value['selected_by']:
        obj(ref,{'path','sha256','selector','supported_invocation'});reference({k:ref[k] for k in ('path','sha256')})
        if type(ref['supported_invocation']) is not bool:raise ValueError('invalid package selection invocation')
    return value


def runtime_tools(root,profile):
    from pathlib import Path
    import os,shutil
    from al_check_setup import executable
    from al_package_contracts import BINARIES
    root=Path(root);base=os.environ.get('PATH','');cwd=root/profile['unit']
    manager_identity=executable(profile['manager']['name'])
    # npm 11 run-script prepends every ancestor, including those above the
    # checkout, then its own node-gyp wire. Observe that exact ordering.
    ancestry=[cwd,*cwd.parents]
    wires=[str(parent/'node_modules/.bin') for parent in ancestry]
    if profile['manager']['name']=='npm':
        npm=Path(manager_identity['path']).parent.parent
        wires.append(str(npm/'node_modules/@npmcli/run-script/lib/node-gyp-bin'))
    search=os.pathsep.join(wires+[base])
    host=executable('node');selected=shutil.which('node',path=search)
    if not selected:raise ValueError('package Node runtime is unavailable')
    node=executable(selected);node['requested']='node'
    result={'node':node,'manager':manager_identity,'qualification_reasons':[]}
    if profile['manager']['name']!='npm':result['qualification_reasons'].append('manager script PATH contract is unqualified')
    script_manager=shutil.which(profile['manager']['name'],path=search)
    if not script_manager or executable(script_manager)['path']!=result['manager']['path']:result['qualification_reasons'].append('unqualified package-local manager shadow')
    if node['path']!=host['path'] or node['sha256']!=host['sha256']:result['qualification_reasons'].append('unqualified package-local Node shadow')
    for record in profile['semantic_coverage']['records']:
        name=record['tool']
        if name=='node':continue
        binary_name,wire=BINARIES[name];selected=shutil.which(binary_name,path=search)
        if not selected:
            result['qualification_reasons'].append('local framework executable unavailable: '+name);continue
        binary=Path(selected).resolve(strict=True);package=None
        for parent in reversed(_parents(profile['unit'])):
            candidate=root/parent/'node_modules'/name/'package.json'
            if candidate.is_file():package=candidate;break
        if package is None or root not in package.resolve().parents:
            result['qualification_reasons'].append('framework would borrow ambient/global tooling: '+name);continue
        value=json.loads(package.read_text());declared=value.get('bin',{})
        relative=declared.get(binary_name) if isinstance(declared,dict) else declared if isinstance(declared,str) else None
        if value.get('version')!=record['version'] or relative!=wire or binary!=(package.parent/wire).resolve(strict=True) or root not in binary.parents:
            result['qualification_reasons'].append('framework executable/version wire is unqualified: '+name);continue
        result[name]={'manifest_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),'version':value['version'],'executable':executable(str(binary))}
    return result


def result_gaps(profile,entry):
    result=entry.get('package_script_result')
    if not isinstance(result,dict) or result.get('schema_version')!=1 or result.get('profile_sha256')!=digest(profile):return ['package-script result binding missing or changed']
    try:
        from pathlib import Path
        packets=[json.loads(line[len('AL PACKAGE RESULT '):]) for line in Path(entry['log']).read_text().splitlines() if line.startswith('AL PACKAGE RESULT ')]
        if len(packets)!=1 or packets[0]!=result:return ['package result differs from retained runtime metadata']
        from al_check_setup import executable
        host=executable('node');host['requested']='node'
        tools=entry.get('package_runtime_tools',{})
        if tools.get('node')!=host or tools.get('manager')!=executable(profile['manager']['name']) or tools.get('qualification_reasons'):return ['package runtime changed or is unqualified']
    except (OSError,ValueError,KeyError):return ['package result evidence unavailable']
    if result.get('semantic_verdict')!='VERIFIED':return ['package semantic result is '+str(result.get('semantic_verdict'))]
    if result.get('execution')!='PASS' or result.get('exit')!=0 or not result.get('output',{}).get('complete'):return ['package execution did not produce complete evidence']
    return []


def runtime_environment():
    import os
    from al_check_setup import SetupError
    allowed={'PATH','CI','npm_config_offline','npm_config_audit','npm_config_fund','npm_config_cache','npm_config_userconfig','NPM_CONFIG_USERCONFIG','npm_config_globalconfig','NPM_CONFIG_GLOBALCONFIG'}
    if any(name.lower()=='path' and name!='PATH' and os.environ.get(name) for name in os.environ):raise SetupError('noncanonical script PATH environment requires qualification')
    if any(name.lower().startswith('npm_config_') and name not in allowed and os.environ.get(name) for name in os.environ):
        raise SetupError('unsupported ambient npm configuration input')
    if any(os.environ.get(name) for name in ENV if name not in allowed):
        raise SetupError('unsupported ambient package or Node execution control')
    if os.environ.get('npm_config_offline') not in {None,'','true','1'} or any(os.environ.get(name) not in {None,'','false','0'} for name in ('npm_config_audit','npm_config_fund')):
        raise SetupError('unsupported package setup controls')


def observed_result(profile,code,raw,metadata):
    result=Profiles.result(profile,code,raw.decode('utf-8',errors='replace'))
    result['output']=metadata
    if not metadata['complete']:
        result['semantic_verdict']='UNVERIFIED';result['reasons']=['package output or time budget exceeded']
    return result


def prepare_setup(root,profile):
    """Match actual materialized bytes before any package installation is started."""
    from pathlib import Path
    from al_check_setup import plan,SetupError
    validate_profile(profile);root=Path(root)
    for ref in [*profile['manager']['inputs'],*profile['closure']['inputs']]:
        path=root/ref['path']
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:
            raise SetupError('package execution inputs differ in materialized source; independent base/current profile required')
    for parent in _parents(profile['unit']):
        if (root/parent/'.npmrc').exists():raise SetupError('unsupported materialized package-manager configuration')
    recipe=plan(root,profile['unit'],package_root=profile['manager']['cwd'])
    if not recipe or recipe['manager']!=profile['manager']['name'] or recipe['cwd']!=profile['manager']['cwd']:
        raise SetupError('locked setup selection differs from sourced package profile')
    expected={ref['path']:ref['sha256'] for ref in profile['manager']['inputs']}
    if any(expected.get(ref['path'])!=ref['sha256'] for ref in recipe['inputs']):
        raise SetupError('locked setup sources differ from package profile')
    return recipe


def _configuration_metadata(path,label):
    import stat
    from pathlib import Path
    from al_check_setup import SetupError
    path=Path(path)
    try:
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode):raise SetupError('unqualified '+label+' npm configuration alias')
        null=path==Path('/dev/null')
        if not null and (not stat.S_ISREG(info.st_mode) or info.st_size):raise SetupError('nonempty or unsupported '+label+' npm configuration requires qualification')
        identity=[info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns]
        return {'location':label,'exists':True,'identity_sha256':digest(identity)}
    except FileNotFoundError:return {'location':label,'exists':False}
    except OSError as exc:raise SetupError('npm configuration metadata unavailable') from exc


def preflight(root,profile):
    """Metadata/data inspection only; must precede every package-related spawn."""
    import os,shutil
    from pathlib import Path
    from al_check_setup import SetupError
    root=Path(root);runtime_environment();validate_profile(profile)
    for ref in profile['manager']['inputs']:
        path=root/ref['path']
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise SetupError('package preflight source inputs changed')
    environment=os.environ
    def chosen(lower,upper,default):
        a=environment.get(lower);b=environment.get(upper)
        if a and b and a!=b:raise SetupError('ambiguous effective npm configuration source')
        return a or b or default
    paths=[]
    paths.append((chosen('npm_config_userconfig','NPM_CONFIG_USERCONFIG',str(Path.home()/'.npmrc')),'user'))
    explicit=chosen('npm_config_globalconfig','NPM_CONFIG_GLOBALCONFIG',None)
    if explicit:paths.append((explicit,'global'))
    else:
        for tool in ('node',profile['manager']['name']):
            selected=shutil.which(tool)
            if selected:paths.append((str(Path(selected).parent.parent/'etc/npmrc'),'global'))
        paths.append(('/etc/npmrc','system'))
    for parent in _parents(profile['unit']):
        paths.append((str(root/parent/'.npmrc'),'project'))
        if any((root/parent/name).exists() for name in ('.pnpmfile.cjs','pnpmfile.cjs')):raise SetupError('unqualified package-manager executable configuration')
    manager_executable=shutil.which(profile['manager']['name'])
    if manager_executable:
        resolved=Path(manager_executable).resolve(strict=True)
        for parent in resolved.parents:
            if parent.name in {'npm','pnpm'}:paths.append((str(parent/'npmrc'),'builtin'));break
    observations=[];seen=set()
    for path,label in paths:
        if path in seen:continue
        seen.add(path);observations.append((path,label,_configuration_metadata(path,label)))
    # Lock-format facts make unknown dependency install hooks explicit; no
    # fetching/inspection of third-party source or hidden hook disabling occurs.
    lock=root/profile['manager']['lock']
    if profile['manager']['name']=='npm':
        value=json.loads(lock.read_text())
        if value.get('lockfileVersion') not in {2,3}:raise SetupError('unqualified npm lock-format lifecycle contract')
        for name,entry in value.get('packages',{}).items():
            if 'node_modules/' in name and isinstance(entry,dict) and entry.get('hasInstallScript'):
                raise SetupError('unqualified dependency install lifecycle')
    else:
        from al_ci_yaml import load
        value=load(lock.read_text())
        if str(value.get('lockfileVersion')) not in {'9.0','9'}:raise SetupError('unqualified pnpm lock-format lifecycle contract')
        workspace=root/profile['manager']['cwd']/'pnpm-workspace.yaml'
        if workspace.exists():
            configuration=load(workspace.read_text())
            if not isinstance(configuration,dict) or set(configuration)-{'packages'}:raise SetupError('unqualified pnpm workspace execution configuration')
        if any(isinstance(row,dict) and row.get('requiresBuild') for row in value.get('packages',{}).values()):raise SetupError('unqualified dependency build lifecycle')
    lifecycle=[]
    for ref in profile['manager']['inputs']:
        if not ref['path'].endswith('package.json'):continue
        value=json.loads((root/ref['path']).read_text());scripts=value.get('scripts',{})
        if (root/ref['path']).parent.joinpath('binding.gyp').exists():raise SetupError('unqualified automatic native-install context')
        if value.get('pnpm'):raise SetupError('unqualified package-manager manifest configuration')
        for key in ('preinstall','install','postinstall','prepublish','preprepare','prepare','postprepare'):
            if not scripts.get(key):continue
            finite=closure(scripts,key)
            for args in finite['commands']:
                intrinsic=args[0]=='echo'
                if not intrinsic:raise SetupError('unqualified authored install lifecycle')
            if finite['status']!='READY':raise SetupError('unqualified authored install lifecycle closure')
            lifecycle.append({'path':ref['path'],'selector':'scripts.'+key,'body_sha256':hashlib.sha256(scripts[key].encode()).hexdigest()})
    return {'observations':observations,'metadata':{'schema_version':1,'configuration':[row for _,_,row in observations],'lifecycle':lifecycle}}


def preflight_current(context):
    try:return all(_configuration_metadata(path,label)==row for path,label,row in context['observations'])
    except (ValueError,__import__('al_check_setup').SetupError):return False


def probe_runtime(root,profile,owner,capture,log):
    import subprocess
    from al_package_output import write_metadata,inspect_call
    from al_package_contracts import NODE_VERSIONS,MANAGER_VERSIONS
    tools=inspect_call(owner,log,'probe',runtime_tools,root,profile)
    process=owner.spawn([tools['manager']['path'],'--version'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    code,raw,metadata=inspect_call(owner,log,'runtime-version',capture.run,process,10)
    match=re.fullmatch(rb'(\d+\.\d+\.\d+)\s*',raw);version=match[1].decode() if match else None
    del raw
    tools['manager_version']=version
    if code or not metadata['complete'] or version not in MANAGER_VERSIONS.get(profile['manager']['name'],set()):tools['qualification_reasons'].append('manager version has no live-qualified lifecycle contract')
    write_metadata(log,'TOOL',{'schema_version':1,'tool':profile['manager']['name'],'version':version,'output':metadata})
    process=owner.spawn([tools['node']['path'],'--version'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    code,raw,metadata=inspect_call(owner,log,'runtime-version',capture.run,process,10)
    match=re.fullmatch(rb'v?(\d+\.\d+\.\d+)\s*',raw)
    version=match[1].decode() if match else None
    del raw
    tools['node_version']=version
    if code or not metadata['complete'] or version not in NODE_VERSIONS:tools['qualification_reasons'].append('Node version has no live-qualified parser contract')
    write_metadata(log,'TOOL',{'schema_version':1,'tool':'node','version':version,'output':metadata})
    return tools
