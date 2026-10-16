#!/usr/bin/env python3
"""Public-command workflow simulations, with honest baseline comparisons."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import al_replay_policy as P


def invoke(root, argv, env):
    return subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=120)


def resolve(value, state):
    if isinstance(value,dict) and set(value)=={'$state'}:
        current=state
        for part in value['$state'].split('.'):
            current=current[int(part)] if isinstance(current,list) else current[part]
        return current
    if isinstance(value,dict):return {k:resolve(v,state) for k,v in value.items()}
    if isinstance(value,list):return [resolve(v,state) for v in value]
    return sys.executable if value=='$python' else value


def write_fixture(root,path,value):
    root=Path(root).resolve()
    target=(root/path).resolve()
    if root not in target.parents:raise ValueError('unsafe replay fixture path')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(value if isinstance(value,str) else json.dumps(value))


def run_case(package, case):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='al-replay-') as tmp:
        root = Path(tmp).resolve(); runtime = root / 'runtime'
        env = dict(os.environ, AL_RUNTIME_DIR=str(runtime), **{"CL"+"AUDE_PROJECT_DIR":str(root)})
        for argv in (['git','init','-q'], ['git','config','user.email','replay@example.invalid'],
                     ['git','config','user.name','AL Replay']):
            invoke(root, argv, env).check_returncode()
        (root/'input.txt').write_text('replay input\n')
        invoke(root,['git','add','input.txt'],env).check_returncode()
        invoke(root,['git','commit','-qm','fixture'],env).check_returncode()
        for path, value in case.get('files',{}).items():
            write_fixture(root,path,value)
        outcomes=[]; unavailable=False
        for action in case['actions']:
            before=(runtime/'replay.json').read_bytes() if (runtime/'replay.json').is_file() else None
            state=json.loads(before) if before else {}
            try:
                for item in action.get('writes',[]):write_fixture(root,item['path'],resolve(item['content'],state))
                arguments=resolve(action['argv'],state)
            except (KeyError,IndexError,TypeError,ValueError,AttributeError) as exc:
                unavailable=True
                outcomes.append({'kind':'action','argv':[],'matched':False,'unsupported':True,'reason':'action reference unavailable: '+repr(action.get('argv'))+' ('+str(exc)+')'})
                continue
            if not isinstance(arguments,list) or any(not isinstance(x,str) for x in arguments):
                unavailable=True
                outcomes.append({'kind':'action','argv':[],'matched':False,'unsupported':True,'reason':'action argv must resolve to a list of strings: '+repr(action.get('argv'))})
                continue
            arguments=[str(root/x[6:]) if x.startswith('$file:') else x for x in arguments]
            argv=[sys.executable,str(package/'scripts/al_state.py'),'--session','replay',*arguments]
            try:result=invoke(root,argv,env)
            except (OSError,subprocess.TimeoutExpired) as exc:
                unavailable=True
                outcomes.append({'kind':'action','argv':arguments,'matched':False,'unsupported':True,'error':str(exc)})
                continue
            output=result.stdout+result.stderr
            unsupported='invalid choice:' in output or 'unrecognized arguments:' in output
            unavailable |= unsupported
            expected=action.get('expected','pass')
            passed=((result.returncode==0) if expected=='pass' else (result.returncode!=0)) and not unsupported
            if action.get('unchanged'):
                after=(runtime/'replay.json').read_bytes() if (runtime/'replay.json').is_file() else None
                passed &= before==after
            if action.get('contains'): passed &= action['contains'] in output
            outcomes.append({'kind':'action','argv':arguments,'exit':result.returncode,'expected':expected,'matched':passed,'unsupported':unsupported,'output':output})
        outcomes.extend(P.evaluate(package,case,root,env))
        unavailable |= any(x['unsupported'] for x in outcomes)
        st=json.loads((runtime/'replay.json').read_text()) if (runtime/'replay.json').is_file() else {}
        return {'id':case['id'],'provenance':case['provenance'],'simulation':True,'outcomes':outcomes,
                'matched':all(x['matched'] for x in outcomes),'unavailable':unavailable,'counts':P.counts(outcomes),
                'phase_count':len(st.get('phase_occurrences',st.get('phase_timings',{}))),
                'handoffs':st.get('handoffs',0),'elapsed_seconds':time.monotonic()-started}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--fixtures',required=True); ap.add_argument('--candidate',required=True)
    ap.add_argument('--baseline'); ap.add_argument('--output',required=True); a=ap.parse_args()
    paths=sorted(Path(a.fixtures).glob('*.json')) if Path(a.fixtures).is_dir() else [Path(a.fixtures)]
    cases=[]
    for path in paths:
        data=json.loads(path.read_text()); cases.extend(data if isinstance(data,list) else [data])
    if not cases: ap.error('empty replay fixture set')
    results=[]
    for case in cases:
        if not case.get('id') or not case.get('provenance') or not case.get('actions'): ap.error('fixture requires id/provenance/actions')
        candidate=run_case(Path(a.candidate).resolve(),case)
        baseline=run_case(Path(a.baseline).resolve(),case) if a.baseline else None
        results.append({'candidate':candidate,'baseline':baseline,'baseline_comparison':'unavailable' if not baseline or baseline['unavailable'] else 'available',
                        'expected_baseline_failure':case.get('expected_baseline_failure',False),
                        'safety_regression':not candidate['matched']})
    report={'schema_version':1,'simulation':True,'cases':results,'safety_regressions':sum(x['safety_regression'] for x in results)}
    Path(a.output).write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))
    return bool(report['safety_regressions'])

if __name__=='__main__': sys.exit(main())
