"""Public failed-batch continuation retains failure and owned destination identity."""
import copy
import json
import sys
import time
from pathlib import Path
import pytest
import al_gate_continuation as Resume
import al_gates as Gates
from batch13_helpers import start, manifest
from hook_regression_helpers import invoke
from evidence_helpers import wait_running
from launcher_context import admit_planning


def prepare(cli, repo, tmp_path, slow=False):
    start(cli, repo)
    markers = [tmp_path/name for name in ('first', 'middle', 'last')]
    release = tmp_path/'release'
    def command(marker, middle=False):
        body = 'from pathlib import Path; import sys,time; p=Path('+repr(str(marker))+'); p.write_text(p.read_text()+"x" if p.exists() else "x"); '
        if middle:
            body += ('\nwhile not Path('+repr(str(release))+').exists(): time.sleep(0.02)\n' if slow else 'sys.exit(0 if Path('+repr(str(release))+').exists() else 1)')
        return [sys.executable, '-c', body]
    manifest(cli, tmp_path, command(markers[0]))
    state = cli.state(); value = Gates.manifest(state)
    first = next(g for g in value['gates'] if g['id'] == 'boundary')
    for name, marker in zip(('middle', 'last'), markers[1:]):
        gate = copy.deepcopy(first)
        gate.update(id=name, argv=command(marker, name=='middle'))
        value['gates'].append(gate)
    path = tmp_path/'chain.json'; path.write_text(json.dumps(value))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    return markers, release


def report(output):
    for line in reversed(output.splitlines()):
        if line.startswith('{'):
            value = json.loads(line)
            if value.get('kind') == 'continuation-report-v1':return value
    raise AssertionError(output[-2000:])


def test_failed_middle_resumes_only_missing_and_requires_explicit_failed_retry(cli, repo, tmp_path):
    markers, release = prepare(cli, repo, tmp_path)
    invoke(cli, 'gates', 'run', '--stage', 'candidate', ok=False)
    assert [p.read_text() if p.exists() else '' for p in markers] == ['x', 'x', '']
    _, output, _ = invoke(cli, 'gates', 'run', '--stage', 'candidate', '--resume', ok=False)
    result = report(output)
    assert result['selected'] == ['last'] and result['retained_failed'] == ['middle']
    assert not result['stage_ready'] and result['unexecuted'] == []
    assert [p.read_text() for p in markers] == ['x', 'x', 'x']
    release.write_text('retry explicitly authorized by the test')
    _, output, _ = invoke(cli, 'gates', 'run', '--stage', 'candidate', '--resume', '--retry-failed')
    result = report(output)
    assert result['selected'] == ['middle'] and result['stage_ready']
    assert [p.read_text() for p in markers] == ['x', 'xx', 'x']
    state = cli.state()
    failures = [e for e in state['evidence']['entries'] if e['area'] == 'gate-candidate-middle' and e['status'] == 'FAIL']
    assert len(failures) == 1 and Path(failures[0]['log']).is_file()
    seq = state['evidence']['seq']
    _, output, _ = invoke(cli, 'gates', 'run', '--stage', 'candidate', '--resume')
    assert report(output)['selected'] == [] and cli.state()['evidence']['seq'] == seq


def test_explicit_id_can_retry_failure_but_does_not_claim_unexecuted_stage(cli, repo, tmp_path):
    markers, release = prepare(cli, repo, tmp_path)
    invoke(cli, 'gates', 'run', '--stage', 'candidate', ok=False)
    release.write_text('explicit retry')
    _, output, _ = invoke(cli, 'gates', 'run', '--stage', 'candidate', '--resume', '--id', 'middle', ok=False)
    result = report(output)
    assert result['selected'] == ['middle'] and result['unexecuted'] == ['last']
    assert not result['stage_ready'] and not result['retained_failed']
    assert markers[1].read_text() == 'xx' and not markers[2].exists()


