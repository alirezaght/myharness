"""Parent gitlink lifecycle keeps base, staging and effective pointers distinct."""
import os
import subprocess
import pytest
import al_scope as S
import al_checkouts as C
from evidence_helpers import git
from gitlink_helpers import gitlink, oid, commit_child, remove_child, row_at, state


def index_entry(root):
    raw = S.git_bytes('ls-files', '--stage', 'svc/vendor', root=root).decode()
    return raw.split()[:2]


def test_base_index_and_effective_pointer_are_distinct(repo, gitlink):
    path, a, base = gitlink
    b = commit_child(path, 'B')
    git(repo, 'add', 'svc/vendor')
    c = commit_child(path, 'C')
    row = row_at(S.records(base))
    assert row['base'] == {'mode': '160000', 'oid': a}
    assert row['index'] == {'mode': '160000', 'oid': b}
    assert row['worktree'] == {'type': 'gitlink', 'mode': '160000', 'oid': c}
    assert row['child']['head'] == c and row['child']['state'] == 'initialized'
    before = S.git_bytes('ls-files', '--stage', '-z')
    with C.materialize(state(base), 'candidate') as checkout:
        assert index_entry(checkout['root']) == ['160000', c]
        child = checkout['root'] / 'svc/vendor'
        assert not child.exists() or list(child.iterdir()) == []
    assert S.git_bytes('ls-files', '--stage', '-z') == before
    assert oid(path) == c
    assert subprocess.run(['git', 'cat-file', '-e', c], cwd=repo, capture_output=True).returncode != 0


@pytest.mark.parametrize('kind', ['added', 'removed', 'staged', 'unstaged', 'committed'])
def test_pointer_lifecycle_materializes_effective_parent(repo, gitlink, kind):
    path, a, base = gitlink
    expected = a
    if kind == 'added':
        base = S.git_bytes('rev-parse', 'HEAD^').decode().strip()
    elif kind == 'removed':
        git(repo, 'update-index', '--force-remove', 'svc/vendor')
        expected = None
    else:
        expected = commit_child(path, 'B')
        if kind in ('staged', 'committed'):
            git(repo, 'add', 'svc/vendor')
        if kind == 'committed':
            git(repo, 'commit', '-qm', 'parent B')
    row = row_at(S.records(base))
    if kind == 'removed':
        assert row['index'] is None and row['worktree'] == {'type': 'deleted'}
        assert row['child']['state'] == 'removed'
    else:
        assert row['worktree']['oid'] == expected
    with C.materialize(state(base), 'candidate') as checkout:
        assert index_entry(checkout['root']) == ([] if expected is None else ['160000', expected])


@pytest.mark.parametrize('shape', ['initialized', 'empty', 'missing'])
def test_unchanged_children_need_no_modules_file_or_parent_objects(repo, gitlink, shape):
    path, a, _ = gitlink
    (repo / '.gitmodules').unlink()
    git(repo, 'add', '.gitmodules')
    git(repo, 'commit', '-qm', 'no metadata')
    base = oid(repo)
    child_index = None
    if shape != 'initialized':
        remove_child(path, empty=shape == 'empty')
    else:
        os.utime(path / 'file', (1, 1))
        child_index_path = path / os.fsdecode(S.git_bytes('rev-parse', '--git-path', 'index', root=path)).strip()
        child_index = child_index_path.read_bytes()
    capture = S.capture_candidate(state(base))
    assert row_at(capture['files'])['worktree']['oid'] == a
    for stage in ('base', 'candidate'):
        with C.materialize(state(base), stage) as checkout:
            assert index_entry(checkout['root']) == ['160000', a]
    if child_index is not None:
        assert child_index_path.read_bytes() == child_index


@pytest.mark.parametrize('transition', ['gitlink-file', 'file-gitlink', 'gitlink-directory'])
def test_type_transitions_replace_stale_disposable_paths(repo, gitlink, transition):
    path, a, base = gitlink
    if transition == 'file-gitlink':
        prior = S.git_bytes('rev-parse', 'HEAD^').decode().strip()
        # The review base has an ordinary file at the future gitlink path.
        blob = subprocess.run(['git', 'hash-object', '-w', '--stdin'], cwd=repo, input=b'old file', capture_output=True, check=True).stdout.decode().strip()
        tree = S.git_bytes('rev-parse', prior + '^{tree}').decode().strip()
        git(repo, 'read-tree', tree)
        git(repo, 'update-index', '--add', '--cacheinfo', '100644,' + blob + ',svc/vendor')
        tree = S.git_bytes('write-tree').decode().strip()
        base = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit-tree', tree], cwd=repo, input=b'file baseline', capture_output=True, check=True).stdout.decode().strip()
        git(repo, 'read-tree', 'HEAD')
    else:
        remove_child(path)
        git(repo, 'update-index', '--force-remove', 'svc/vendor')
        if transition == 'gitlink-file':
            path.write_text('replacement')
        else:
            path.mkdir()
            (path / 'normal').write_text('replacement')
        git(repo, 'add', 'svc/vendor')
    with C.materialize(state(base), 'candidate') as checkout:
        dest = checkout['root'] / 'svc/vendor'
        if transition == 'file-gitlink':
            assert index_entry(checkout['root']) == ['160000', a]
        else:
            assert (dest if transition == 'gitlink-file' else dest / 'normal').read_text() == 'replacement'


def test_unsafe_and_unmerged_parent_states_still_refuse(repo, gitlink):
    path, _, base = gitlink
    remove_child(path)
    path.symlink_to(repo / 'svc/app.py')
    # A final symlink is a normal type replacement; ancestor traversal is unsafe.
    rows = S.records(base)
    assert row_at(rows)['worktree']['type'] == 'symlink'
    path.unlink()
    git(repo, 'update-index', '--force-remove', 'svc/vendor')
    subprocess.run(['git', 'update-index', '--index-info'], cwd=repo, check=True,
                   input=('160000 ' + oid(repo) + ' 1\tsvc/vendor\n').encode())
    with pytest.raises(SystemExit, match='unmerged'):
        S.records(base)


def test_source_pointer_drift_during_overlay_refuses_execution(repo,gitlink,monkeypatch):
    path,_,base=gitlink
    original=C.run
    def run(root,*args):
        result=original(root,*args)
        if args[0]=='add':commit_child(path,'late pointer')
        return result
    monkeypatch.setattr(C,'run',run)
    with pytest.raises(C.MaterializationError,match='candidate changed'):
        with C.materialize(state(base),'candidate'):pytest.fail('source drift reached execution')
