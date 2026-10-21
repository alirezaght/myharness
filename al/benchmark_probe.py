"""Source-copied, value-free pytest probe; never supplies target runtime imports."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time

COUNTS=Counter()
STAGES={}
ERRORS=[]
START=time.monotonic()
VERBS={'rev-parse','merge-base','ls-files','ls-tree','cat-file','diff','status','show','config','add','commit','init','update-index','write-tree','read-tree','checkout','worktree','submodule'}
SITES={'al_scope','al_gitlinks','al_repository_facts','al_git_cache','al_ledger','al_checkouts','al_ci_inventory','al_gate_currency','al_state','conftest'}


def kind(argv):
    if not isinstance(argv,(tuple,list)) or not argv:return 'other'
    executable=Path(os.fsdecode(argv[0])).name
    if executable=='git':
        index=1
        while index<len(argv):
            value=argv[index]
            if value in {'-c','-C','--git-dir','--work-tree'}:index+=2;continue
            if isinstance(value,str) and value.startswith('-'):index+=1;continue
            return 'git:'+value if value in VERBS else 'git:other'
        return 'git:other'
    if executable.startswith('python'):return 'python'
    return executable if executable in {'glab','make','uv','node','sh','bash','zsh','ps'} else 'other'


def pytest_configure(config):
    def audit(event,args):
        if event!='subprocess.Popen':return
        frame=sys._getframe(1);site='other'
        for _ in range(16):
            if frame is None:break
            module=frame.f_globals.get('__name__')
            if module in SITES:site=module;break
            frame=frame.f_back
        COUNTS[kind(args[1])+':'+site]+=1
    sys.addaudithook(audit)


def pytest_runtest_logreport(report):
    STAGES.setdefault(report.nodeid,{})[report.when]={'outcome':report.outcome,'seconds':report.duration,
                                                  'xfail':bool(getattr(report,'wasxfail',False))}


def pytest_collectreport(report):
    if report.failed:ERRORS.append('collection failed')


def pytest_deselected(items):
    if items:ERRORS.append('unexpected deselection')


def pytest_sessionfinish(session,exitstatus):
    root=Path(os.environ['AL_BENCHMARK_RUNTIME_ROOT']).resolve()
    origins={}
    for name,module in sorted(sys.modules.items()):
        if not name.startswith('al_'):continue
        path=getattr(module,'__file__',None)
        if not path:ERRORS.append('runtime module origin missing');continue
        path=Path(path).resolve()
        if root not in path.parents:ERRORS.append('runtime module outside target: '+name);continue
        origins[name]={'relative':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    if not origins:ERRORS.append('no target runtime modules observed')
    if ERRORS:session.exitstatus=1
    value={'schema_version':1,'exitstatus':int(session.exitstatus),'errors':ERRORS,'stages':STAGES,
           'elapsed_seconds':time.monotonic()-START,'runtime_origins':origins,
           'subprocess_kinds_and_sites':dict(sorted(COUNTS.items()))}
    profile=sys.modules.get('al_profile')
    value['runtime_spans']=profile.report() if profile else {'available':False}
    path=Path(os.environ['AL_BENCHMARK_REPORT']);temporary=path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
