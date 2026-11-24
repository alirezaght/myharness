"""Snapshot refs pinned by a run are released at its end; orphans are swept by age (#25)."""
import al_snapshot
from test_attribution_tree import _git
from test_task_attribution import run, state


def test_snapshot_refs_are_released_at_run_end_and_orphans_swept_by_age(repo):
    tree_id = al_snapshot.snapshot(repo)
    al_snapshot.pin('run-a', tree_id, tree_id, repo)
    al_snapshot.pin('run-b', tree_id, tree_id, repo)
    al_snapshot.pin('run-c', tree_id, tree_id, repo)
    al_snapshot.release('run-a', repo)
    assert [r.split('/')[3] for r in al_snapshot.pinned(repo)] == ['run-b', 'run-c']
    import time
    assert al_snapshot.sweep(keep=['run-b'], root=repo) == []      # recent pins stay
    later = time.time() + al_snapshot.ORPHAN_SECONDS + 60
    assert al_snapshot.sweep(keep=['run-b'], root=repo, now=later) == ['run-c']
    assert [r.split('/')[3] for r in al_snapshot.pinned(repo)] == ['run-b']
    _git(repo, 'pack-refs', '--all')                                    # packed refs age with the file
    assert al_snapshot.sweep(root=repo, now=later) == ['run-b']
    assert al_snapshot.pinned(repo) == []


def test_a_new_run_pins_its_trees_and_release_drops_only_them(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Release retained snapshot trees when the run ends.')
    st = state(state_file)
    other = al_snapshot.snapshot(repo)
    al_snapshot.pin('other-run', other, other, repo)
    mine = al_snapshot.REF_PREFIX + st['run_id'] + '/'
    assert any(r.startswith(mine) for r in al_snapshot.pinned(repo))
    al_snapshot.release(st['run_id'], repo)
    assert al_snapshot.pinned(repo) == [al_snapshot.REF_PREFIX + 'other-run/' + other]
