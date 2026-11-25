"""Planning reuse retains old authorship and requires current Architect assessment."""
import copy
import json
from pathlib import Path

import al_proof_json as J
import pytest
from launcher_context import _reports
from test_planning_authoring import pending, ok


def prepared(cli, repo, tmp_path, monkeypatch, *, accepted=False, extra=False, degraded=False):
    pending(cli, repo, tmp_path, monkeypatch)
    previous = json.loads(ok(cli, 'planning', 'packet')[1])
    original = _reports().report_for(previous)
    original['authored_obligations']['implementation_context'] = {'status': 'not_started', 'observation_ref': None}
    old_report = tmp_path / 'old-report.json'
    old_report.write_text(json.dumps(original))
    if accepted:
        ok(cli, 'planning', 'report', '--file', str(old_report))
    old_packet = tmp_path / 'old-packet.json'
    old_packet.write_text(json.dumps(previous['content']))
    if degraded:
        ok(cli, 'phase', 'IMPLEMENTATION')
        ok(cli, 'writer', 'start', '--id', 'later-degraded', '--role', 'al-engineer', '--files', 'svc/app.py',
           '--degraded', '--reason', 'Implement defined scoped work while current planning admission is pending.')
    amendment = {'id': 'AM-1', 'source': 'explicit developer clarification',
                 'text': 'Clarify the existing outcome without replacing the original accepted requirements.'}
    if extra:
        amendment['supplemental_acceptance'] = ['AC-02']
    path = tmp_path / 'amend.json'
    path.write_text(json.dumps(amendment))
    ok(cli, 'task', 'amend', '--file', str(path))
    ok(cli, 'spec-gate', 'SPEC_READY')
    fresh = json.loads(ok(cli, 'planning', 'packet')[1])
    return previous, original, old_packet, old_report, fresh


def derive(cli, old_packet, old_report):
    return json.loads(ok(cli, 'planning', 'rebind', '--from-packet', str(old_packet),
                         '--from-report', str(old_report))[1])


def fill(result, tmp_path):
    report = copy.deepcopy(result['report_template'])
    report['invocation_id'] = 'actual-architect-current-invocation'
    report['specification'] = {'status': 'SPEC_READY',
                               'reason': 'Architect reviewed the current amendment and source before retaining the authored plan.'}
    # This fixture has no admitted writer or implementation contribution.
    report['authored_obligations']['implementation_context'] = {'status': 'not_started', 'observation_ref': None}
    path = tmp_path / 'current-report.json'
    path.write_text(json.dumps(report))
    assessment = copy.deepcopy(result['revalidation_template'])
    assessment.update(report_sha256=J.digest(report), invocation_id=report['invocation_id'])
    assessment['assessment'] = {key: 'Architect inspected current ' + key + ' and verified inherited behavior remains appropriate.'
                                for key in assessment['assessment']}
    validation = tmp_path / 'current-revalidation.json'
    validation.write_text(json.dumps(assessment))
    return report, path, assessment, validation


@pytest.mark.parametrize('accepted', [False, True])
def test_rebind_preserves_original_and_does_not_admit_or_submit(cli, repo, tmp_path, monkeypatch, state_file, accepted):
    old, report, packet_path, report_path, fresh = prepared(cli, repo, tmp_path, monkeypatch, accepted=accepted)
    before_bytes = report_path.read_bytes()
    before_state = json.loads(state_file().read_bytes())
    historical = before_state['planning_history'][-1]
    current = before_state['planning_authorship']
    old_analysis = J.read(historical['analysis_observation'])
    current_analysis = J.read(current['analysis_observation'])
    assert old_analysis['packet'] == historical['packet']
    assert current_analysis['packet'] == current['packet']
    assert old_analysis['analysis']['source_specification']['amendments'] == []
    assert len(current_analysis['analysis']['source_specification']['amendments']) == 1
    assert historical['analysis_observation'] != current['analysis_observation']
    result = derive(cli, packet_path, report_path)
    receipt = J.read(result['receipt'])
    assert receipt['original_report_status'] == ('retained' if accepted else 'unsubmitted')
    assert J.read(receipt['original_report']) == report
    assert report_path.read_bytes() == before_bytes
    assert receipt['original_authorship']['invocation_id'] == report['invocation_id']
    assert result['authority'] == 'none' and result['status'] == 'requires-architect-revalidation'
    assert result['report_template']['authored_obligations']['obligations'] == report['authored_obligations']['obligations']
    assert result['report_template']['binding'] == fresh['content']['binding']
    assert 'FILL:' in result['report_template']['invocation_id']
    state = json.loads(state_file().read_bytes())
    assert state['agent_contracts'] == before_state['agent_contracts']
    assert state['agent_contract_history'] == before_state['agent_contract_history']
    assert state['proof_admission']['status'] == 'pending'
    assert 'report' not in state['planning_authorship']
    path = tmp_path / 'unfinished.json'
    path.write_text(json.dumps(result['report_template']))
    assert cli('planning', 'report', '--file', str(path))[0] != 0


