"""review-start reads the working copy through one observation window (#35)."""
import json

import al_scope as S
from evidence_helpers import plan_and_run_broad, start_implementation
from launcher_context import observe_behavior


def _ready_for_review(cli, tmp_path):
    start_implementation(cli, tmp_path)
    assert plan_and_run_broad(cli) == 0
    assert cli('phase', 'VERIFICATION')[0] == 0
    result, out, err = cli('gates', 'run', '--stage', 'candidate')
    assert result == 0, out + err
    observe_behavior(cli, directory=tmp_path)


def _windows(monkeypatch):
    opened = []
    original = S._observed_decision

    def counted(fn, *args, **kwargs):
        opened.append(fn.__name__)
        return original(fn, *args, **kwargs)

    monkeypatch.setattr(S, '_observed_decision', counted)
    return opened


def test_review_start_admits_and_checks_in_one_window(cli, tmp_path, state_file, monkeypatch):
    _ready_for_review(cli, tmp_path)
    opened = _windows(monkeypatch)
    result, out, err = cli('review-start')
    assert result == 0, out + err
    # Admission (gate currency, coverage readiness, workflow gaps) and the review's
    # completeness checks share one window.
    assert opened == ['_admitted_inputs'], opened
    st = json.loads(state_file().read_bytes())
    assert st['phase'] == 'REVIEW' and st['review']['stage'] == 'fresh'


def test_a_window_evaluated_again_starts_from_the_loaded_state(cli, tmp_path, state_file, repo, monkeypatch):
    _ready_for_review(cli, tmp_path)
    import al_review
    original = al_review.require_transition
    seen = []

    def moving(st, *args, **kwargs):
        # The transition and the checks run on a copy of the loaded state: what an unstable
        # first evaluation changed is not seen by the second one.
        seen.append('probe' in st)
        st['probe'] = True
        if len(seen) == 1:
            (repo / 'moved-during-review-start.txt').write_text('moved\n')
        return original(st, *args, **kwargs)

    monkeypatch.setattr(al_review, 'require_transition', moving)
    result, out, err = cli('review-start')
    assert seen == [False, False], (seen, result, out[-800:], err[-800:])
    st = json.loads(state_file().read_bytes())
    assert 'probe' not in st or result == 0
    assert st['phase'] == ('REVIEW' if result == 0 else 'VERIFICATION'), out + err
