"""Bounded literal GitHub quality jobs mapped to complete local verification."""
import hashlib
import re
import shlex
import tomllib
from pathlib import Path
from al_ci_yaml import load, Unsupported
import al_ci_rules as Rules
import al_ci_verification as Verification
import al_pytest_profile as Pytest

ENV=['HOME','PATH','PYTHONHOME','PYTHONPATH','UV_CACHE_DIR','UV_CONFIG_FILE','UV_NO_CONFIG',
     'UV_OFFLINE','UV_PROJECT_ENVIRONMENT','UV_PYTHON','UV_PYTHON_INSTALL_DIR','UV_PYTHON_DOWNLOADS','XDG_CONFIG_HOME',*Pytest.ENV]


def read(root,path):
    target=root/path
    if any(p.is_symlink() for p in [target,*target.parents]) or not target.is_file():raise Unsupported('unsafe/unavailable GitHub input '+path)
    before=target.stat();raw=target.read_bytes();after=target.stat()
    if len(raw)>2*1024*1024 or (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise Unsupported('changing/oversized GitHub input '+path)
    return raw,{'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'job':None}


def applicability(value,context):
    events={v:None for v in value} if isinstance(value,list) else {value:None} if isinstance(value,str) else value
    if not isinstance(events,dict) or not events:raise Unsupported('literal workflow trigger required')
    for name,filters in events.items():
        if name not in {'push','pull_request','workflow_dispatch'}:raise Unsupported('unsupported workflow event '+str(name))
        if filters is not None:
            if not isinstance(filters,dict) or set(filters)-({'branches','tags'} if name=='push' else {'branches'}):raise Unsupported('unsupported workflow event filtering')
            if any(not isinstance(v,list) or any(not isinstance(x,str) or '$' in x for x in v) for v in filters.values()):raise Unsupported('nonliteral workflow filters')
    quality='pull_request' in events or ('push' in events and not (events['push'] or {}).get('tags'))
    if not quality:
        if context=='merge_request_quality':return None
        raise Unsupported('release/manual workflow needs explicit quality planning context')
    return {'profile':context,'events':events,'selection':'conservative quality-event union'}


def execution(root,path,name,job,workflow_source,context):
    if not isinstance(job,dict) or set(job)-{'name','runs-on','steps'} or job.get('runs-on')!='ubuntu-latest':raise Unsupported('unsupported GitHub job environment/condition')
    steps=job.get('steps')
    if not isinstance(steps,list) or not steps or len(steps)>128:raise Unsupported('bounded literal steps required')
    dependencies={path:workflow_source};setup=[];requirements=[];records=[];lines=[];checkout=False;uv=False;python=False;synced=False
    def dependency(p):
        raw,ref=read(root,p);dependencies[p]=ref;return raw
    for index,step in enumerate(steps):
        selector='jobs.'+name+'.steps['+str(index)+']'
        if not isinstance(step,dict) or set(step)-{'name','uses','with','run'} or ('uses' in step)==('run' in step):raise Unsupported('unsupported/conditional GitHub step')
        if 'uses' in step:
            action=step['uses'];options=step.get('with',{})
            if not isinstance(options,dict):raise Unsupported('literal action inputs required')
            if action=='actions/checkout@v4' and not checkout and not lines:
                if set(options)-{'fetch-depth'} or 'fetch-depth' in options and options['fetch-depth'] not in {'0',0}:raise Unsupported('unsupported checkout inputs')
                checkout=True;kind='checkout'
            elif action=='astral-sh/setup-uv@v6' and checkout and not uv and not lines:
                if set(options)-{'enable-cache'} or 'enable-cache' in options and type(options['enable-cache']) is not bool:raise Unsupported('unsupported uv setup inputs')
                uv=True;kind='uv-tool-setup'
            else:raise Unsupported('unsupported action/setup order: '+str(action))
            setup.append({'selector':selector,'kind':kind,'literal_definition':step,'source_sha256':workflow_source['sha256'],'dependencies':[path]})
            continue
        if 'with' in step or not checkout or not uv:raise Unsupported('run step requires supported checkout/uv setup')
        text=step['run']
        if not isinstance(text,str) or not text or any(t in text for t in ('$','`',';','&&','||','|','>','<','\\')):raise Unsupported('unsupported shell expansion/control flow')
        for line in text.splitlines():
            tokens=shlex.split(line)
            if tokens==['uv','python','install'] and not python and not synced:
                project=tomllib.loads(dependency('pyproject.toml').decode())
                requirement=project.get('project',{}).get('requires-python')
                if not isinstance(requirement,str) or not re.fullmatch(r'(?:>=|==|~=)\d+\.\d+(?:\.\d+)?',requirement):raise Unsupported('unsupported Python requirement')
                inputs=['pyproject.toml']
                if (root/'.python-version').exists():
                    version=dependency('.python-version').decode().strip()
                    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?',version):raise Unsupported('unsupported Python version declaration')
                    from al_repo_python import compatible
                    if not compatible('python'+requirement,'python',version):raise Unsupported('Python version contradicts project requirement')
                    inputs.append('.python-version')
                python=True;kind='python-install'
            elif tokens==['uv','sync','--locked'] and python and not synced:
                dependency('pyproject.toml');dependency('uv.lock');inputs=['pyproject.toml','uv.lock'];synced=True;kind='locked-sync'
            else:
                pytest_args=Verification.pytest_arguments(tokens)
                profile=Pytest.profile(root,pytest_args,dependency) if synced and pytest_args is not None else None
                checks=Verification.observed(tokens,pytest_profile=profile)
                if not synced or not checks:raise Unsupported('unsupported/missing verification command or prerequisite')
                inputs=['pyproject.toml','uv.lock'];pytest_args=Verification.pytest_arguments(tokens);extra={}
                mapped=line
                if pytest_args is not None:
                    checks=Verification.observed(tokens,pytest_profile=profile)
                    if tokens[:2]!=['uv','run']:raise Unsupported('pytest profile requires the sourced uv execution environment')
                    uv_options=[]
                    for token in tokens[2:]:
                        if token not in {'--frozen','--locked','--no-sync'}:break
                        uv_options.append(token)
                    guarded,resources=Pytest.execution(pytest_args,uv_options);mapped=shlex.join(guarded)
                    inputs=profile['source_dependencies']
                    extra={'pytest_profile':profile,'guarded_argv':guarded,'guard_resources':resources}
                records.extend(checks)
                requirements.append({'selector':selector,'purpose':Verification.purpose(tokens,checks),'argv':tokens,'cwd':'.','env_names':ENV,'targets':checks,'dependencies':inputs,**extra})
                lines.append(mapped);continue
            setup.append({'selector':selector,'kind':kind,'literal_definition':tokens,'source_sha256':workflow_source['sha256'],'dependencies':inputs});lines.append(line)
    if not records:raise Unsupported('job has no supported verification requirements')
    for p in ('.ruff.toml','ruff.toml','pytest.ini','setup.cfg','tox.ini','conftest.py'):
        if (root/p).exists():dependency(p)
    for requirement in requirements:
        if 'pytest_profile' in requirement and Pytest.profile(root,Verification.pytest_arguments(requirement['argv']),dependency)!=requirement['pytest_profile']:
            raise Unsupported('pytest profile membership/environment changed during observation')
    chain={'schema_version':1,'provider':'github','workflow':path,'job':name,'applicability':context,'setup':setup,'requirements':requirements,
           'source_chain':[dependencies[p] for p in sorted(dependencies)],'complete':True}
    return {'verification':records,'verification_chain':chain,'source_dependencies':list(dependencies.values()),
            'argv':['sh','-eu','-c','\n'.join(lines)],'cwd':'.','execution_kind':'local','env_names':ENV}


def github_inventory(root,paths,context=None,opaque=()):
    directory=root/'.github/workflows'
    files=sorted(p.relative_to(root).as_posix() for p in directory.glob('*') if p.suffix in {'.yml','.yaml'})
    if len(files)>128:raise Unsupported('GitHub workflow count limit')
    obligations=[];dependencies={}
    for path in files:
        src={'path':path,'sha256':'0'*64,'job':None}
        def append(name,status,reason,exe=None):
            obligations.append({'source_path':path,'source_sha256':src['sha256'],'job':name,'units':sorted({Rules.unit(p) for p in paths}),
                                'applicability':status,'dependencies':list(dependencies.values())+(exe.pop('source_dependencies') if exe else []),'reason':reason,'inputs':sorted(paths),'execution':exe})
        try:
            if any(path==o or path.startswith(o.rstrip('/')+'/') for o in opaque):raise Unsupported('unavailable workflow child')
            raw,src=read(root,path);dependencies[path]=src;data=load(raw.decode())
            selected=applicability(data.get('on'),context)
            if selected is None:continue
            if set(data)-{'name','on','jobs'}:raise Unsupported('unsupported workflow-level execution semantics')
            jobs=data.get('jobs')
            if not isinstance(jobs,dict) or not jobs:raise Unsupported('literal nonempty jobs required')
            for name,job in jobs.items():
                try:
                    exe=execution(root,path,name,job,src,selected)
                    exe['verification_chain']['workflow_membership']=files
                    append(name,'APPLICABLE','complete literal GitHub quality job',exe)
                except (Unsupported,ValueError,TypeError) as exc:append(name,'UNRESOLVED',str(exc))
        except (Unsupported,ValueError,UnicodeError,TypeError) as exc:append('<unresolved-workflow>','UNRESOLVED',str(exc))
    after=sorted(p.relative_to(root).as_posix() for p in directory.glob('*') if p.suffix in {'.yml','.yaml'})
    if after!=files:raise Unsupported('GitHub workflow membership changed')
    dependencies.update({d['path']:d for o in obligations for d in o['dependencies']})
    for p,ref in dependencies.items():
        if read(root,p)[1]!=ref:raise Unsupported('GitHub workflow changed during observation')
    # Every applicable job depends on complete workflow enumeration, including excluded workflows.
    for obligation in obligations:
        obligation['dependencies']=[{**d} for d in {d['path']:d for d in [*dependencies.values(),*obligation['dependencies']]}.values()]
    return {'obligations':obligations,'dependencies':list(dependencies.values())}
