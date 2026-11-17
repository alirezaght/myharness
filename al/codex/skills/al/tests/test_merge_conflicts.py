"""Regular unmerged stages are observable repair inputs, never final candidates."""
import copy
import hashlib
import json
import subprocess

import pytest
import al_checkouts as C
import al_gitlinks as G
import al_scope as S
import al_task_attribution as T
from evidence_helpers import git, start_implementation


def head(repo):
    return S.git_bytes('rev-parse', 'HEAD', root=repo).decode().strip()


def blob(repo, body):
    return subprocess.run(['git', 'hash-object', '-w', '--stdin'], cwd=repo,
                          input=body, capture_output=True, check=True).stdout.decode().strip()


def stages(repo, path='svc/app.py', values=None):
    if values is None:
        values = [(1, '100644', blob(repo, b'base\n')),
                  (2, '100644', blob(repo, b'ours\n')),
                  (3, '100755', blob(repo, b'theirs\n'))]
    body = f'0 {"0" * 40}\t{path}\n' + ''.join(f'{mode} {oid} {stage}\t{path}\n' for stage, mode, oid in values)
    subprocess.run(['git', 'update-index', '--index-info'], cwd=repo, input=body.encode(), check=True)
    return values


def state(repo):
    return {'baseline_head': head(repo), 'review_mode': 'task',
            'identity': {'merge_base': head(repo), 'target_oid': head(repo)},
            'session_id': 'fixture', 'run_id': 'fixture'}


def index_bytes(repo):
    return (repo / '.git/index').read_bytes()


def test_all_stages_and_worktree_are_bound_without_index_writes(repo):
    expected = stages(repo)
    (repo / 'svc/app.py').write_bytes(b'conflict markers\n')
    before = index_bytes(repo)
    capture = S.capture_candidate(state(repo))
    row = next(r for r in capture['files'] if r['path'] == 'svc/app.py')
    assert row['index'] == {'stages': [dict(stage=s, mode=m, oid=o, sha256=hashlib.sha256(body).hexdigest())
        for (s, m, o), body in zip(expected, [b'base\n', b'ours\n', b'theirs\n'])]}
    assert row['worktree']['sha256'] == hashlib.sha256(b'conflict markers\n').hexdigest()
    assert index_bytes(repo) == before
    assert S.candidate_fingerprint(state(repo)) == capture['fingerprint']
    assert S.unresolved_index_gaps(capture) == ['unresolved index conflict: svc/app.py']


@pytest.mark.parametrize('change', ['oid', 'mode', 'presence', 'worktree'])
def test_any_conflict_input_changes_fingerprint(repo, change):
    values = stages(repo)
    st = state(repo)
    before = S.candidate_fingerprint(st)
    if change == 'oid': values[0] = (1, '100644', blob(repo, b'other base\n'))
    elif change == 'mode': values[0] = (1, '100755', values[0][2])
    elif change == 'presence': values.pop(0)
    else: (repo / 'svc/app.py').write_text('changed\n')
    stages(repo, values=values)
    assert S.candidate_fingerprint(st) != before


@pytest.mark.parametrize('missing', [1, 2, 3])
def test_missing_stages_remain_absent_and_deleted_worktree_supported(repo, missing):
    values = stages(repo)
    stages(repo, values=[v for v in values if v[0] != missing])
    (repo / 'svc/app.py').unlink()
    row = next(r for r in S.records(head(repo)) if r['path'] == 'svc/app.py')
    assert [r['stage'] for r in row['index']['stages']] == [s for s in [1, 2, 3] if s != missing]
    assert row['worktree'] == {'type': 'deleted'}


@pytest.mark.parametrize('shape', ['symlink-stage', 'gitlink-stage', 'symlink-worktree', 'directory-worktree', 'ancestor'])
def test_unsafe_or_ambiguous_conflicts_refuse(repo, shape):
    oid = blob(repo, b'normal\n')
    mode = {'symlink-stage': '120000', 'gitlink-stage': '160000', 'unknown-stage': '100600'}.get(shape, '100644')
    if shape == 'ancestor':
        (repo / 'alias').symlink_to(repo / 'svc', target_is_directory=True)
        stages(repo, path='alias/app.py', values=[(1, mode, oid)])
    else:
        stages(repo, values=[(1, mode, oid)])
        if shape.endswith('worktree'):
            (repo / 'svc/app.py').unlink()
            if shape == 'symlink-worktree': (repo / 'svc/app.py').symlink_to(repo / 'web/app.js')
            else: (repo / 'svc/app.py').mkdir()
    with pytest.raises(SystemExit): S.records(head(repo))


