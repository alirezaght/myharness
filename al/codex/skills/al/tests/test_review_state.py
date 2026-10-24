"""Declared review-hook state under `.git` is isolated, candidate-bound and audited."""
import base64
import copy
import json
import sys

import pytest

import al_artifacts as A
import al_review_state as ReviewState
from hook_regression_helpers import invoke
from test_hook_batch6 import _manifest, _source

WRITE = ("import os,sys;target=sys.argv[1];os.makedirs(os.path.dirname(target),exist_ok=True);"
         "open(target,'w').write('{\"verdict\":\"approved\"}')")


def started(cli):
    invoke(cli, 'start', '--spec', 'Run the repository review hook as a gate and keep its state.')
    invoke(cli, 'spec-gate', 'SPEC_READY');invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    return json.loads(cli('show')[1])


def planned(cli, repo, tmp_path, target, declaration):
    state = started(cli)
    argv = [sys.executable, '-c', WRITE, target] if target else [sys.executable, '-c', 'print("review hook wrote nothing")']
    m = _manifest(state, source=_source(repo, 'svc/app.py'), active='loc', argv=argv)
    gate = next(g for g in m['gates'] if g['id'] == 'loc')
    if declaration is not None:gate['review_state'] = declaration
    path = tmp_path / 'manifest.json';path.write_text(json.dumps(m))
    return path


def base_entry(cli):
    state = json.loads(cli('show')[1])
    run = next(r for r in state['gate_runs'] if r['gate'] == 'loc' and r['stage'] == 'base')
    return state, next(e for e in state['evidence']['entries'] if e['id'] == run['evidence'])


def test_declared_review_state_is_captured_bound_and_removed(cli, repo, tmp_path):
    invoke(cli, 'gates', 'plan', '--file', str(planned(cli, repo, tmp_path, '.git/codex-review/verdict.json', {'path': '.git/codex-review'})))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    state, entry = base_entry(cli)
    assert entry['status'] == 'PASS', entry.get('note')
    review = entry['review_state']
    assert review['path'] == '.git/codex-review' and review['candidate'] == state['identity']['merge_base']
    bundle = json.loads(A.read(review['artifact']))
    assert base64.b64decode(bundle['contents']['verdict.json']) == b'{"verdict":"approved"}'
    assert bundle['run'] == state['run_id'] and bundle['gate'] == 'loc' and bundle['invocation'] == entry['invocation']
    import al_gates
    assert not [g for g in al_gates.base_gaps(state) if 'loc' in g]
    # The source repository's own metadata never receives review state.
    assert not (repo / '.git/codex-review').exists()


def test_an_undeclared_single_hook_state_location_is_removed_and_noted(cli, repo, tmp_path):
    invoke(cli, 'gates', 'plan', '--file', str(planned(cli, repo, tmp_path, '.git/codex-review/verdict.json', None)))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    _, entry = base_entry(cli)
    assert entry['status'] == 'PASS' and 'hook state .git/codex-review removed' in entry['note']


@pytest.mark.parametrize('target,declaration', [
    ('.git/other-tool/verdict.json', {'path': '.git/codex-review'}),
])
def test_undeclared_metadata_writes_still_fail_scratch_integrity(cli, repo, tmp_path, target, declaration):
    invoke(cli, 'gates', 'plan', '--file', str(planned(cli, repo, tmp_path, target, declaration)))
    invoke(cli, 'gates', 'run', '--stage', 'base', ok=False)
    _, entry = base_entry(cli)
    assert entry['status'] != 'PASS'


@pytest.mark.parametrize('declaration', [{'path': '.git/objects'}, {'path': '.git/hooks/x'}, {'path': 'state'},
                                         {'path': '.git/../escape'}, {'path': '.git/review', 'extra': 1}, {'path': '.git'}])
def test_invalid_review_state_declarations_are_refused(cli, repo, tmp_path, state_file, declaration):
    path = planned(cli, repo, tmp_path, '.git/codex-review/verdict.json', declaration)
    before = state_file().read_bytes()
    invoke(cli, 'gates', 'plan', '--file', str(path), ok=False)
    assert state_file().read_bytes() == before


def test_stale_foreign_or_altered_review_state_cannot_satisfy_a_gate(cli, repo, tmp_path):
    invoke(cli, 'gates', 'plan', '--file', str(planned(cli, repo, tmp_path, '.git/codex-review/verdict.json', {'path': '.git/codex-review'})))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    state, entry = base_entry(cli)
    gate = {'id': 'loc', 'review_state': {'path': '.git/codex-review'}}
    fingerprint = state['identity']['merge_base']
    assert ReviewState.gaps(state, gate, entry, fingerprint) == []
    assert ReviewState.gaps(state, gate, entry, '0' * 40) == ['stale review state for loc']
    assert ReviewState.gaps(state, dict(gate, id='other'), entry, fingerprint)
    assert ReviewState.gaps(state, {'id': 'loc'}, entry, fingerprint)
    missing = {k: v for k, v in entry.items() if k != 'review_state'}
    assert ReviewState.gaps(state, gate, missing, fingerprint) == ['missing review state for loc']
    forged = copy.deepcopy(entry);forged['review_state']['files']['verdict.json']['sha256'] = 'f' * 64
    assert ReviewState.gaps(state, gate, forged, fingerprint)
    from pathlib import Path
    Path(entry['review_state']['artifact']['stored_path']).write_text('{}')
    assert ReviewState.gaps(state, gate, entry, fingerprint) == ['unreadable review state artifact for loc']


def test_absent_output_is_recorded_and_links_are_refused(cli, repo, tmp_path):
    invoke(cli, 'gates', 'plan', '--file', str(planned(cli, repo, tmp_path, None, {'path': '.git/codex-review'})))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    state, entry = base_entry(cli)
    bundle = json.loads(A.read(entry['review_state']['artifact']))
    assert bundle['present'] is False and bundle['files'] == {} and bundle['reservation'] == entry['reservation']
    import os
    root = tmp_path / 'scratch';(root / '.git/codex-review').mkdir(parents=True)
    (root / 'outside').write_text('x');os.link(root / 'outside', root / '.git/codex-review/linked')
    from al_checkout_paths import MaterializationError
    with pytest.raises(MaterializationError):
        ReviewState.capture(root, {'path': '.git/codex-review'})


def test_capture_ignores_access_time_and_refuses_intermediate_links(tmp_path):
    import os
    import time
    root = tmp_path / 'scratch';state = root / '.git/codex-review/nested'
    state.mkdir(parents=True);(state / 'verdict.json').write_text('{}')
    old = time.time() - 3600
    os.utime(state / 'verdict.json', (old - 100, old))  # an access-time update on read must not refuse
    captured = ReviewState.capture(root, {'path': '.git/codex-review'})
    assert captured['files']['nested/verdict.json'][0] == b'{}'
    elsewhere = tmp_path / 'elsewhere';elsewhere.mkdir()
    (root / '.git/linked').mkdir();(root / '.git/linked/a').symlink_to(elsewhere, target_is_directory=True)
    from al_checkout_paths import MaterializationError
    with pytest.raises(MaterializationError):
        ReviewState.capture(root, {'path': '.git/linked/a'})


@pytest.mark.parametrize('name', ['REMOTES', 'rr-cache', 'Refs', 'sharedindex.abc'])
def test_git_metadata_names_are_reserved_case_insensitively(name):
    with pytest.raises(ValueError):
        ReviewState.validate_declaration({'path': '.git/' + name})
