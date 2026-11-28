"""Checks AL cannot establish or set up are unverified and listed, never blockers (#157)."""
import copy
import json

import pytest

import al_service_coverage as Coverage
from al_check_setup import UNQUALIFIED_NOTE, SetupError, unqualified
from al_checkout_paths import MaterializationError
from test_package_checks import js
from test_service_coverage import discover, start


@pytest.mark.parametrize('code', ['missing-check', 'missing-verification-check', 'missing-manifest',
                                  'package-semantic-unverified', 'package-execution-pending',
                                  'unclassified-unit', 'unknown-ci-inventory', 'unsupported-ci-provider'])
def test_a_check_al_cannot_establish_does_not_block(code):
    gap = {'code': code, 'unit': 'apps/web', 'path': 'apps/web', 'reason': 'no executable binding'}
    assert not Coverage.blocking(gap)
    assert Coverage.coverage_guidance([gap]) == Coverage.NEXT_COVERAGE_STEP


@pytest.mark.parametrize('code', ['torn-scan', 'torn-capture', 'base-symlink', 'contract-changed',
                                  'gitlink-boundary', 'symlink-boundary', 'opaque-boundary',
                                  'unsafe-boundary', 'required-rule-boundary', 'missing-input',
                                  'file-limit', 'unsupported-shared-language', 'a-future-gap-code'])
def test_every_other_gap_blocks_including_unknown_codes(code):
    # Integrity, visibility and unknown-impact gaps, and any code not listed as unverified (fail closed).
    gap = {'code': code, 'unit': None, 'path': 'vendor/child', 'reason': 'cannot be enumerated'}
    assert Coverage.blocking(gap)
    guidance = Coverage.coverage_guidance([gap])
    assert code + ' at path vendor/child' in guidance and 'rediscover over the same planned paths' in guidance


def test_in_scope_unverified_and_outside_reach_notes_are_both_listed():
    value = {'assessment': {
        'gaps': [{'code': 'missing-check', 'unit': 'apps/web', 'path': '.', 'reason': 'no binding'},
                 {'code': 'torn-scan', 'unit': None, 'path': '.', 'reason': 'moved'}],
        'coverage_notes': [{'code': 'missing-manifest', 'unit': 'services/x', 'path': 'services/x', 'reason': 'no manifest'}]}}
    notes = Coverage.notes(value)
    assert any(n.startswith('coverage unverified (in the change') and 'missing-check: apps/web' in n for n in notes)
    assert any(n.startswith('coverage note (outside') and 'services/x' in n for n in notes)
    # A blocking gap is never presented as a mere note.
    assert not any('torn-scan' in n for n in notes)


def test_only_policy_setup_refusals_are_unqualified():
    assert unqualified(SetupError('unqualified authored install lifecycle closure'))
    assert unqualified(SetupError('unsupported ambient npm configuration input'))
    # A real setup failure or an integrity refusal is not a policy refusal: it keeps blocking.
    assert not unqualified(SetupError('locked dependency setup failed with exit 1'))
    assert not unqualified(SetupError('locked setup sources differ from package profile'))
    assert not unqualified(MaterializationError('unqualified but not a setup refusal'))


def test_package_gate_whose_setup_al_cannot_qualify_is_unverified_not_blocking(cli, repo, tmp_path):
    import al_gates as G

    # An authored install lifecycle (`prepare`) is outside AL's qualified setup contract.
    js(repo, {'test': 'node --test', 'prepare': 'husky install'})
    start(cli)
    _, manifest = discover(cli, tmp_path, ['apps/web/src/app.js'])
    gate = next(g for g in manifest['gates'] if g.get('package_script', {}).get('script') == 'test')
    cli('gates', 'run', '--stage', 'base', '--id', gate['id'])
    cli('gates', 'run', '--stage', 'candidate', '--id', gate['id'])
    state = json.loads(cli('show')[1])
    entry = next(e for e in reversed(state['evidence']['entries']) if e['area'] == 'gate-base-' + gate['id'])
    assert entry['status'] == 'INFRA'
    assert entry['note'].startswith(UNQUALIFIED_NOTE) and 'authored install lifecycle' in entry['note']
    status = json.loads(cli('gates', 'status')[1])
    assert not any(gate['id'] in gap for gap in status['base']), status['base']
    assert not any(gate['id'] in gap for gap in status['candidate']), status['candidate']
    rows = {row['stage']: row for row in status['unverified'] if row['gate'] == gate['id']}
    assert set(rows) == {'base', 'candidate'} and all(r['verified_by'] == 'not verified' for r in rows.values())
    # The same INFRA from any other materialization refusal still requires passing evidence.
    m = G.manifest(state)
    other = copy.deepcopy(state)
    next(e for e in reversed(other['evidence']['entries']) if e['id'] == entry['id'])['note'] = 'rejected base materialization'
    assert G.gate_result(other, m, gate, 'base', m['base_oid']) not in G.ACCEPTED
    assert G.gate_result(state, m, gate, 'base', m['base_oid']) == 'unverified'


