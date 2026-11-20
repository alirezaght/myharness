#!/usr/bin/env python3
"""Compare one identical controlled PASSing workload in isolated source copies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from al.verification_profile import capture_source_binding,read_json,write_json,digest,ROOTS,EXCLUDED
from al.verification_plan import (environment, allocate_verification_storage,
                                  record_verification_storage, finalize_verification_storage)
from al.verification_process import execute
from al.installer.source_materialization import copy2


def validate_baseline(root,path,expected_sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:raise ValueError('baseline file receipt digest changed')
    value=read_json(path)
    if Path(value['baseline']).resolve()!=Path(root).resolve():raise ValueError('baseline receipt root mismatch')
    for row in value['files']:
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or row['type']!='file':raise ValueError('unsupported baseline receipt member')
        target=Path(root)/relative
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256'] or target.stat().st_mode&0o777!=row['mode']:
            raise ValueError('preserved baseline changed: '+str(relative))
    expected={row['path']:{'sha256':row['sha256'],'mode':row['mode']} for row in value['files']
              if any(row['path']==prefix or row['path'].startswith(prefix+'/') for prefix in ROOTS)
              and not any(part in EXCLUDED for part in Path(row['path']).parts)
              and not row['path'].endswith(('.pyc','.pyo'))}
    observed=capture_source_binding(root)['files']
    if observed!=expected:raise ValueError('preserved baseline relevant membership changed')
    return {'path':str(Path(path).resolve()),'sha256':expected_sha,'members':len(value['files'])}


def copy_bound(root,binding,destination,*,omit=()):
    for relative,value in binding['files'].items():
        if relative in omit:continue
        source=Path(root)/relative;target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        copy2(source,target)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=value['sha256'] or target.stat().st_mode&0o777!=value['mode']:
            raise ValueError('benchmark copy does not match bound source')


def run_cohort(*, root, original_binding, candidate, common, directory, instrument, suite, nodes, label):
    """Disposable sources/homes belong to one allocation; receipts remain durable."""
    owned=directory/label/'execution';owned.mkdir(parents=True,mode=0o700)
    assignment='benchmark:'+label+':'+digest(nodes)
    manager,binding=allocate_verification_storage(owned,assignment)
    scratch=Path(binding['allocation']['path'])
    try:
        source=scratch/'source';source.mkdir()
        copy_bound(root,original_binding,source,omit=common)
        copy_bound(candidate,{'files':common},source)
        staged=capture_source_binding(source)
        source_path=owned/'source-bindings.json'
        write_json(source_path,{'original':original_binding,'staged':staged,'common_fixture_overlay':common})
        result_path=owned/'probe.json';log=owned/'output.log'
        env=environment(source,scratch/'environment',profile=True)
        env['PYTHONPATH']=os.pathsep.join([str(instrument),str(source)])
        env.update(AL_BENCHMARK_RUNTIME_ROOT=str(source/'al'/suite/'skills/al/scripts'),AL_BENCHMARK_REPORT=str(result_path))
        cwd=source/'al'/suite/'skills/al'
        argv=[sys.executable,'-m','pytest','-q','--rootdir',str(cwd),'-o','addopts=','-p','no:cacheprovider','-p','_al_benchmark_probe','--basetemp',str(scratch/'pytest-tmp'),*nodes]
        before=time.monotonic()
        with log.open('wb') as output:
            owner=execute(argv,cwd=cwd,env=env,output=output,directory=owned,assignment=assignment,timeout=180)
        value=read_json(result_path)
        expected=set(nodes);actual=set(value['stages'])
        passed={node for node,stages in value['stages'].items() if set(stages)=={'setup','call','teardown'} and all(v['outcome']=='passed' and not v['xfail'] for v in stages.values())}
        if capture_source_binding(source)!=staged:raise ValueError('benchmark source moved')
        for name,origin in value['runtime_origins'].items():
            relative='al/'+suite+'/skills/al/scripts/'+origin['relative']
            if original_binding['files'].get(relative,{}).get('sha256')!=origin['sha256']:raise ValueError('runtime implementation drift/fallback: '+name)
        success=owner.process.returncode==0 and not value['errors'] and actual==expected==passed
        publication=record_verification_storage(owned,manager,binding,owner,success=success,
            complete=owner.last_cleanup.get('complete') is True,
            artifacts=[result_path,log,source_path,owned/'process-journal.json'])
        run={'status':'PASS' if success else 'FAIL','elapsed_seconds':time.monotonic()-before,
             'source_binding':original_binding,'staged_binding':staged,'probe':value,
             'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),
             'probe_path':str(result_path),'probe_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),
             'ownership':owner.record(),'storage_result':publication,'storage_binding':binding}
        return run,manager
    except BaseException:
        manager.retain(binding['allocation']['allocation_id'],reason='benchmark_execution_incomplete')
        raise


def compare(*,baseline,candidate,suite,nodes,report,baseline_files,baseline_files_sha256):
    started=time.monotonic();baseline=Path(baseline).resolve();candidate=Path(candidate).resolve();report=Path(report).resolve()
    if suite not in {'codex','claude'} or not nodes or len(nodes)>12 or len(set(nodes))!=len(nodes):raise ValueError('bounded explicit benchmark nodes required')
    if any(not n.startswith('tests/') or '..' in n.split('::',1)[0].split('/') for n in nodes):raise ValueError('unsafe benchmark node')
    if report.exists() or any(root==report or root in report.parents for root in (candidate,baseline)):
        raise ValueError('benchmark needs a new external report path')
    receipt=validate_baseline(baseline,baseline_files,baseline_files_sha256)
    bindings={label:capture_source_binding(root) for label,root in [('baseline',baseline),('candidate',candidate)]}
    directory=Path(tempfile.mkdtemp(prefix='al-controlled-benchmark-',dir=report.parent))
    instrument=directory/'instrument';instrument.mkdir(mode=0o700)
    probe=Path(__file__).with_name('benchmark_probe.py');copy2(probe,instrument/'_al_benchmark_probe.py')
    fixture_prefix='al/'+suite+'/skills/al/tests/'
    common={p:v for p,v in bindings['candidate']['files'].items() if p.startswith(fixture_prefix)}
    if not common:raise ValueError('missing controlled fixtures')
    runs={};managers={}
    try:
        for label,root in [('baseline',baseline),('candidate',candidate)]:
            runs[label],managers[label]=run_cohort(root=root,original_binding=bindings[label],
                candidate=candidate,common=common,directory=directory,instrument=instrument,suite=suite,nodes=nodes,label=label)
        validate_baseline(baseline,baseline_files,baseline_files_sha256)
        if any(capture_source_binding(root)!=bindings[label] for label,root in [('baseline',baseline),('candidate',candidate)]):
            raise ValueError('original benchmark source moved')
    except BaseException:
        for label,manager in managers.items():
            manager.retain(runs[label]['storage_binding']['allocation']['allocation_id'],reason='benchmark_comparison_incomplete')
        raise
    cleanup_path=directory/'storage-cleanup.json'
    result={'schema_version':1,'kind':'controlled-implementation-cohort','status':'PASS' if all(r['status']=='PASS' for r in runs.values()) else 'FAIL',
            'baseline_receipt':receipt,'suite':suite,'nodes':nodes,'common_fixture_overlay':common,
            'instrumentation':{'sha256':hashlib.sha256(probe.read_bytes()).hexdigest(),'origin':str(probe)},
            'runs':runs,'elapsed_seconds':time.monotonic()-started,'storage_cleanup_path':str(cleanup_path),
            'conditions':'same explicit node IDs and current fixture/helper bytes; distinct original runtime implementations, private copies/homes/indexes; sequential execution; process-local caches start cold; no all-suite timing claim'}
    write_json(report,result)
    # Finalization binds this immutable durable report; its bytes stay unchanged.
    cleanup=finalize_verification_storage(report,references=[run['storage_result'] for run in runs.values()])
    write_json(cleanup_path,cleanup)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','candidate','report','baseline-files','baseline-files-sha256'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--suite',choices=['codex','claude'],required=True);parser.add_argument('--node',action='append',required=True)
    args=parser.parse_args()
    result=compare(baseline=args.baseline,candidate=args.candidate,suite=args.suite,nodes=args.node,report=args.report,
                   baseline_files=args.baseline_files,baseline_files_sha256=args.baseline_files_sha256)
    print(json.dumps({'status':result['status'],'report':args.report,'runs':{k:{'seconds':v['elapsed_seconds'],'status':v['status']} for k,v in result['runs'].items()}},sort_keys=True))
    outcomes=[stages for run in result['runs'].values() for stages in run['probe']['stages'].values()]
    passed=sum(set(s)=={'setup','call','teardown'} and all(v['outcome']=='passed' and not v['xfail'] for v in s.values()) for s in outcomes)
    failed=sum(any(v['outcome']=='failed' for v in s.values()) for s in outcomes)
    print(str(passed)+' passed, '+str(failed)+' failed')
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
