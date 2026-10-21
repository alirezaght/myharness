"""Bounded owned subprocess shards with exact, fail-closed outcome reconciliation."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from al.verification_plan import environment, pytest_argv, qualified, reconcile
from al.verification_profile import digest, read_json, write_json
from al.verification_process import OwnedProcess


def partition(plan, *, workers, durations):
    workers = max(1, min(int(workers), 8, os.cpu_count() or 1))
    groups = {}
    for assignment in plan['execution_assignments']:
        groups.setdefault(assignment['group'], []).append(assignment)
    result = []
    for group, nodes in groups.items():
        suite = nodes[0]['suite']
        row = {'schema_version': 1, 'run_id': plan['run_id'], 'shard_id': uuid.uuid5(uuid.NAMESPACE_URL, plan['run_id'] + group).hex,
               'suite': suite, 'nodes': [n['node'] for n in nodes], 'source_digest': plan['source_binding']['content_digest'],
               'collection_digest': plan['collection_digest'], 'serialization_group': nodes[0]['serialization_group'],
               'expected_seconds': sum(durations.get(n['id'], 1.0) for n in nodes)}
        row['assignment_digest'] = digest(row)
        result.append(row)
    return sorted(result, key=lambda r: (bool(r['serialization_group']), -r['expected_seconds'], r['suite'], r['shard_id']))


def validate_child_report(assignment, report, process):
    errors = []
    for key in ('schema_version', 'run_id', 'shard_id', 'suite', 'assignment_digest', 'source_digest'):
        if report.get(key) != assignment[key]:errors.append('child binding mismatch: ' + key)
    if process.get('ownership') and report.get('launch_token')!=process['ownership']['token']:errors.append('child launch token mismatch')
    if report.get('finalized') is not True:errors.append('child report not finalized')
    if report.get('exitstatus') != process['returncode']:errors.append('process/report exit mismatch')
    if report.get('nodes') != assignment['nodes']:errors.append('assigned collection/source/fixture drift')
    if report.get('deselected') or report.get('errors'):errors.append('child collection errors/deselection')
    expected = {n['nodeid'] for n in assignment['nodes']}
    stages = report.get('reports', {})
    if set(stages) != expected:errors.append('missing/extra executed nodes')
    outcomes = {}
    for node in expected:
        results = stages.get(node, {})
        if not isinstance(results, dict):
            outcomes[qualified(assignment['suite'], node)] = 'error';continue
        if any(r.get('wasxfail') for r in results.values()):
            status = 'xfailed' if any(r['outcome'] == 'skipped' for r in results.values()) else 'xpassed'
        elif any(r.get('outcome') == 'failed' for r in results.values()):status = 'failed'
        elif any(r.get('outcome') == 'skipped' for r in results.values()):status = 'skipped'
        elif set(results) == {'setup', 'call', 'teardown'} and all(r.get('outcome') == 'passed' for r in results.values()):status = 'passed'
        else:status = 'error'
        outcomes[qualified(assignment['suite'], node)] = status
    if process['returncode'] == 0 and any(v in {'failed', 'error'} for v in outcomes.values()):errors.append('successful process conceals failing stages')
    if process['returncode'] != 0:errors.append('child process failed')
    return {'outcomes': outcomes, 'errors': errors, 'subprocesses': report.get('subprocesses', 0), 'runtime_profile': report.get('runtime_profile', {}),
            'durations': {qualified(assignment['suite'], node): sum(r.get('duration', 0) for r in stages.get(node, {}).values()) for node in expected}}


def cancel_owned(children, cause):
    errors, unresolved, secondary = [], [], []
    for child in children:
        try:result=child['owner'].cleanup()
        except BaseException as exc:
            errors.append('owned cleanup raised '+type(exc).__name__+': '+str(exc))
            unresolved.append(child['owner'].record())
            continue
        errors.extend(result['errors']);unresolved.extend(result['unresolved'])
        secondary.extend(result['secondary_signals'])
    return {'cause': cause, 'errors': errors, 'unresolved': unresolved,
            'secondary_signals': secondary, 'complete': not errors and not unresolved}


def run(plan, *, workers, artifact_root, cache_mode, durations=None, profile=False):
    started = time.monotonic()
    workers = max(1, min(int(workers), 8, os.cpu_count() or 1))
    artifact_root = Path(artifact_root);artifact_root.mkdir(parents=True, exist_ok=True)
    assignments = partition(plan, workers=workers, durations=durations or {})
    write_json(artifact_root / 'plan.json', plan)
    pending, active, receipts = list(assignments), [], []
    cleanup = {'complete': True, 'errors': [], 'unresolved': []}
    cause = None
    journal = {'schema_version': 1, 'run_id': plan['run_id'], 'source_digest': plan['source_binding']['content_digest'],
               'status': 'RUNNING', 'owner_pid': os.getpid(), 'children': []}
    write_json(artifact_root / 'journal.json', journal)
    previous = {}
    def interrupted(number, _frame):raise KeyboardInterrupt(signal.Signals(number).name)
    def launch(assignment):
        directory = artifact_root / assignment['shard_id'];directory.mkdir(mode=0o700)
        path, report, log = directory / 'assignment.json', directory / 'report.json', directory / 'output.log'
        write_json(path, assignment)
        suite = plan['collections'][assignment['suite']]
        argv = pytest_argv(suite['cwd']) + ['--basetemp', str(directory / 'pytest-tmp'), '--al-assignment', str(path), '--al-child-report', str(report)]
        argv.extend(n['nodeid'] for n in assignment['nodes'])
        output = log.open('wb')
        owner=OwnedProcess(directory,assignment['assignment_digest'])
        child = {'assignment': assignment, 'report': report, 'log': log, 'output': output, 'started': time.monotonic(), 'argv': argv, 'owner':owner}
        active.append(child)
        try:
            child['process'] = owner.spawn(argv, cwd=suite['cwd'], env=environment(plan['source_binding']['root'], directory, profile=profile, cache_mode=cache_mode), output=output)
        except BaseException:
            output.close();raise
        journal['children'].append({'shard_id': assignment['shard_id'], 'assignment_digest': assignment['assignment_digest'], **owner.record()})
        write_json(artifact_root / 'journal.json', journal)
        return child
    try:
        for number in (signal.SIGINT, signal.SIGTERM):previous[number] = signal.signal(number, interrupted)
        while pending or active:
            exclusive = any(c['assignment']['serialization_group'] for c in active)
            while pending and len(active) < workers and not exclusive:
                assignment = pending[0]
                if assignment['serialization_group'] and active:break
                pending.pop(0)
                launch(assignment)
                if assignment['serialization_group']:break
            finished = [c for c in active if c['process'].poll() is not None]
            if not finished:
                time.sleep(0.02);continue
            for child in finished:
                child['output'].close()
                process = {'returncode': child['process'].returncode, 'pid': child['process'].pid, 'argv': child['argv'], 'elapsed_seconds': time.monotonic() - child['started'], 'ownership':child['owner'].record()}
                descendant_cleanup=child['owner'].cleanup()
                cleanup.setdefault('secondary_signals', []).extend(descendant_cleanup['secondary_signals'])
                cleanup['errors'].extend(descendant_cleanup['errors'])
                cleanup['unresolved'].extend(descendant_cleanup['unresolved'])
                cleanup['complete']=not cleanup['errors'] and not cleanup['unresolved']
                if descendant_cleanup['secondary_signals']:raise KeyboardInterrupt(descendant_cleanup['secondary_signals'][0])
                if not descendant_cleanup['complete']:raise OSError('owned descendant cleanup uncertain')
                try:
                    report = read_json(child['report'])
                    validated = validate_child_report(child['assignment'], report, process)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    validated = {'outcomes': {}, 'errors': ['missing/invalid child report: ' + str(exc)]}
                reference={k: child['assignment'][k] for k in ('shard_id','suite','assignment_digest','source_digest')}
                reference['path']=str(child['report'].parent/'assignment.json')
                receipts.append({'assignment': reference, 'process': process, 'log': str(child['log']),
                                 'log_sha256': hashlib.sha256(child['log'].read_bytes()).hexdigest(),
                                 'report': str(child['report']), 'report_sha256': hashlib.sha256(child['report'].read_bytes()).hexdigest() if child['report'].is_file() else None,
                                 **validated})
                active.remove(child)
    except BaseException as exc:
        cause = {'kind': type(exc).__name__, 'message': str(exc)}
        cancelled = cancel_owned(active, cause)
        cleanup['errors'].extend(cancelled['errors']);cleanup['unresolved'].extend(cancelled['unresolved'])
        cleanup.setdefault('secondary_signals', []).extend(cancelled['secondary_signals'])
        cleanup['complete']=not cleanup['errors'] and not cleanup['unresolved']
    finally:
        for child in active:
            try:child['output'].close()
            except BaseException as exc:
                cleanup['errors'].append('owned output close failed: '+str(exc));cleanup['complete']=False
        for number, handler in previous.items():signal.signal(number, handler)
    reconciliation = reconcile(plan, receipts)
    if cause or pending or active or not cleanup['complete']:
        reconciliation.update(complete=False, selected_success=False)
    journal.update(status='PENDING' if not cleanup['complete'] else 'FINISHED', cause=cause, cleanup=cleanup,
                   reconciliation=reconciliation, ended_at=time.time())
    write_json(artifact_root / 'journal.json', journal)
    return {'schema_version': 1, 'run_id': plan['run_id'], 'workers': workers, 'cache_mode': cache_mode,
            'cache_conditions': 'fresh process-local caches on each command; warm may benefit from OS caches',
            'elapsed_seconds': time.monotonic() - started, 'shards': receipts,
            'assignments': [{k:a[k] for k in ('shard_id','suite','assignment_digest','serialization_group')} for a in assignments],
            'reconciliation': reconciliation, 'cause': cause, 'cleanup': cleanup,
            'journal': str(artifact_root / 'journal.json')}
