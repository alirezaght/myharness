"""`gates plan` verifies the capture discovery recorded instead of re-analysing (#25)."""
import json

import al_gate_inventory as V
from hook_regression_helpers import invoke
from test_discovery_reuse import _count_attempts, _fixture, _paths
from test_service_coverage import put, start


def _discover_then_plan(cli, tmp_path, monkeypatch):
    calls = _count_attempts(monkeypatch, only_discover=False)
    out = tmp_path / 'manifest.json'
    invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['services/a/src/app.py'])), '--output', str(out))
    discovered = len(calls)
    return calls, discovered, out


def test_plan_after_discover_verifies_instead_of_rediscovering(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    start(cli)
    calls, discovered, out = _discover_then_plan(cli, tmp_path, monkeypatch)
    planned = tmp_path / 'planned.json'
    invoke(cli, 'gates', 'plan', '--file', str(out), '--output', str(planned))
    assert len(calls) == discovered, 'plan re-observed the recorded capture instead of a new inventory'
    # The planned manifest is the discovered one, as its next revision.
    drop = lambda m: {k: v for k, v in m.items() if k != 'revision'}
    assert drop(json.loads(planned.read_bytes())) == drop(json.loads(out.read_bytes()))


def test_a_changed_input_after_discover_makes_plan_reanalyse(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    start(cli)
    calls, discovered, out = _discover_then_plan(cli, tmp_path, monkeypatch)
    put(repo, 'services/b/src/app.py', 'import common\nCHANGED = 1\n')
    result = cli('gates', 'plan', '--file', str(out))
    assert len(calls) == discovered + 1, 'a changed source is drift: plan computed a fresh inventory'
    assert result[0] in (0, None) or 'incomplete repository gate inventory' in str(result)


def test_disabled_store_always_reanalyses(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    start(cli)
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    calls, discovered, out = _discover_then_plan(cli, tmp_path, monkeypatch)
    invoke(cli, 'gates', 'plan', '--file', str(out))
    assert len(calls) == discovered + 1


def test_a_forged_verification_record_is_never_trusted(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    start(cli)
    calls, discovered, out = _discover_then_plan(cli, tmp_path, monkeypatch)
    import al_store
    records = list((al_store.RUNTIME_DIR / 'inventory-verifications').glob('*.json'))
    assert records
    for record in records:
        value = json.loads(record.read_text())
        value['observations']['files'] = {p: {**r, 'sha256': '0' * 64} for p, r in value['observations']['files'].items()}
        value['observations']['capture_started_ns'] = 0  # every source re-read and re-hashed
        record.write_text(json.dumps(value))
    invoke(cli, 'gates', 'plan', '--file', str(out))
    assert len(calls) == discovered + 1
    assert V._OBSERVED  # the fresh inventory recorded its own capture
