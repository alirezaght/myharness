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