def test_baseline_fix_decision_is_preserved_without_synthesizing_pass(cli, repo, tmp_path):
    markers, _ = prepare(cli, repo, tmp_path)
    invoke(cli, 'gates', 'run', '--stage', 'base', ok=False)
    invoke(cli, 'gates', 'decision', '--id', 'middle', '--action', 'fix',
           '--reason', 'The developer explicitly includes repairing this demonstrated baseline failure.',
           '--source', 'Synthetic developer baseline-fix decision')
    # Every base gate already ran in the first pass, so the resume has nothing left to execute.
    assert markers[2].read_text() == 'x'
    _, output, _ = invoke(cli, 'gates', 'run', '--stage', 'base', '--resume')
    result = report(output)
    assert result['selected'] == [] and result['stage_ready']
    assert result['base_decisions']['middle']['action'] == 'fix'
    state = cli.state()
    attempts = [e for e in state['evidence']['entries'] if e['area'] == 'gate-base-middle']
    assert len(attempts) == 1 and attempts[0]['status'] == 'FAIL' and markers[1].read_text() == 'x'


def test_atomic_recheck_rejects_running_destination_without_new_reservation(cli, repo, tmp_path, state_file, cli_process):
    markers, release = prepare(cli, repo, tmp_path, slow=True)
    # Complete the actual independently sourced architecture gate first.
    value = Gates.manifest(cli.state())
    architecture = [g['id'] for g in value['gates'] if g['category'] == 'lint-imports' and g['applicability'] == 'APPLICABLE']
    for gate in architecture:invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', gate)
    process = cli_process('gates', 'run', '--stage', 'candidate', '--resume', '--id', 'middle')
    try:
        wait_running(state_file, 'gate-candidate-middle', process=process)
        before = state_file().read_bytes()
        code, output, err = invoke(cli, 'gates', 'run', '--stage', 'candidate', '--resume', '--id', 'middle', ok=False)
        assert 'unresolved' in str(code)+output+err
        assert state_file().read_bytes() == before
        deadline=time.monotonic()+5
        while not markers[1].exists() and time.monotonic()<deadline:time.sleep(0.01)
        assert markers[1].read_text() == 'x'
    finally:
        release.write_text('release owned test operation')
        stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 1, stdout[-1000:]+stderr[-1000:]
    state = cli.state()
    attempts = [e for e in state['evidence']['entries'] if e['area'] == 'gate-candidate-middle']
    assert len(attempts) == 1 and attempts[0]['status'] == 'PASS'


def test_selection_is_rechecked_after_another_invocation_finishes(cli, repo, tmp_path):
    markers, release = prepare(cli, repo, tmp_path)
    release.write_text('success')
    state = cli.state(); value = Gates.manifest(state)
    selected = Resume.select_continuation(state, value, 'candidate')
    assert 'boundary' in [g['id'] for g in selected]
    invoke(cli, 'gates', 'run', '--stage', 'candidate')
    state = cli.state()
    context = {'stage':'candidate','gate':'boundary','manifest':state['gate_manifests'][-1]['sha256'],'resume':True,'explicit':False,'retry_failed':False}
    with pytest.raises(Resume.DestinationSettled):Resume.admit_destination(state, context)
    assert [p.read_text() for p in markers] == ['x', 'x', 'x']


def test_cleanup_uncertainty_remains_blocking_even_after_reconciliation(cli, repo, tmp_path):
    _, release = prepare(cli, repo, tmp_path)
    release.write_text('success')
    invoke(cli, 'gates', 'run', '--stage', 'candidate')
    state = cli.state(); entry = next(e for e in reversed(state['evidence']['entries']) if e['area'] == 'gate-candidate-middle')
    entry.update(cleanup={'errors':['signal denied'], 'unresolved':True}, reconciliation={'note':'does not prove cleanup'})
    value = Gates.manifest(state)
    assert 'middle' not in [g['id'] for g in Resume.select_continuation(state, value, 'candidate', retry_failed=True)]
    assert 'middle' in Resume.stage_report(state, 'candidate')['unresolved']
    context = {'stage':'candidate','gate':'middle','manifest':state['gate_manifests'][-1]['sha256']}
    with pytest.raises(SystemExit, match='unresolved'):Resume.admit_destination(state, context)


