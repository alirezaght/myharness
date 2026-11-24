"""COMPLEX requires a named hard part: an algorithm, an invariant or a protocol (#30)."""
import json

import pytest
import al_task_risk as Risk


def _start(cli):
    assert cli('start', '--spec', 'Change account withdrawals across several services safely.')[0] == 0
    code, out, _ = cli('spec-gate', 'SPEC_READY')
    assert code == 0 and '--hard-part algorithm|invariant|protocol' in out


def test_complex_without_a_named_hard_part_is_refused(cli, state_file):
    _start(cli)
    before = state_file().read_bytes()
    code, out, err = cli('classify', 'COMPLEX', '--designer', 'yes', '--implementers', 'al-engineer')
    assert code != 0 and 'named hard part' in str(code) + out + err
    code, out, err = cli('classify', 'COMPLEX', '--designer', 'yes', '--implementers', 'al-engineer',
                         '--reason', 'Touches 7 services and fixes 12 defects found earlier.')
    assert code != 0
    code, out, err = cli('classify', 'COMPLEX', '--designer', 'yes', '--implementers', 'al-engineer',
                         '--hard-part', 'invariant: 7 services')
    assert code != 0 and 'breadth' in str(code) + out + err
    assert state_file().read_bytes() == before


def test_complex_records_its_named_hard_part(cli, state_file):
    _start(cli)
    code, out, err = cli('classify', 'COMPLEX', '--designer', 'yes', '--implementers', 'al-engineer',
                         '--hard-part', 'invariant: balance never goes negative under concurrent withdrawals')
    assert code == 0, (out, err)
    assert 'Hard part: invariant: balance never goes negative' in out
    st = json.loads(state_file().read_bytes())
    assert st['classification_hard_parts'] == [{'kind': 'invariant', 'name': 'balance never goes negative under concurrent withdrawals'}]


def test_reason_prefix_names_the_hard_part(cli, state_file):
    _start(cli)
    code, out, err = cli('classify', 'COMPLEX', '--designer', 'yes', '--implementers', 'al-engineer',
                         '--reason', 'protocol: two-phase reservation handshake between ledger and wallet')
    assert code == 0, (out, err)
    assert json.loads(state_file().read_bytes())['classification_hard_parts'][0]['kind'] == 'protocol'


@pytest.mark.parametrize('text,expected', [
    ('Touches many files across multiple services', True),
    ('Twelve defects were found; 5 findings remain', True),
    ('3 defects in the retry backoff algorithm', False),
    ('Keeps exactly-once delivery across 4 services', False),
])
def test_breadth_only(text, expected):
    assert Risk.breadth_only(text) is expected


def test_assessment_hard_part_that_argues_from_breadth_is_refused(repo):
    import hashlib
    value = {'schema_version': 1, 'scope': 'cross_component', 'existing_pattern': False, 'behavior_clear': True,
             'hard_parts': [{'kind': 'domain', 'new_design': True, 'description': 'Changes span 9 services and 40 files.'}],
             'pseudocode_benefit': 'Pseudocode would order the edits across every service.',
             'evidence': [{'path': 'svc/app.py', 'sha256': hashlib.sha256((repo / 'svc/app.py').read_bytes()).hexdigest(),
                           'reason': 'Existing entry point.'}],
             'reason': 'The change reaches many services in one release.'}
    with pytest.raises(ValueError, match='breadth'):
        Risk.assess(value, repo)
