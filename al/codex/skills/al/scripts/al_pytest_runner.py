"""Visible post-sync pytest profile and outcome guard; never a success substitute."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import types
from collections import Counter


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def packages(root,mode):
    lock=tomllib.loads((root/'uv.lock').read_text());locked={}
    for row in lock.get('package',[]):
        if isinstance(row.get('name'),str) and isinstance(row.get('version'),str):locked.setdefault(row['name'].lower().replace('_','-'),set()).add(row['version'])
    version=importlib.metadata.version('pytest')
    if version not in locked.get('pytest',set()) or version.split('.')[0] not in {'8','9'}:raise ValueError('actual pytest is not a supported locked version')
    plugins=[]
    for distribution in importlib.metadata.distributions():
        selected=[entry for entry in distribution.entry_points if entry.group=='pytest11']
        if selected:
            name=distribution.metadata['Name'].lower().replace('_','-')
            plugins.extend({'distribution':name,'version':distribution.version,'name':e.name,'value':e.value} for e in selected)
    if mode=='locked-empty-entrypoints' and plugins:raise ValueError('unmodeled installed pytest entry-point plugins')
    return {'pytest_version':version,'entry_points':sorted(plugins,key=lambda p:(p['distribution'],p['name'],p['value'])),'mode':mode}


def builtin_plugin(plugin):
    import _pytest
    module=plugin if isinstance(plugin,types.ModuleType) else sys.modules.get(getattr(plugin if isinstance(plugin,type) else type(plugin),'__module__',''))
    if not isinstance(module,types.ModuleType) or not module.__name__.startswith('_pytest.') or sys.modules.get(module.__name__) is not module:return False
    file=getattr(module,'__file__',None)
    if not file:return False
    path=Path(file).resolve();package=Path(_pytest.__file__).resolve().parent
    if not path.is_file() or package not in path.parents:return False
    if not isinstance(plugin,types.ModuleType):
        cls=plugin if isinstance(plugin,type) else type(plugin)
        if vars(module).get(cls.__name__) is not cls:return False
    return True


class Outcomes:
    def __init__(self,root,conftests,marker=None):
        self.collected=[];self.outcomes=[];self.plugins=None;self.errors=[];self.allowed={str(root/name) for name in conftests}
        self.marker=marker;self.deselected=[]
    def pytest_collection_finish(self,session):
        self.collected=[item.nodeid for item in session.items]
        if len(set(self.collected))!=len(self.collected):self.errors.append('duplicate collected test identity')
        loaded=session.config.pluginmanager.get_plugins()
        for plugin in loaded:
            module=getattr(plugin,'__name__',type(plugin).__module__)
            file=getattr(plugin,'__file__',None)
            conftest=isinstance(plugin,types.ModuleType) and sys.modules.get(module) is plugin and file is not None and str(Path(file).resolve()) in self.allowed
            if plugin is not self and not builtin_plugin(plugin) and not conftest:
                self.errors.append('unmodeled collected pytest plugin '+module)
        self.plugins=frozenset(id(plugin) for plugin in loaded)
    def pytest_plugin_registered(self,plugin,plugin_name,manager):
        if self.plugins is not None:self.errors.append('pytest plugin registered during execution: '+plugin_name)
    def pytest_deselected(self,items):
        # Only the repository's own declared marker expression may deselect; it is recorded.
        if items and self.marker is None:self.errors.append('pytest required selection was deselected')
        self.deselected.extend(item.nodeid for item in items)
    def pytest_runtest_logreport(self,report):
        if report.when=='call' or report.failed or report.skipped:
            self.outcomes.append({'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome,'xfail':hasattr(report,'wasxfail')})
    def pytest_sessionfinish(self,session,exitstatus):
        if [item.nodeid for item in session.items]!=self.collected:self.errors.append('pytest required selection changed during execution')
        if self.plugins is not None and frozenset(id(p) for p in session.config.pluginmanager.get_plugins())!=self.plugins:
            self.errors.append('pytest plugin set changed during execution')
    def summary(self):
        passed=[r['nodeid'] for r in self.outcomes if r['when']=='call' and r['outcome']=='passed']
        calls=Counter(passed)
        if any(count!=1 for count in calls.values()):self.errors.append('duplicate successful pytest call identity')
        if any(r['nodeid'] not in self.collected for r in self.outcomes):self.errors.append('unexpected pytest outcome identity')
        failed=[r for r in self.outcomes if r['outcome']=='failed'];skipped=[r for r in self.outcomes if r['outcome']=='skipped']
        if any(r['xfail'] for r in self.outcomes):self.errors.append('pytest expected-failure outcome is not successful verification')
        return {'collected':len(self.collected),'passed':len(passed),'failed':len(failed),'skipped':len(skipped),
                'missing':sorted(set(self.collected)-set(passed)),'errors':self.errors,
                **({'marker_expression':self.marker,'deselected':sorted(self.deselected)} if self.marker is not None else {})}


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--resources',required=True);parser.add_argument('pytest_args',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv);resources=json.loads(args.resources);here=Path(__file__).resolve().parent
    names={'al_pytest_profile.py','al_pytest_sources.py','al_ci_yaml.py','al_pytest_runner.py'}
    if set(resources)!=names:raise ValueError('pytest guard resource closure differs')
    for name,row in resources.items():
        path=here/name
        if str(path)!=row['path'] or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('pytest guard resource changed')
    sys.path.insert(0,str(here))
    from al_pytest_profile import profile
    root=Path.cwd().resolve();inputs={}
    def read(name):
        path=root/name
        if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents):raise ValueError('unsafe pytest input')
        raw=path.read_bytes()
        if len(raw)>2*1024*1024:raise ValueError('oversized pytest input')
        inputs[name]=hashlib.sha256(raw).hexdigest();return raw
    selected=args.pytest_args[1:] if args.pytest_args[:1]==['--'] else args.pytest_args
    before=profile(root,selected,read);read('uv.lock');actual=packages(root,before['plugin_mode'])
    controls={name:os.environ.get(name) for name in before['environment_names']}
    import pytest
    recorder=Outcomes(root,before['conftest']['files'],before['marker_expression'])
    # A private exact config plus explicit root/cutoff prevents any external
    # ancestor configuration or conftest from selecting or changing this run.
    with tempfile.TemporaryDirectory(prefix='al-pytest-profile-') as directory:
        config=before['configuration']['path']
        if config:config=str(root/config)
        else:
            path=Path(directory)/'pytest.ini';path.write_text('[pytest]\n');config=str(path)
        from al_pytest_profile import split
        options,explicit,_=split(selected)
        result=pytest.main(['-c',config,'--rootdir',str(root),'--confcutdir',str(root),*options,*(explicit or before['collection_roots'])],plugins=[recorder])
    previous=dict(inputs);after=profile(root,selected,read);read('uv.lock')
    if after!=before or inputs!=previous or controls!={name:os.environ.get(name) for name in controls}:
        raise ValueError('pytest execution profile inputs changed')
    if packages(root,before['plugin_mode'])!=actual:raise ValueError('pytest execution plugin environment changed')
    for name,row in resources.items():
        if hashlib.sha256((here/name).read_bytes()).hexdigest()!=row['sha256']:raise ValueError('pytest guard resource moved')
    for name,sha in previous.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=sha:raise ValueError('pytest source input moved')
    summary=recorder.summary();print('AL_PYTEST_EXECUTION '+json.dumps({'schema_version':1,'profile':before,'environment':actual,'inputs':inputs,'outcomes':summary},sort_keys=True))
    if result!=0:return int(result)
    if not summary['collected'] or summary['missing'] or summary['failed'] or summary['skipped'] or summary['errors']:return 1
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,TypeError,KeyError,ImportError) as exc:
        print('AL pytest execution unresolved: '+str(exc),file=sys.stderr);raise SystemExit(2)
