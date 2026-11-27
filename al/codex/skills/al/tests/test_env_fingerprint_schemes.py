"""An issuing runtime's evidence is judged by the environment fingerprint scheme that recorded it (#24).

Released AL up to v0.1.70 hashed only the declared names; v0.1.71 added CI; current AL also binds the
managed execution policy. Current-policy readiness and terminal source proofs run current code on an
old issuer's records, so they evaluate those records with `issuer_gaps`.
"""
import hashlib
import json
import os

import pytest
import al_ledger as Ledger
from evidence_helpers import ok_cmd


def _released(names, *, with_ci):
    """Independent copy of the released algorithms (empty scope): sha256 of each value, or 'unset'."""
    h = hashlib.sha256(json.dumps([]).encode())
    for name in sorted(set(names) | ({'CI'} if with_ci else set())):
        value = os.environ.get(name)
        digest = hashlib.sha256(value.encode()).hexdigest() if value is not None else 'unset'
        h.update(f'\0env:{name}={digest}'.encode())
    return h.hexdigest()[:16]


def _state(state_file):
    return json.loads(state_file().read_text())


def _record(cli, area, scheme):
    """Real planned broad evidence, executed while fingerprints use `scheme` (as that issuer would)."""
    with Ledger.env_scheme(scheme):
        assert cli('evidence', 'plan', '--area', area, '--tier', 'broad', '--scope', 'svc',
                   '--env', 'FIXTURE_TOKEN', '--', *ok_cmd('1 passed'))[0] == 0
        assert cli('evidence', 'run', '--area', area)[0] == 0


def _gaps(state_file, areas):
    return lambda: [gap for gap in Ledger.gaps(_state(state_file), 'broad') if gap[0] in areas]


def test_historical_schemes_reproduce_released_issuer_fingerprints(repo, monkeypatch):
    monkeypatch.setenv('FIXTURE_TOKEN', 'one')
    monkeypatch.delenv('CI', raising=False)
    names = ['FIXTURE_TOKEN']
    with Ledger.env_scheme('declared'):
        assert Ledger.scope_fingerprint([], names) == _released(names, with_ci=False)
    with Ledger.env_scheme('defaults'):
        assert Ledger.scope_fingerprint([], names) == _released(names, with_ci=True)
    current = Ledger.scope_fingerprint([], names)
    assert current not in {_released(names, with_ci=False), _released(names, with_ci=True)}
    with pytest.raises(ValueError, match='unknown environment fingerprint scheme'):
        with Ledger.env_scheme('future'):
            pass
    assert Ledger.scope_fingerprint([], names) == current


@pytest.mark.parametrize('scheme', ['declared', 'defaults'])
def test_old_issuer_evidence_is_current_under_its_own_scheme_only(cli, run_state, state_file, monkeypatch, scheme):
    run_state('implemented')
    monkeypatch.setenv('FIXTURE_TOKEN', 'one')
    _record(cli, 'issued', scheme)
    gaps = _gaps(state_file, {'issued'})
    # Current code alone cannot reproduce the issuer's fingerprint; the issuer's own scheme can.
    assert gaps() == [('issued', 'STALE')]
    assert Ledger.issuer_gaps(gaps) == []
    # The issuer's scheme still binds the declared value it recorded.
    monkeypatch.setenv('FIXTURE_TOKEN', 'two')
    assert Ledger.issuer_gaps(gaps) == [('issued', 'STALE')]


def test_current_evidence_is_never_downgraded_to_an_older_scheme(cli, run_state, state_file, monkeypatch):
    run_state('implemented')
    monkeypatch.setenv('FIXTURE_TOKEN', 'one')
    monkeypatch.delenv('CI', raising=False)
    _record(cli, 'current', 'policy')
    gaps = _gaps(state_file, {'current'})
    assert gaps() == [] and Ledger.issuer_gaps(gaps) == []
    # CI is unbound by the 'declared' scheme, but current records were not made with it.
    monkeypatch.setenv('CI', '1')
    assert gaps() == [('current', 'STALE')]
    assert Ledger.issuer_gaps(gaps) == [('current', 'STALE')]