@pytest.mark.parametrize('stages_list', [[1, 1], [0, 1], [4], [-1]])
def test_invalid_stage_metadata_refused(stages_list):
    raw = b''.join(f'100644 {"a" * 40} {n}\tx\0'.encode() for n in stages_list)
    with pytest.raises(G.GitlinkError): G.entries(raw, index=True)


def test_no_conflict_schema_and_digest_exactly_unchanged(repo):
    st = state(repo)
    rows = S.records(st['baseline_head'])
    assert all(set(row['index']) == {'mode', 'oid', 'sha256'} for row in rows)
    content = {'base': st['baseline_head'], 'target': st['identity']['target_oid'], 'files': rows}
    assert S.candidate_fingerprint(st) == hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    raw = S.git_bytes('ls-files', '--stage', '-z')
    assert all(set(value) == {'mode', 'oid'} for value in G.entries(raw, index=True).values())


def test_candidate_materialization_refuses_but_base_is_available(repo):
    stages(repo)
    before = index_bytes(repo)
    with pytest.raises(C.MaterializationError, match='unresolved index'):
        with C.materialize(state(repo), 'candidate'): pytest.fail('unresolved candidate materialized')
    with C.materialize(state(repo), 'base') as checkout:
        assert (checkout['root'] / 'svc/app.py').read_text() == 'x = 1\n'
    assert index_bytes(repo) == before


def test_torn_stage_observation_refuses_without_state_mutation(repo, monkeypatch):
    stages(repo)
    st = state(repo)
    before = copy.deepcopy(st)
    original = T._rows
    count = 0
    def changing(st):
        nonlocal count
        rows = original(st)
        count += 1
        if count == 1: stages(repo, values=[(2, '100644', blob(repo, b'moved\n'))])
        return rows
    monkeypatch.setattr(T, '_rows', changing)
    with pytest.raises(SystemExit, match='unstable task attribution'): T.observe(st)
    assert st == before


def test_public_scoped_writer_admits_and_preserves_conflict_before_after(cli, repo, tmp_path):
    stages(repo)
    untouched = stages(repo, 'web/app.js')
    start_implementation(cli, tmp_path)
    before = index_bytes(repo)
    result = cli('writer', 'start', '--id', 'repair', '--role', 'al-engineer', '--files', 'svc/app.py')
    assert result[0] == 0, result
    assert index_bytes(repo) == before
    (repo / 'svc/app.py').write_text('resolved\n')
    # Test fixture acts as the trusted operator, not newly authorized agent Git staging.
    git(repo, 'add', '--', 'svc/app.py')
    result = cli('writer', 'finish', '--id', 'repair', '--outcome', 'success')
    assert result[0] == 0, result
    st = cli.state()
    import al_artifacts as A
    contribution = A.read_json(st['task_attribution']['contributions'][-1])
    prior = A.read_json(contribution['before_capture_ref'])
    after = A.read_json(contribution['after_capture_ref'])
    get = lambda c, p: next(r for r in c['files'] if r['path'] == p)
    assert 'stages' in get(prior, 'svc/app.py')['index']
    assert 'stages' not in get(after, 'svc/app.py')['index']
    assert [(x['stage'], x['mode'], x['oid']) for x in get(after, 'web/app.js')['index']['stages']] == untouched
    assert T.classify_changes(st)['task_owned'] == ['svc/app.py']


@pytest.mark.parametrize('kind', ['text', 'modify-delete'])
def test_real_git_merge_conflict_supports_public_writer(cli, repo, tmp_path, kind):
    base = head(repo)
    git(repo, 'checkout', '-qb', 'incoming')
    if kind == 'modify-delete': git(repo, 'rm', 'svc/app.py')
    else:
        (repo / 'svc/app.py').write_text('incoming = 1\n')
        git(repo, 'add', 'svc/app.py')
    git(repo, 'commit', '-qm', 'incoming')
    git(repo, 'checkout', '-qb', 'working', base)
    (repo / 'svc/app.py').write_text('working = 1\n')
    git(repo, 'add', 'svc/app.py')
    git(repo, 'commit', '-qm', 'working')
    merge = subprocess.run(['git', 'merge', '--no-edit', 'incoming'], cwd=repo, capture_output=True)
    assert merge.returncode == 1
    before = index_bytes(repo)
    start_implementation(cli, tmp_path)
    result = cli('writer', 'start', '--id', 'repair', '--role', 'al-engineer', '--files', 'svc/app.py')
    assert result[0] == 0, result
    assert index_bytes(repo) == before
    row = next(r for r in S.records(head(repo)) if r['path'] == 'svc/app.py')
    assert [v['stage'] for v in row['index']['stages']] == ([1, 2] if kind == 'modify-delete' else [1, 2, 3])
    assert any('unresolved index' in value for value in cli.state()['implementation_readiness']['obligations'])


