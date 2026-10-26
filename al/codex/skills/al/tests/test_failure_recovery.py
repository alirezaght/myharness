"""Service errors cannot silently consume implementation retries or request fake approval."""
import json

import pytest
from hook_regression_helpers import invoke


def state(cli):
    return json.loads(invoke(cli, 'show', '--json')[1])


@pytest.mark.parametrize('reason', [
    'Selected model is at capacity. Please try a different model.',
    'model service overloaded: 429',
    'model rate-limit exceeded',
    'unknown model for spawn_agent',
    'agent type is currently not available',
])
def test_service_error_rejected_without_failure_or_budget_mutation(cli, state_file, reason):
    invoke(cli, 'start', '--spec', 'Separate provider service failures from implementation retries.')
    before = state_file().read_bytes()
    code, out, err = invoke(cli, 'agent-failed', 'al-engineer', '--reason', reason,
                            '--retry-safe', 'yes', ok=False)
    assert code != 0 and 'implementation failure' in str(code) + out + err
    assert state_file().read_bytes() == before


def test_capacity_has_one_same_model_retry_and_independent_implementation_budget(cli, tmp_path, state_file):
    invoke(cli, 'start', '--spec', 'Model availability and implementation have independent budgets.')
    proof = tmp_path / 'reconciled.md'
    proof.write_text('Source stable; no writers or checks running; no partial agent edits.')
    reason = 'Selected model is at capacity. Please try a different model.'
    before = state(cli)
    invoke(cli, 'model-retry', 'al-engineer', '--reason', reason, '--artifact', str(proof))
    current = state(cli)
    assert current['retries'] == before['retries']
    assert current['model_tier'] == before['model_tier']
    assert len(current['model_retry_history']) == 1
    raw = state_file().read_bytes()
    invoke(cli, 'model-retry', 'al-engineer', '--reason', reason, '--artifact', str(proof), ok=False)
    assert state_file().read_bytes() == raw
    invoke(cli, 'agent-failed', 'al-engineer', '--reason', 'transient runner error', '--retry-safe', 'yes')
    invoke(cli, 'retry', 'al-engineer', '--reason', 'reconciled safe implementation retry')
    assert state(cli)['retries']['al-engineer'] == 1


@pytest.mark.parametrize('reason,kind,phrase', [
    ('This content was flagged for possible cybersecurity risk.', 'model-refusal', 'No user approval is pending'),
    ('Operation not permitted by the filesystem sandbox', 'permission', 'concrete action'),
    ('Automatic approval review rejected this operation', 'permission', 'permission source'),
])
def test_nonimplementation_failure_explains_recovery_and_disallows_retry_or_fallback(cli, state_file, reason, kind, phrase):
    invoke(cli, 'start', '--spec', 'Explain real permission and refusal states accurately.')
    code, out, err = invoke(cli, 'agent-failed', 'al-engineer', '--reason', reason, '--retry-safe', 'yes')
    assert kind in out and phrase in out
    failure = state(cli)['failure']
    assert failure['kind'] == kind and failure['retry_safe'] == 'no'
    before = state_file().read_bytes()
    for command in (
        ('retry', 'al-engineer', '--reason', 'try again'),
        ('model-unavailable', 'astra', '--reason', reason),
    ):
        invoke(cli, *command, ok=False)
        assert state_file().read_bytes() == before


def test_explicit_kind_cannot_relabel_refusal_as_capacity(cli, tmp_path, state_file):
    invoke(cli, 'start', '--spec', 'Never relabel a model refusal as availability.')
    proof = tmp_path / 'proof.md'
    proof.write_text('No work running.')
    before = state_file().read_bytes()
    invoke(cli, 'model-retry', 'al-engineer', '--kind', 'model-capacity', '--reason',
           'This content was flagged for possible cybersecurity risk.', '--artifact', str(proof), ok=False)
    assert state_file().read_bytes() == before


def test_first_capacity_failure_cannot_trigger_fallback(cli, state_file):
    invoke(cli, 'start', '--spec', 'Capacity needs one same-model retry before fallback.')
    before = state_file().read_bytes()
    invoke(cli, 'model-unavailable', 'astra', '--reason', 'Selected model is at capacity.', ok=False)
    assert state_file().read_bytes() == before
