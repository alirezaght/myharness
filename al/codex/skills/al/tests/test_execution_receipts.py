"""Real isolated executions supply direct immutable proof across invocations/stages."""
import copy
import json
import sys
from pathlib import Path

import pytest
import al_artifacts as A
import al_execution_receipts as R
import al_gates as G
import al_ledger as L
from batch13_helpers import start
from hook_regression_helpers import invoke
from test_gate_reuse import two_gates, evidence_for_gates


def reader_gates(cli, repo, tmp_path, anticipated=()):
    start(cli, repo)
    argv = [sys.executable, '-I', '-m', 'json.tool', 'svc/tests/fixtures/rows.json']
    two_gates(cli, tmp_path, argv, anticipated=anticipated)
    st = json.loads(cli('show')[1]);manifest = G.manifest(st)
    for gate in manifest['gates']:
        if gate['id'] in {'boundary', 'duplicate'}:gate['scope'] = ['Makefile', 'svc']
    file = tmp_path/'readers.json';file.write_text(json.dumps(manifest))
    invoke(cli, 'gates', 'plan', '--file', str(file))


@pytest.mark.parametrize('source_stage', ['base', 'candidate'])
def test_real_receipt_reuses_across_invocations_and_supported_stages(cli, repo, tmp_path, source_stage):
    reader_gates(cli, repo, tmp_path)
    invoke(cli, 'gates', 'run', '--stage', source_stage, '--id', 'boundary')
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', 'duplicate')
    st = json.loads(cli('show')[1])
    source = evidence_for_gates(st, {'boundary'}, source_stage)['boundary'][1]
    target = evidence_for_gates(st, {'duplicate'}, 'candidate')['duplicate'][1]
    assert source['pid'] and source['execution_completion']['complete']
    assert source['execution_receipt'] and target['pid'] is None and target['execution_association']
    assert not target.get('reuse') and not target.get('execution_receipt')
    association = A.read_json(target['execution_association'])
    assert association['receipt_ref'] == source['execution_receipt']
    assert source['invocation'] != target['invocation']
    assert R.validate_association(st, target) == []
    assert L.entry_verdict(st, target)[0] == 'VALID'
    receipt = A.read_json(source['execution_receipt'])
    assert receipt['original_reservation'] == source['reservation']
    assert receipt['process_ownership'] == source['execution_completion']
    assert receipt['cleanup']['group_absent'] and not receipt['cleanup']['unresolved']
    Path(source['log']).write_text('changed execution log')
    assert R.validate_association(st, target)
    assert L.entry_verdict(st, target)[0] == 'REUSE_INVALID'


def test_later_failed_running_or_uncertain_attempt_never_resurrects_old_pass(cli, repo, tmp_path):
    reader_gates(cli, repo, tmp_path)
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', 'boundary')
    baseline = json.loads(cli('show')[1]);original = evidence_for_gates(baseline, {'boundary'}, 'candidate')['boundary'][1]
    receipt = A.read_json(original['execution_receipt']);projection = receipt['projection']
    assert R.reuse_candidate(baseline, projection) == original['execution_receipt']
    for status in ['FAIL', 'RUNNING', 'INFRA', 'TORN', 'PASS-with-uncertainty']:
        st = copy.deepcopy(baseline);later = copy.deepcopy(original)
        later.update(id='E999', reservation='distinct-later-reservation', status='PASS' if status.startswith('PASS') else status)
        later.pop('execution_receipt', None)
        if status == 'PASS-with-uncertainty':later['cleanup'] = {'unresolved': True, 'errors': []}
        st['evidence']['entries'].append(later)
        assert R.reuse_candidate(st, projection) is None, status
        assert R.validate_receipt(st, original['execution_receipt'], projection), status


def test_original_and_destination_provenance_are_checked_independently(cli, repo, tmp_path):
    reader_gates(cli, repo, tmp_path)
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', 'boundary')
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', 'duplicate')
    baseline = json.loads(cli('show')[1]);rows = evidence_for_gates(baseline, {'boundary', 'duplicate'}, 'candidate')
    original_id = rows['boundary'][1]['id'];target_id = rows['duplicate'][1]['id']
    for damage in ['reservation', 'pid', 'scope', 'argv', 'completion', 'foreign-run', 'missing-original', 'receipt-kind', 'chain']:
        st = copy.deepcopy(baseline);target = L.find(st, target_id);original = L.find(st, original_id)
        if damage == 'reservation':target['reservation'] = 'forged'
        elif damage == 'pid':target['pid'] = 123
        elif damage == 'scope':target['scope'] = ['web']
        elif damage == 'argv':target['argv'] = ['true']
        elif damage == 'completion':original['execution_completion']['token'] = 'forged'
        elif damage == 'foreign-run':st['run_id'] = 'foreign-run'
        elif damage == 'missing-original':st['evidence']['entries'].remove(original)
        elif damage == 'receipt-kind':original['execution_receipt']['kind'] = 'execution-association-v1'
        else:original['execution_association'] = target['execution_association']
        assert R.validate_association(st, target), damage


