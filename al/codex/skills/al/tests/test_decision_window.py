"""A decision reuses one worktree observation only while the worktree stays put."""
import subprocess

import pytest

import al_scope as S


def _state(repo):
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    return {'baseline_head': head, 'identity': {'merge_base': head, 'target_oid': head}}


def test_stable_decision_reads_once_inside_the_window(repo):
    st = _state(repo)
    seen = []

    @S.decision
    def decide(state):
        seen.append(S.records(state['baseline_head']))
        seen.append(S.records(state['baseline_head']))
        seen[-1][0]['path'] = 'mutated-by-caller'
        return len(seen)

    assert decide(st) == 2
    assert seen[0] == S.records(st['baseline_head']) and seen[1][0]['path'] == 'mutated-by-caller'


def test_moved_worktree_reevaluates_the_decision_fresh(repo):
    st = _state(repo)
    calls = []

    @S.decision
    def decide(state):
        calls.append(S.records(state['baseline_head']))
        if len(calls) == 1:
            (repo / 'moved-during-decision.txt').write_text('moved\n')
        return calls[-1]

    result = decide(st)
    assert len(calls) == 2
    assert any(row['path'] == 'moved-during-decision.txt' for row in result)


def test_refusal_is_kept_only_for_a_stable_worktree(repo):
    st = _state(repo)
    attempts = []

    @S.decision
    def refuse_once(state):
        attempts.append(1)
        if len(attempts) == 1:
            (repo / 'moved-before-refusal.txt').write_text('moved\n')
            raise SystemExit('refused against a moving worktree')
        return 'fresh answer'

    assert refuse_once(st) == 'fresh answer'

    @S.decision
    def always_refuse(state):
        raise SystemExit('stable refusal')

    with pytest.raises(SystemExit, match='stable refusal'):
        always_refuse(st)
