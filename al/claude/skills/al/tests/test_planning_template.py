import json

from evidence_helpers import plan_na_gates
from execution_helpers import bound
import launcher_context
from launcher_context import _reports


def _packet(cli, repo, tmp_path, monkeypatch):
    bound(cli, tmp_path, repo, monkeypatch)
    # Exercise the real packet -> report -> admit path instead of the fixture's automatic admission.
    monkeypatch.setattr(launcher_context, 'admit_planning', lambda *args, **kwargs: None)
    assert cli('spec-gate', 'SPEC_READY')[0] == 0
    assert cli('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')[0] == 0
    plan_na_gates(cli, tmp_path, ('docs/readme.md',))
    code, out, err = cli('planning', 'packet')
    assert code == 0, (code, out, err)
    return json.loads(out)


def test_packet_prints_a_template_with_every_mechanical_field_filled(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    template, expected = packet['report_template'], _reports().report_for(packet)
    for key in ('schema_version', 'kind', 'attempt_id', 'model', 'effort', 'policy_digest', 'packet_sha256', 'binding', 'routing'):
        assert template[key] == expected[key], key
    behavior, reference = template['authored_obligations'], expected['authored_obligations']
    for key in ('binding', 'authorship', 'criteria_registry'):
        assert behavior[key] == reference[key], key


def test_unedited_template_is_refused_and_a_filled_one_is_admitted(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(packet['report_template']))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code != 0 and 'FILL:' in str(code) + out + err
    filled = json.loads(json.dumps(packet['report_template']).replace('FILL: ', 'Architect: '))
    path.write_text(json.dumps(filled))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, out, err)
    obligations = json.loads(out)['materialized_obligations']['stored_path']
    assert cli('planning', 'admit', '--file', obligations)[0] == 0


def test_behavior_packet_prints_an_observation_template_per_scenario(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    filled = json.loads(json.dumps(packet['report_template']).replace('FILL: ', 'Architect: '))
    path = tmp_path / 'report.json';path.write_text(json.dumps(filled))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, out, err)
    assert cli('planning', 'admit', '--file', json.loads(out)['materialized_obligations']['stored_path'])[0] == 0
    code, out, err = cli('behavior', 'packet')
    assert code == 0, (code, out, err)
    behavior = json.loads(out)
    templates = behavior['observation_templates']
    scenarios = [s['id'] for g in behavior['obligations']['content']['obligations'] for s in g['scenarios']]
    assert [t['scenario_id'] for t in templates] == scenarios
    path = tmp_path / 'observation.json';path.write_text(json.dumps(templates[0]))
    code, out, err = cli('behavior', 'observe', '--file', str(path))
    assert code != 0 and 'FILL:' in str(code) + out + err


def _filled(packet):
    return json.loads(json.dumps(packet['report_template']).replace('FILL: ', 'Architect: '))


def test_omitted_runtime_fixed_template_fields_are_restored(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    del report['specification']['status']
    for key in ('kind', 'binding', 'packet_sha256'):
        del report[key]
    del report['authored_obligations']['authorship']
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, out, err)
    stored = json.loads(open(json.loads(out)['report']['stored_path']).read())
    assert stored == _filled(packet)


def test_changed_runtime_fixed_template_field_is_refused_by_path(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    report['packet_sha256'] = '0' * 64
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code != 0 and 'packet_sha256' in str(code) + out + err


def test_missing_and_unexpected_fields_name_their_full_json_path(cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    del report['specification']['reason']
    report['specification']['extra'] = 1
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    text = str(code) + out + err
    assert code != 0 and 'specification.reason' in text and 'specification.extra' in text
    report = _filled(packet)
    del report['authored_obligations']['implementation_context']['status']
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    text = str(code) + out + err
    assert code != 0 and 'authored_obligations.implementation_context.status' in text


def test_identical_report_resubmit_is_idempotent_and_a_different_one_is_refused(
        cli, repo, tmp_path, monkeypatch):
    packet = _packet(cli, repo, tmp_path, monkeypatch)
    report = _filled(packet)
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    code, first, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, first, err)
    path.write_text(json.dumps(report, indent=2))
    code, again, err = cli('planning', 'report', '--file', str(path))
    assert code == 0, (code, again, err)
    first, again = json.loads(first), json.loads(again)
    assert again['report'] == first['report']
    assert again['materialized_obligations'] == first['materialized_obligations']
    report['specification']['reason'] = 'Architect: a different rationale'
    path.write_text(json.dumps(report))
    code, out, err = cli('planning', 'report', '--file', str(path))
    assert code != 0 and 'cannot be replaced' in str(code) + out + err