@pytest.mark.parametrize('change', ['digest', 'duplicate', 'mode', 'oid', 'stage', 'mixed', 'worktree'])
def test_malformed_retained_conflict_records_refuse(repo, change):
    stages(repo)
    rows = S.records(head(repo))
    row = next(r for r in rows if r['path'] == 'svc/app.py')
    entry = row['index']['stages'][0]
    if change == 'digest': entry['sha256'] = '0' * 64
    elif change == 'duplicate': row['index']['stages'].append(copy.deepcopy(entry))
    elif change == 'mode': entry['mode'] = '120000'
    elif change == 'oid': entry['oid'] = 'not an oid'
    elif change == 'stage': entry['stage'] = True
    elif change == 'mixed': row['index']['mode'] = '100644'
    else: row['worktree'] = {'type': 'symlink', 'text': 'target'}
    with pytest.raises(SystemExit, match='unmerged'): T._validate_conflict_records(rows)


def test_index_cache_conflict_records_do_not_alias(repo):
    stages(repo)
    value = G.index_entries(repo)
    value[b'svc/app.py']['stages'][0]['oid'] = '0' * 40
    assert G.index_entries(repo)[b'svc/app.py']['stages'][0]['oid'] != '0' * 40


def test_review_start_and_approval_include_unresolved_barrier(repo, monkeypatch):
    import al_review as R
    import al_store
    import al_service_coverage
    import al_acceptance
    stages(repo)
    st = state(repo)
    monkeypatch.setattr(al_store, 'load', lambda sid: st)
    monkeypatch.setattr(R, 'require_transition', lambda *a: None)
    for name in ['workflow_gaps', 'candidate_gaps', 'behavior_gaps', 'operation_gaps', 'review_gaps']:
        monkeypatch.setattr(R, name, lambda st: [])
    monkeypatch.setattr(R.L, 'gaps', lambda *a: [])
    monkeypatch.setattr(al_service_coverage, 'admission_gaps', lambda *a, **kw: [])
    monkeypatch.setattr(al_acceptance, 'completion_gaps', lambda st: [])
    with pytest.raises(SystemExit, match='unresolved index'): R.cmd_review_start(None, 'fixture')
    assert R.approval_gaps(st) == ['unresolved index conflict: svc/app.py']


def test_publication_readiness_retains_unresolved_barrier(repo, monkeypatch):
    import al_publication_checks as P
    stages(repo)
    monkeypatch.setattr(P.W, 'operation_gaps', lambda st: [])
    monkeypatch.setattr(P.Currency, 'check', lambda st: {'current': True})
    monkeypatch.setattr(P.G, 'base_gaps', lambda st: [])
    monkeypatch.setattr(P.G, 'candidate_gaps', lambda st: [])
    monkeypatch.setattr(P.G, 'manifest', lambda st: None)
    monkeypatch.setattr(P.L, 'gaps', lambda *a: [])
    monkeypatch.setattr(P.L, 'ledger', lambda st: {'plan': {}})
    result = P.checks(state(repo), [])
    assert not result['ready'] and result['gaps'] == ['unresolved index conflict: svc/app.py']


def test_unmerged_parser_rejects_noncanonical_mode():
    with pytest.raises(G.GitlinkError, match='unmerged'):
        G.entries(f'100600 {"a" * 40} 1\tx\0'.encode(), index=True)


@pytest.mark.parametrize('mode', ['120000', '160000'])
def test_conflict_beneath_tree_type_boundary_is_not_hidden(repo, mode):
    value = head(repo) if mode == '160000' else blob(repo, b'svc')
    git(repo, 'update-index', '--add', '--cacheinfo', mode + ',' + value + ',boundary')
    git(repo, 'commit', '-qm', 'type boundary')
    base = head(repo)
    git(repo, 'update-index', '--force-remove', 'boundary')
    stages(repo, path='boundary/app.py')
    (repo / 'boundary').mkdir()
    (repo / 'boundary/app.py').write_text('conflict worktree')
    before = index_bytes(repo)
    with pytest.raises(SystemExit, match='type boundary'): S.records(base)
    with pytest.raises(SystemExit, match='type boundary'): S.source_rows(base=base)
    assert index_bytes(repo) == before