def test_a_changed_setup_input_or_environment_asks_for_a_new_attempt(cli, repo, tmp_path, monkeypatch):
    import al_gates as G
    import al_store

    js(repo, {'test': 'node --test', 'prepare': 'husky install'})
    start(cli)
    _, manifest = discover(cli, tmp_path, ['apps/web/src/app.js'])
    gate = next(g for g in manifest['gates'] if g.get('package_script', {}).get('script') == 'test')
    cli('gates', 'run', '--stage', 'base', '--id', gate['id'])
    state = json.loads(cli('show')[1])
    m = G.manifest(state)
    assert G.gate_result(state, m, gate, 'base', m['base_oid']) == 'unverified'
    monkeypatch.setenv('npm_config_ignore_scripts', 'true')  # the package-manager environment moved
    assert G.gate_result(state, m, gate, 'base', m['base_oid']) == 'stale'
    monkeypatch.delenv('npm_config_ignore_scripts')
    (al_store.REPO_ROOT / 'apps/web/.npmrc').write_text('ignore-scripts=true\n')  # a setup file moved
    assert G.gate_result(state, m, gate, 'base', m['base_oid']) == 'stale'


def test_setup_the_change_introduces_keeps_blocking(cli, repo, tmp_path):
    from test_service_coverage import put

    js(repo, {'test': 'node --test'})
    # The change itself adds the authored install lifecycle AL cannot qualify.
    package = json.loads((repo / 'apps/web/package.json').read_text())
    package['scripts']['postinstall'] = 'node scripts/setup.js'
    put(repo, 'apps/web/package.json', json.dumps(package))
    start(cli)
    _, manifest = discover(cli, tmp_path, ['apps/web/package.json'])
    gate = next(g for g in manifest['gates'] if g.get('package_script', {}).get('script') == 'test')
    cli('gates', 'run', '--stage', 'base', '--id', gate['id'])
    state = json.loads(cli('show')[1])
    entry = next(e for e in reversed(state['evidence']['entries']) if e['area'] == 'gate-base-' + gate['id'])
    assert entry['status'] == 'INFRA' and not entry['note'].startswith(UNQUALIFIED_NOTE)
    assert 'setup the change edits is not qualified by AL (apps/web/package.json)' in entry['note']
    status = json.loads(cli('gates', 'status')[1])
    assert any(gate['id'] in gap for gap in status['base'])
    assert not any(row['gate'] == gate['id'] for row in status['unverified'])


def test_an_unverified_candidate_result_is_bound_to_its_candidate(cli, repo, tmp_path):
    import al_gates as G
    import al_scope as S
    import al_store

    js(repo, {'test': 'node --test', 'prepare': 'husky install'})
    start(cli)
    _, manifest = discover(cli, tmp_path, ['apps/web/src/app.js'])
    gate = next(g for g in manifest['gates'] if g.get('package_script', {}).get('script') == 'test')
    cli('gates', 'run', '--stage', 'candidate', '--id', gate['id'])
    state = json.loads(cli('show')[1])
    m = G.manifest(state)
    assert G.gate_result(state, m, gate, 'candidate', S.capture_candidate(state)['fingerprint']) == 'unverified'
    (al_store.REPO_ROOT / 'apps/web/src/app.js').write_text('module.exports = 2;\n')
    # A different candidate is a different execution: never the earlier unverified row.
    assert G.gate_result(state, m, gate, 'candidate', S.capture_candidate(state)['fingerprint']) != 'unverified'