@pytest.mark.parametrize('defect', ['missing', 'newer-issuance', 'ambiguous-reference', 'bytes'])
def test_historical_analysis_cannot_be_regenerated_or_substituted(
        cli, repo, tmp_path, monkeypatch, state_file, defect):
    _, _, packet, report, _ = prepared(cli, repo, tmp_path, monkeypatch)
    state = json.loads(state_file().read_bytes())
    prior = state['planning_history'][-1]
    if defect == 'missing':
        del prior['analysis_observation']
    elif defect == 'newer-issuance':
        prior['analysis_observation'] = state['planning_authorship']['analysis_observation']
    elif defect == 'ambiguous-reference':
        prior['analysis_observation'] = [prior['analysis_observation']]
    else:
        reference = prior['analysis_observation']
        Path(reference['stored_path']).write_bytes(Path(reference['stored_path']).read_bytes() + b' ')
    state_file().write_text(json.dumps(state))
    before = state_file().read_bytes()
    response = cli('planning', 'rebind', '--from-packet', str(packet), '--from-report', str(report))
    assert response[0] != 0 and 'report_template' not in response[1]
    assert state_file().read_bytes() == before


def test_same_spec_later_analysis_observation_cannot_authorize_old_packet(
        cli, repo, tmp_path, monkeypatch, state_file):
    pending(cli, repo, tmp_path, monkeypatch)
    old = json.loads(ok(cli, 'planning', 'packet')[1])
    report = tmp_path / 'original-report.json'
    report.write_text(json.dumps(_reports().report_for(old)))
    packet = tmp_path / 'original-packet.json'
    packet.write_text(json.dumps(old['content']))
    (repo / 'svc/app.py').write_text('later_source = 1\n')
    fresh = json.loads(ok(cli, 'planning', 'packet')[1])
    assert old['content']['task_package'] == fresh['content']['task_package']
    assert old['content']['accepted_amendments'] == fresh['content']['accepted_amendments'] == []
    state = json.loads(state_file().read_bytes())
    state['planning_history'][-1]['analysis_observation'] = state['planning_authorship']['analysis_observation']
    state_file().write_text(json.dumps(state))
    before = state_file().read_bytes()
    result = cli('planning', 'rebind', '--from-packet', str(packet), '--from-report', str(report))
    assert result[0] != 0 and 'different planning issuance' in ''.join(map(str, result))
    assert state_file().read_bytes() == before


def test_current_revalidation_binds_final_report_and_retains_derivation(cli, repo, tmp_path, monkeypatch, state_file):
    _, _, packet_path, report_path, _ = prepared(cli, repo, tmp_path, monkeypatch)
    result = derive(cli, packet_path, report_path)
    report, path, _, validation = fill(result, tmp_path)
    ok(cli, 'planning', 'report', '--file', str(path), '--rebind-receipt', result['receipt']['sha256'],
       '--revalidation-file', str(validation))
    current = json.loads(state_file().read_bytes())['planning_authorship']
    assert J.read(current['report']) == report
    assert current['rebind_validation']['receipt'] == result['receipt']
    assert J.read(current['rebind_validation']['revalidation'])['report_sha256'] == J.digest(report)
    # Rebind/report are non-authorizing; only ordinary current admission may publish proof.
    assert json.loads(state_file().read_bytes())['proof_admission']['status'] == 'pending'