def test_one_issuer_scheme_must_reproduce_every_record(cli, run_state, state_file, monkeypatch):
    run_state('implemented')
    monkeypatch.setenv('FIXTURE_TOKEN', 'one')
    _record(cli, 'declared-area', 'declared')
    _record(cli, 'defaults-area', 'defaults')
    gaps = _gaps(state_file, {'declared-area', 'defaults-area'})
    assert sorted(gaps()) == [('declared-area', 'STALE'), ('defaults-area', 'STALE')]
    # No single runtime records with two schemes; the current gaps are reported.
    assert sorted(Ledger.issuer_gaps(gaps)) == [('declared-area', 'STALE'), ('defaults-area', 'STALE')]


def test_one_observation_keeps_each_scheme_apart(cli, run_state, state_file, monkeypatch):
    """The validators run every scheme inside one observation; cached fingerprints must not cross."""
    from al_analysis_cache import ObservationContext
    run_state('implemented')
    monkeypatch.setenv('FIXTURE_TOKEN', 'one')
    _record(cli, 'issued', 'defaults')
    gaps = _gaps(state_file, {'issued'})
    with ObservationContext({'test': 'one validator observation across schemes'}):
        assert gaps() == [('issued', 'STALE')]
        assert Ledger.issuer_gaps(gaps) == []
        assert gaps() == [('issued', 'STALE')]


def test_changed_execution_policy_is_not_excused_by_an_older_scheme(cli, run_state, state_file, monkeypatch):
    import al_verification_policy as Policy
    run_state('implemented')
    monkeypatch.setenv('FIXTURE_TOKEN', 'one')
    _record(cli, 'current', 'policy')
    gaps = _gaps(state_file, {'current'})
    before = Policy.execution_policy()
    monkeypatch.setattr(Policy, 'execution_policy', lambda: {**before, 'version': before['version'] + 1})
    assert gaps() == [('current', 'STALE')]
    assert Ledger.issuer_gaps(gaps) == [('current', 'STALE')]


@pytest.mark.parametrize('scheme', ['declared', 'defaults'])
def test_recorded_execution_inputs_follow_the_issuer_scheme(repo, monkeypatch, scheme):
    """Reused-result and projection policies bind execution inputs; v0.1.70 never bound CI."""
    import al_verification_inputs as Inputs
    from al_environment_observation import env_scheme
    monkeypatch.setenv('FIXTURE_TOKEN', 'one')
    monkeypatch.delenv('CI', raising=False)
    with env_scheme(scheme):
        recorded = {'kind': 'declared-execution-v1', 'declared_names': ['FIXTURE_TOKEN'],
                    'inputs': Inputs.execution_environment(['FIXTURE_TOKEN'])}
    assert ('CI' in recorded['inputs']) is (scheme != 'declared')
    assert Inputs.environment_gaps(recorded) == ([] if scheme == 'defaults' else ['execution environment inputs differ'])
    assert Ledger.issuer_gaps(lambda: Inputs.environment_gaps(recorded)) == []
    monkeypatch.setenv('FIXTURE_TOKEN', 'two')
    assert Ledger.issuer_gaps(lambda: Inputs.environment_gaps(recorded)) == ['execution environment inputs differ']


def test_current_scheme_inputs_are_the_frozen_release_contract():
    """A released scheme's inputs never change. If this fails, freeze the previous values as a new
    historical scheme in al_environment_observation (ENV_SCHEMES/HISTORICAL_DEFAULT_NAMES and the
    policy line in al_ledger._env_digest), then update these expectations."""
    import al_environment_observation as Observation
    import al_verification_policy as Policy
    assert Observation.ENV_SCHEMES == ('declared', 'defaults', 'policy')
    assert Observation.HISTORICAL_DEFAULT_NAMES == {'declared': frozenset(), 'defaults': frozenset({'CI'})}
    assert Observation.DEFAULT_VERIFICATION_NAMES == frozenset({'CI'})
    assert Policy.execution_policy() == {'version': 2, 'default_stdin': 'closed-pipe'}
