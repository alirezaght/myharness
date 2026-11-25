"""Repository brief (#30): today's facts behind one replaceable function."""
import json

import al_brief
from evidence_helpers import start_implementation


def test_brief_names_missing_facts_before_gates_are_planned(cli):
    assert cli('start', '--spec', 'Verify a scoped implementation and preserve its repository evidence.')[0] == 0
    code, out, _ = cli('brief')
    assert code == 0 and 'source: none' in out and 'gates discover' in out


def test_brief_reports_units_gates_and_gaps_from_the_manifest(cli, tmp_path, state_file, run_state):
    run_state("implemented")
    code, out, _ = cli('brief', '--format', 'json')
    assert code == 0
    value = json.loads(out)
    st = json.loads(state_file().read_bytes())
    manifest = json.loads(open(st['gate_manifests'][-1]['stored_path'], 'rb').read())
    assert value['kind'] == 'repository-brief-v1' and value['source'] == 'gate-manifest'
    assert value['facts']['sha256'] == st['gate_manifests'][-1]['sha256']
    assert value['reached_units'] == sorted(manifest['units'])[:al_brief.LIMIT]
    assert {g['id'] for g in value['applicable_gates']} == {g['id'] for g in manifest['gates'] if g['applicability'] == 'APPLICABLE'}
    assert set(manifest['paths']) <= set(value['paths'])
    code, text, _ = cli('brief')
    assert text.startswith('REPOSITORY BRIEF') and '- reached units:' in text and '- coverage gaps:' in text


def test_facts_is_the_single_backing_function(monkeypatch):
    fake = {'source': 'fact-graph', 'reference': {'kind': 'graph', 'sha256': 'f' * 64}, 'paths': ['a.py'],
            'units': ['svc'], 'gates': [{'id': 'g1', 'category': 'ci', 'applicability': 'APPLICABLE', 'units': ['svc'], 'required': True}],
            'rules': [{'path': 'AGENTS.md', 'sha256': '0' * 64}], 'service_coverage': {'assessment': {'known_affected': ['api']}}}
    monkeypatch.setattr(al_brief, 'facts', lambda st: fake)
    value = al_brief.brief({'gate_manifests': []}, ['b.py'])
    assert value['source'] == 'fact-graph' and value['paths'] == ['a.py', 'b.py']
    assert value['consumers'] == ['api'] and value['relevant_files'] == ['AGENTS.md']
    assert 'g1 [ci] units=svc required' in al_brief.render(value)
