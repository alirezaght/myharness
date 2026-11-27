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
