"""Amendments explain invalidation; repeated routing never invents new authority."""
import json


def ok(cli, *args):
    result = cli(*args)
    assert result[0] == 0, result
    return result


def test_amendment_reports_gate_reset_and_classification_is_idempotent(cli, tmp_path, state_file):
    ok(cli, 'start', '--spec', 'Clarify the local implementation while retaining approved routing history.')
    ok(cli, 'spec-gate', 'SPEC_READY')
    route = ('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')
    ok(cli, *route, '--reason', 'Existing implementation pattern is sufficient.')
    for number in (1, 2):
        path = tmp_path / f'amend-{number}.json'
        path.write_text(json.dumps({'id': f'AM-{number}', 'source': 'explicit user clarification',
                                    'text': f'Retain the existing behavior and clarify acceptance detail number {number}.'}))
        result = ok(cli, 'task', 'amend', '--file', str(path))
        assert 'Specification gate reset' in result[1]
        assert 'spec-gate SPEC_READY' in result[1]
        before = state_file().read_bytes()
        amended = json.loads(before)
        assert amended['spec_status'] is None
        assert amended['classification'] == 'STANDARD'
        assert amended['proof_admission']['status'] == 'pending'
        result = ok(cli, *route)
        assert 'classification unchanged' in result[1]
        assert 'Specification gate pending' in result[1]
        assert state_file().read_bytes() == before
        ok(cli, 'spec-gate', 'SPEC_READY')


def test_identical_routing_after_planning_is_a_readonly_noop(cli, tmp_path, state_file):
    from evidence_helpers import plan_na_gates

    ok(cli, 'start', '--spec', 'Implement a small scoped change using the existing service behavior.')
    ok(cli, 'spec-gate', 'SPEC_READY')
    route = ('classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    ok(cli, *route)
    plan_na_gates(cli, tmp_path, ('svc/app.py',))
    ok(cli, 'phase', 'IMPLEMENTATION')
    before = state_file().read_bytes()
    assert 'classification unchanged' in ok(cli, *route)[1]
    assert state_file().read_bytes() == before
    result = cli('classify', 'COMPLEX', '--designer', 'yes', '--implementers', 'al-engineer', '--hard-part', 'invariant: amendments keep earlier feedback')
    assert result[0] != 0 and 'only change during planning' in ''.join(map(str, result))
    assert state_file().read_bytes() == before