def test_retry_failed_requires_explicit_continuation_and_unknown_id_refuses_atomically(cli, repo, tmp_path, state_file):
    prepare(cli, repo, tmp_path)
    for args in (('--retry-failed',), ('--resume','--id','unknown')):
        before = state_file().read_bytes()
        invoke(cli, 'gates', 'run', '--stage', 'candidate', *args, ok=False)
        assert state_file().read_bytes() == before


def test_other_destination_cannot_launch_past_unresolved_owned_cleanup(cli, repo, tmp_path):
    _, release = prepare(cli, repo, tmp_path)
    release.write_text('success')
    invoke(cli, 'gates', 'run', '--stage', 'candidate')
    state = cli.state()
    entry = next(e for e in state['evidence']['entries'] if e['area'] == 'gate-candidate-middle')
    entry.update(status='INFRA', cleanup={'errors':['signal denied'], 'unresolved':True},
                 reconciliation={'note':'historical reconciliation cannot attest descendant cleanup'})
    context = {'stage':'candidate','gate':'last','manifest':state['gate_manifests'][-1]['sha256'],
               'resume':True,'explicit':True,'retry_failed':False}
    with pytest.raises(SystemExit, match='owned.*cleanup'):
        Resume.admit_destination(state, context)
    result = Resume.stage_report(state, 'candidate')
    assert not result['stage_ready'] and 'middle' in result['unresolved']


def test_resume_other_checks_never_approves_around_actual_architecture_failure(cli, repo, tmp_path, state_file):
    (repo/'svc/app.py').write_text('import forbidden_dependency\n')
    start(cli, repo)
    marker=tmp_path/'independent-check'
    argv=[sys.executable,'-c','from pathlib import Path; Path('+repr(str(marker))+').write_text("verified")']
    manifest(cli, tmp_path, argv)
    value=Gates.manifest(cli.state())
    architecture=next(g['id'] for g in value['gates'] if g['category']=='lint-imports' and g['applicability']=='APPLICABLE')
    invoke(cli, 'gates', 'run', '--stage', 'base', ok=False)
    invoke(cli, 'gates', 'decision', '--id', architecture, '--action', 'fix',
           '--reason', 'The developer explicitly includes the demonstrated forbidden dependency repair.',
           '--source', 'Synthetic developer baseline-fix decision')
    invoke(cli, 'gates', 'run', '--stage', 'base', '--resume')
    invoke(cli, 'spec-gate', 'SPEC_READY')
    invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    admit_planning(cli)
    invoke(cli, 'phase', 'IMPLEMENTATION')
    invoke(cli, 'phase', 'VERIFICATION')
    from evidence_helpers import plan_and_run_broad
    assert plan_and_run_broad(cli) == 0
    invoke(cli, 'gates', 'run', '--stage', 'candidate', ok=False)
    _, output, _=invoke(cli, 'gates', 'run', '--stage', 'candidate', '--resume', ok=False)
    result=report(output)
    assert result['selected']==['boundary'] and result['retained_failed']==[architecture]
    assert marker.read_text()=='verified' and not result['stage_ready']
    state=cli.state()
    failed=next(e for e in reversed(state['evidence']['entries']) if e['area']=='gate-candidate-'+architecture)
    assert failed['status']=='FAIL' and 'forbidden_dependency' in Path(failed['log']).read_text()
    before=state_file().read_bytes()
    code, out, err=invoke(cli, 'review-start', ok=False)
    assert 'candidate gate '+architecture+' requires passing evidence' in str(code)+out+err
    assert state_file().read_bytes()==before
    invoke(cli, 'approved', ok=False)
