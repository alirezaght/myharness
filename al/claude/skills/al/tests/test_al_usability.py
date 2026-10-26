"""Public regressions for planning diagnostics, artifact creation and tracked recovery."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import al_artifacts as A
import al_behavior_obligations as B
import al_gate_discovery as D
from al_recovery_prerequisites import prerequisites
from al_proof_json import reference
from hook_regression_helpers import invoke, manifest_for
from launcher_context import admit_planning
from al.tests.planning_reports import report_for


def state(cli):
    return json.loads(invoke(cli, 'show', '--json')[1])


def pending(cli, tmp_path, repo):
    invoke(cli, 'start', '--spec', 'Preserve behavior and track every implementation edit.')
    invoke(cli, 'spec-gate', 'SPEC_READY')
    invoke(cli.raw, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    path = tmp_path / 'gates.json'
    path.write_text(json.dumps(manifest_for(state(cli), repo, ['svc/app.py'])))
    invoke(cli, 'gates', 'plan', '--file', str(path))


def test_report_names_uncovered_outcomes_and_template_explains_the_rule(cli, tmp_path, repo, state_file):
    pending(cli, tmp_path, repo)
    packet = json.loads(invoke(cli, 'planning', 'packet')[1])
    assert 'every preserve, reset, journey_end' in json.dumps(packet['report_template'])
    report = report_for(packet)
    group = report['authored_obligations']['obligations'][0]
    missing = group['preserve']['items'][0]['id']
    for scenario in group['scenarios']:
        scenario['outcome_ids'] = [i for i in scenario['outcome_ids'] if i != missing]
        if not scenario['outcome_ids']:
            scenario['outcome_ids'] = [group['journey_end']['items'][0]['id']]
    path = tmp_path / 'report.json';path.write_text(json.dumps(report))
    before = state_file().read_bytes()
    code, out, err = invoke(cli, 'planning', 'report', '--file', str(path), ok=False)
    assert code != 0 and missing in str(code) + out + err and 'outcome_ids' in str(code) + out + err
    assert state_file().read_bytes() == before


def test_artifact_record_creates_real_closed_consumer_references(cli, tmp_path):
    invoke(cli, 'start', '--spec', 'Create discovery references without bypassing admission.')
    path = tmp_path / 'discovery.md';path.write_text('Consumer inventory: dashboard reads delivery snapshots.')
    ref = json.loads(invoke(cli, 'artifact', 'record', '--file', str(path), '--kind', 'consumer-discovery')[1])
    assert set(ref) == {'id','kind','sha256','stored_path','source','captured_at'}
    assert reference(ref) == path.read_bytes()
    B.consumers({'applicability':'applicable','reason':'A changed export has a concrete reader.',
        'entries':[{'id':'dashboard','role':'consumer','kind':'code','contract':'delivery snapshot',
                    'locations':['dashboard.py'], 'source_refs':[ref]}], 'discovery_refs':[ref], 'uncertainties':[]})
    with pytest.raises(SystemExit, match='missing fields.*captured_at'):
        reference({'sha256':ref['sha256']})
    assert state(cli)['proof_admission']['status'] == 'pending'


def test_named_output_is_exact_private_versioned_and_not_a_prompt_match(cli, tmp_path):
    invoke(cli, 'start', '--spec', 'Retain exact named final agent output.')
    path = tmp_path / 'reply.json';path.write_text('{"resolution":"actual final answer"}')
    result = json.loads(invoke(cli, 'artifact', 'output', '--name', 'architect.json', '--file', str(path))[1])
    assert Path(result['path']).read_bytes() == path.read_bytes()
    assert A.read(result['artifact']) == path.read_bytes()
    again = json.loads(invoke(cli, 'artifact', 'show', '--name', 'architect.json')[1])
    assert again == result
    path.write_text('{"resolution":"revised final answer"}')
    invoke(cli, 'artifact', 'output', '--name', 'architect.json', '--file', str(path), ok=False)
    updated = json.loads(invoke(cli, 'artifact', 'output', '--name', 'architect.json', '--file', str(path),
                                '--expected-sha256', result['artifact']['sha256'])[1])
    assert updated['path'] != result['path']
    assert b'actual final answer' in Path(result['path']).read_bytes()


def test_degraded_writer_tracks_edits_and_evidence_without_waiving_admission(cli, tmp_path, repo, state_file):
    pending(cli, tmp_path, repo)
    before = state_file().read_bytes()
    code, out, err = invoke(cli, 'writer', 'start', '--id', 'early', '--role', 'al-engineer',
                            '--files', 'svc/app.py', ok=False)
    assert code != 0 and 'proof admission pending' in str(code) + out + err and '--degraded' in str(code) + out + err
    assert state_file().read_bytes() == before
    _, out, _ = invoke(cli, 'writer', 'start', '--id', 'early', '--role', 'al-engineer', '--files',
                       'svc/app.py', '--degraded', '--reason', 'Repair consumer proof while keeping source edits attributable')
    assert 'EVIDENCE' in out and '--writer early' in out and 'DEGRADED TRACKING' in out
    (repo / 'svc/app.py').write_text('value = 42\n')
    invoke(cli, 'evidence', 'plan', '--area', 'unit', '--tier', 'focused', '--scope', 'svc/app.py',
           '--kind', 'check', '--', sys.executable, '-c', 'print("verified")')
    invoke(cli, 'evidence', 'run', '--area', 'unit', '--writer', 'early')
    invoke(cli, 'writer', 'finish', '--id', 'early', '--outcome', 'success')
    current = state(cli)
    assert current['proof_admission']['status'] == 'pending'
    assert current['writers']['early']['contribution_before']
    assert current['evidence']['entries'][-1]['status'] == 'PASS'
    invoke(cli, 'phase', 'IMPLEMENTATION', ok=False)
    invoke(cli, 'approved', ok=False)
    packet = json.loads(invoke(cli, 'planning', 'packet')[1])
    assert packet['report_template']['authored_obligations']['implementation_context']['status'] == 'partially_built'
    report = report_for(packet)
    report['authored_obligations']['implementation_context'] = {
        'status':'partially_built', 'observation_ref':packet['content']['source_observation']}
    path = tmp_path / 'repaired-report.json';path.write_text(json.dumps(report))
    saved = json.loads(invoke(cli, 'planning', 'report', '--file', str(path))[1])
    invoke(cli, 'planning', 'admit', '--file', saved['materialized_obligations']['stored_path'])
    assert state(cli)['writers']['early']['degraded']['proof_status'] == 'reviewed'
    invoke(cli, 'phase', 'IMPLEMENTATION')


def test_public_writer_refusal_exits_nonzero_without_publishing_state(cli,tmp_path,repo,state_file,cli_process):
    pending(cli,tmp_path,repo)
    before=state_file().read_bytes()
    process=cli_process('writer','start','--id','refused','--role','al-engineer','--files','svc/app.py')
    out,err=process.communicate(timeout=30)
    assert process.returncode==1 and 'proof admission pending' in out+err
    assert state_file().read_bytes()==before


def test_named_preflight_satisfies_recovery_without_an_invented_plan(cli, tmp_path):
    invoke(cli, 'start', '--spec', 'Recognize the named preflight that actually ran.')
    invoke(cli, 'preflight', '--name', 'node-deps', '--', sys.executable, '-c', 'print("ready")')
    invoke(cli, 'evidence', 'plan', '--area', 'unit', '--scope', 'svc', '--preflight', 'node-deps',
           '--', sys.executable, '-c', 'print("verified")')
    assert prerequisites(state(cli)) == []
    current = state(cli);Path(current['evidence']['entries'][0]['log']).write_text('tampered')
    assert prerequisites(current)[0]['code'] == 'PREREQUISITE_UNRESOLVED'


def test_gate_timeout_has_a_reason_and_explicit_id_retry_is_supported(cli, tmp_path, repo):
    hook = repo / '.githooks/pre-commit';hook.parent.mkdir();hook.write_text('#!/bin/sh\nsleep 2\n')
    subprocess.run(['git','add','.githooks/pre-commit'],cwd=repo,check=True)
    subprocess.run(['git','commit','-qm','declare hook'],cwd=repo,check=True)
    invoke(cli, 'start', '--spec', 'Report hook timeout and retry exactly the selected gate.')
    invoke(cli, 'spec-gate', 'SPEC_READY')
    invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    manifest = D.discover(state(cli), ['svc/app.py'])
    path = tmp_path / 'hook-gates.json';path.write_text(json.dumps(manifest))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    gate = next(g for g in manifest['gates'] if g['category'] == 'pre-commit')
    args = ['gates','run','--stage','candidate','--id',gate['id'],'--timeout','0.05']
    for extra in ([], ['--retry-failed']):
        code, out, err = invoke(cli, *args, *extra, ok=False)
        assert 'requires --resume' not in out + err
        entry = state(cli)['evidence']['entries'][-1]
        assert entry['status'] == 'INFRA' and 'timeout after 0.05 s' in entry['note']
        assert 'timeout after 0.05 s' in Path(entry['log']).read_text()
    status = json.loads(invoke(cli, 'gates', 'status')[1])
    assert 'timeout after 0.05 s' in status['evidence'][-1]['reason']