def test_unknown_command_keeps_real_execution_without_new_reuse_claim(cli, repo, tmp_path):
    start(cli, repo);marker = tmp_path/'executed'
    code = 'from pathlib import Path;p=Path('+repr(str(marker))+');p.write_text(p.read_text()+"x" if p.exists() else "x")'
    two_gates(cli, tmp_path, [sys.executable, '-c', code])
    invoke(cli, 'gates', 'run', '--stage', 'candidate')
    st = json.loads(cli('show')[1]);rows = evidence_for_gates(st, {'boundary', 'duplicate'}, 'candidate')
    assert marker.read_text() == 'xx'
    for _, entry in rows.values():
        assert entry['status'] == 'PASS' and entry['pid']
        assert not entry.get('reuse') and not entry.get('execution_association') and not entry.get('execution_receipt')


@pytest.mark.parametrize('changed_input', ['unrelated', 'reader-input'])
def test_supported_reader_reuses_only_its_actual_complete_inputs(cli, repo, tmp_path, changed_input):
    reader_gates(cli, repo, tmp_path, anticipated=('svc','docs'))
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', 'boundary')
    before = json.loads(cli('show')[1])
    original = evidence_for_gates(before, {'boundary'}, 'candidate')['boundary'][1]
    receipt_bytes = Path(original['execution_receipt']['stored_path']).read_bytes()
    log_bytes = Path(original['log']).read_bytes()
    if changed_input == 'unrelated':
        (repo/'docs/readme.md').write_text('Updated unrelated presentation.\n')
    else:
        (repo/'svc/tests/fixtures/rows.json').write_text('[1]\n')
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', 'duplicate')
    current = json.loads(cli('show')[1]);target = evidence_for_gates(current, {'duplicate'}, 'candidate')['duplicate'][1]
    assert target['status'] == 'PASS'
    assert Path(original['execution_receipt']['stored_path']).read_bytes() == receipt_bytes
    assert Path(original['log']).read_bytes() == log_bytes
    if changed_input == 'unrelated':
        assert target['pid'] is None, 'unrelated content launched another reader process'
        assert A.read_json(target['execution_association'])['receipt_ref'] == original['execution_receipt']
        assert R.validate_association(current, target) == []
    else:
        assert target['pid'] and target['pid'] != original['pid']
        assert not target.get('execution_association') and target['execution_receipt']


@pytest.mark.parametrize('changed_input', ['unrelated-json', 'python-body'])
def test_module_ban_receipt_covers_its_python_sources_not_all_gate_scope(cli, repo, tmp_path, changed_input):
    reader_gates(cli, repo, tmp_path, anticipated=('svc','docs'))
    st = json.loads(cli('show')[1])
    gate = next(g for g in G.manifest(st)['gates'] if g['category'] == 'lint-imports' and g['argv'])
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', gate['id'])
    before = json.loads(cli('show')[1]);original = before['evidence']['entries'][-1]
    assert original['execution_receipt']
    if changed_input == 'unrelated-json':(repo/'svc/tests/fixtures/rows.json').write_text('[2]\n')
    else:(repo/'svc/app.py').write_text('VALUE = 22\n')
    invoke(cli, 'gates', 'run', '--stage', 'candidate', '--id', gate['id'])
    current = json.loads(cli('show')[1]);target = current['evidence']['entries'][-1]
    assert target['status'] == 'PASS'
    assert bool(target.get('execution_association')) == (changed_input == 'unrelated-json')
    assert (target['pid'] is None) == (changed_input == 'unrelated-json')
    if changed_input == 'unrelated-json':assert R.validate_association(current, target) == []


def test_module_ban_selector_observes_added_and_deleted_python_members(cli, repo, tmp_path):
    import al_verification_prepare as P
    import al_verification_scope as Scope
    reader_gates(cli, repo, tmp_path, anticipated=('svc',))
    st = json.loads(cli('show')[1]);gate = next(g for g in G.manifest(st)['gates'] if g['category'] == 'lint-imports' and g['argv'])
    preparation = P.prepare(st, gate=gate['id'], stage='candidate');profile = preparation['command_profiles'][0]
    first = Scope.source_binding(st, profile)
    new = repo/'svc/new_module.py';new.write_text('VALUE = 4\n')
    added = Scope.source_binding(st, profile)
    assert added != first and 'svc/new_module.py' in added['membership']
    new.unlink();assert Scope.source_binding(st, profile) == first
