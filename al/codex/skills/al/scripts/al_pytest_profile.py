"""Closed root pytest selection/configuration, independent of application coverage."""
import configparser
import os
from pathlib import Path,PurePosixPath
import shlex
import tomllib
from al_ci_yaml import Unsupported

ENV=['PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTEST_DISABLE_PLUGIN_AUTOLOAD']
OPTIONS={'-q','-qq','-v','-vv','-x','--exitfirst','--strict-markers','--strict-config','--disable-warnings'}
CONFIGS=('pytest.ini','.pytest.ini','pytest.toml','.pytest.toml','setup.cfg','tox.ini','pyproject.toml')


def options(value,*,targets=False):
    if isinstance(value,str):value=shlex.split(value)
    if not isinstance(value,list) or any(not isinstance(v,str) for v in value):raise Unsupported('literal pytest options required')
    positional=[]
    for item in value:
        if item.startswith('-'):
            if item not in OPTIONS:raise Unsupported('unsupported pytest option '+item)
        elif not targets:raise Unsupported('pytest addopts cannot supply collection targets')
        else:positional.append(item)
    return positional


def literal_root(root,name):
    path=PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or any(c in name for c in '*?[]$`\\;|<>') or '::' in name):
        raise Unsupported('unsafe/unsupported pytest collection root')
    selected=root/path
    if any(p.is_symlink() for p in [selected,*selected.parents]) or not selected.exists():raise Unsupported('missing/unsafe pytest collection root '+name)
    if not selected.is_dir() and (not selected.is_file() or selected.suffix!='.py'):raise Unsupported('unsupported pytest collection file')
    return path.as_posix()


def configuration(root,read):
    files=[];table={};present=None
    for name in CONFIGS:
        if not (root/name).exists():continue
        raw=read(name);files.append(name)
        if name=='pyproject.toml':
            data=tomllib.loads(raw.decode());pytest=data.get('tool',{}).get('pytest',{})
            if not isinstance(pytest,dict) or set(pytest)-{'ini_options'}:raise Unsupported('unsupported native pytest TOML configuration')
            if 'ini_options' in pytest:present=name;table=pytest['ini_options']
        elif name in {'setup.cfg','tox.ini'}:
            parser=configparser.ConfigParser(interpolation=None);parser.read_string(raw.decode())
            if parser.has_section('tool:pytest') or parser.has_section('pytest'):raise Unsupported('alternate pytest INI configuration')
        else:raise Unsupported('alternate/competing pytest configuration')
    if not isinstance(table,dict) or set(table)-{'testpaths','addopts'}:raise Unsupported('unsupported pytest configuration keys')
    roots=table.get('testpaths',[])
    if not isinstance(roots,list) or any(not isinstance(p,str) for p in roots):raise Unsupported('literal pytest testpaths required')
    for path in roots:literal_root(root,path)
    options(table.get('addopts',[]))
    return {'path':present,'table':table,'files':files,'observed_names':list(CONFIGS)}


def profile(root,args,read):
    root=Path(root).resolve();selected=options(args,targets=True);config=configuration(root,read)
    options(os.environ.get('PYTEST_ADDOPTS',''))
    if os.environ.get('PYTEST_PLUGINS',''):raise Unsupported('unsupported explicit pytest environment plugins')
    mode=os.environ.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD','')
    if mode not in {'','1'}:raise Unsupported('unsupported pytest autoload environment')
    mandated=config['table'].get('testpaths') or ['.']
    roots=[literal_root(root,p) for p in (selected or mandated)]
    if selected and sorted(set(roots))!=sorted(set(mandated)):
        raise Unsupported('explicit pytest selection narrows or changes the mandated root suite')
    from al_pytest_sources import fixture_sources
    sources=fixture_sources(root,roots,read)
    return {'schema_version':1,'kind':'root-pytest-execution-profile','configuration':config,'collection_roots':roots,
            'application_scope':{'kind':'repository-mandated-ci-suite','roots':['.'],'claim':'required suite execution, not per-source test coverage'},
            'options':list(args),'environment_names':ENV,'plugin_mode':'disabled' if mode=='1' else 'locked-empty-entrypoints',
            'conftest':sources,'execution_guard':'required-after-locked-sync','source_dependencies':sorted(set(config['files']+sources['dependencies']+['uv.lock','pyproject.toml']))}


def execution(args,uv_options=()):
    import hashlib
    import json
    from al_runtime_resources import resource_identity,resource_bytes
    names=('al_pytest_profile.py','al_pytest_sources.py','al_ci_yaml.py','al_pytest_runner.py')
    resources={name:{'path':resource_identity(name),'sha256':hashlib.sha256(resource_bytes(name)).hexdigest()} for name in names}
    argv=['uv','run',*uv_options,'python','-I','-B',resources['al_pytest_runner.py']['path'],'--resources',json.dumps(resources,sort_keys=True,separators=(',',':')),'--',*args]
    return argv,resources
