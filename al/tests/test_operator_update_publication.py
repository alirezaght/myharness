"""Operator update publication invariants."""

import pytest

from al.tests.operator_update_helpers import (
    U,
    _prefix_cache as _prefix_cache,
    artifact,
    enrolled as enrolled,
    inject,
    json,
    load_record,
    package_path,
    public,
    read_json,
    resolve_target,
    retained,
    tag,
    write_json,
)


@pytest.mark.parametrize('failure', ['late-postimage', 'smoke', 'publication'])
def test_completed_publication_failures_never_claim_success(enrolled, failure):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    if failure == 'late-postimage':
        inject(enrolled, [f"            if platform == 'claude': Path({str(package_path(target, 'codex') / 'SKILL.md')!r}).write_text('changed before publication')"])
    elif failure == 'smoke':
        inject(enrolled, ["        raise ValueError('injected smoke failure')"], "        journal['verification'] = {'smoke': smoke_installation(target, candidate['roles']), 'activity': activity}\n", after=False)
    else:
        inject(enrolled, ["        raise ValueError('injected publication failure')"], '        J.publish_completed(pending, candidate, journal, path, state_root)\n', after=False)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    current = load_record(w['state'], target['target_id'])
    assert current['pending'] and current['current'] == row['current']
    assert retained(enrolled) == before
    journal = read_json(w['state'] / 'installer/operations' / current['pending'] / 'journal.json')
    assert journal['status'] == 'FAILED' and 'manual reconciliation' in journal['recovery']


def test_partial_failure_keeps_pending_manual_reconciliation(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    inject(enrolled, ["            if platform == 'claude': raise OSError('injected second-platform failure')"], after=False)
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1 and 'injected second-platform' in result.data['error']
    current = load_record(w['state'], target['target_id'])
    assert current['pending'] and current['current'] == row['current']
    journal = read_json(w['state'] / 'installer/operations' / current['pending'] / 'journal.json')
    assert journal['status'] == 'FAILED' and 'manual reconciliation' in journal['recovery']
    assert journal['engine_steps'][0]['status'] == 'COMPLETE'
    assert retained(enrolled) == before
    refused = public(enrolled, req, '--allow-running')
    assert refused.returncode == 1 and 'unresolved' in refused.data['error']


@pytest.mark.parametrize('overlap', [False, True])
def test_unsafe_history_discovery_warns_but_actual_payload_overlap_refuses(enrolled, overlap):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    link = w['root'] / 'history-link'
    link.symlink_to(package_path(target, 'codex') / 'reference' if overlap else runtime)
    req['runtime_roots'].append(str(link))
    result = public(enrolled, req, '--allow-running')
    if overlap:
        assert result.returncode == 1 and 'overlaps protected' in result.data['error']
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'runtime-root-unreadable-or-unsafe' in result.data['activity']['gaps']


def test_observation_limits_are_explicit_and_do_not_print_state_prose(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    for index in range(5):
        write_json(runtime / (str(index) + '.json'), {'schema_version': 9, 'spec': 'PRIVATE PROSE'})
    monkeypatch.setattr(U, 'SCAN_LIMIT', 2)
    report = U.observe(tmp_path / 'state', [], resolve_target(home=tmp_path), [str(runtime)], str(tmp_path))
    assert report['counts'] == {'unsupported': 2}
    assert 'metadata-enumeration-truncated' in report['gaps']
    assert 'PRIVATE PROSE' not in json.dumps(report)


def test_deeply_malformed_history_is_a_bounded_warning(tmp_path):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    (runtime / 'deep.json').write_text('[' * 100000 + ']' * 100000)
    report = U.observe(tmp_path / 'state', [], resolve_target(home=tmp_path), [str(runtime)], str(tmp_path))
    assert report['counts']['unreadable-or-malformed'] == 1