@pytest.mark.parametrize('defect', ['foreign-packet', 'forged-report', 'foreign-receipt', 'wrong-final-report'])
def test_unrelated_or_mixed_derivations_refuse_without_state_change(cli, repo, tmp_path, monkeypatch, state_file, defect):
    _, original, packet_path, report_path, _ = prepared(cli, repo, tmp_path, monkeypatch)
    if defect in {'foreign-packet', 'forged-report'}:
        if defect == 'foreign-packet':
            value = json.loads(packet_path.read_text()); value['binding']['run_id'] = 'foreign'
            packet_path.write_text(json.dumps(value))
        else:
            original['packet_sha256'] = '0' * 64; report_path.write_text(json.dumps(original))
        before = state_file().read_bytes()
        assert cli('planning', 'rebind', '--from-packet', str(packet_path), '--from-report', str(report_path))[0] != 0
    else:
        result = derive(cli, packet_path, report_path)
        _, path, assessment, validation = fill(result, tmp_path)
        if defect == 'wrong-final-report':
            assessment['report_sha256'] = '0' * 64; validation.write_text(json.dumps(assessment))
        before = state_file().read_bytes()
        identifier = '0' * 64 if defect == 'foreign-receipt' else result['receipt']['sha256']
        assert cli('planning', 'report', '--file', str(path), '--rebind-receipt', identifier,
                   '--revalidation-file', str(validation))[0] != 0
    assert state_file().read_bytes() == before


def test_added_acceptance_requires_new_obligations(cli, repo, tmp_path, monkeypatch, state_file):
    _, _, packet_path, report_path, _ = prepared(cli, repo, tmp_path, monkeypatch, extra=True)
    result = derive(cli, packet_path, report_path)
    assert result['coverage_diagnostics'] and 'supplemental' in result['coverage_diagnostics'][0]
    _, path, _, validation = fill(result, tmp_path)
    before = state_file().read_bytes()
    assert cli('planning', 'report', '--file', str(path), '--rebind-receipt', result['receipt']['sha256'],
               '--revalidation-file', str(validation))[0] != 0
    assert state_file().read_bytes() == before


def test_moved_source_requires_new_current_packet(cli, repo, tmp_path, monkeypatch, state_file):
    _, _, packet_path, report_path, _ = prepared(cli, repo, tmp_path, monkeypatch)
    result = derive(cli, packet_path, report_path)
    _, path, _, validation = fill(result, tmp_path)
    (repo / 'svc/app.py').write_text('changed_after_draft = True\n')
    before = state_file().read_bytes()
    outcome = cli('planning', 'report', '--file', str(path), '--rebind-receipt', result['receipt']['sha256'],
                  '--revalidation-file', str(validation))
    assert outcome[0] != 0 and 'source observation changed' in ''.join(map(str, outcome))
    assert state_file().read_bytes() == before


def test_issued_rebind_cannot_omit_revalidation_flags_or_saved_provenance(cli, repo, tmp_path, monkeypatch, state_file):
    import al_planning as P
    import al_store

    _, _, packet_path, report_path, _ = prepared(cli, repo, tmp_path, monkeypatch)
    result = derive(cli, packet_path, report_path)
    report, path, _, _ = fill(result, tmp_path)
    before = state_file().read_bytes()
    outcome = cli('planning', 'report', '--file', str(path))
    assert outcome[0] != 0 and 'issued planning rebind requires' in ''.join(map(str, outcome))
    assert state_file().read_bytes() == before
    state = al_store.load(json.loads(before)['session_id'])
    # Counterexample to an old saved state omitting the new required link.
    state['planning_authorship']['report'] = J.snapshot(state['session_id'], report, 'architect-planning-report-v1')
    state['planning_authorship']['obligations'] = result['draft']
    with pytest.raises(SystemExit, match='lacks saved current Architect revalidation'):
        P.validate_admitted(state)


def test_old_not_started_report_rebinds_after_current_degraded_writer(cli, repo, tmp_path, monkeypatch):
    _, original, packet_path, report_path, fresh = prepared(cli, repo, tmp_path, monkeypatch, degraded=True)
    assert original['authored_obligations']['implementation_context']['status'] == 'not_started'
    result = derive(cli, packet_path, report_path)
    assert J.read(J.read(result['receipt'])['original_report']) == original
    report, path, assessment, validation = fill(result, tmp_path)
    # The unchanged old context cannot satisfy current degraded implementation.
    outcome = cli('planning', 'report', '--file', str(path), '--rebind-receipt', result['receipt']['sha256'],
                  '--revalidation-file', str(validation))
    assert outcome[0] != 0 and 'partially_built' in ''.join(map(str, outcome))
    report['authored_obligations']['implementation_context'] = {
        'status': 'partially_built', 'observation_ref': fresh['content']['source_observation']}
    path.write_text(json.dumps(report))
    assessment['report_sha256'] = J.digest(report)
    validation.write_text(json.dumps(assessment))
    ok(cli, 'planning', 'report', '--file', str(path), '--rebind-receipt', result['receipt']['sha256'],
       '--revalidation-file', str(validation))
    assert cli('planning', 'admit')[0] != 0  # The actual writer is still ACTIVE.
