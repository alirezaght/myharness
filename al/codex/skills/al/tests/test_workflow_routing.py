"""Attribution seeds dependency selection without replacing proportional routing."""
import al_gate_inventory as Inventory
import al_task_attribution as T
from evidence_helpers import plan_na_gates, plan_and_run_broad, complete_review
from test_task_attribution import run, state, contribution
from test_service_coverage import seed, put


def test_task_seed_still_discovers_unchanged_preexisting_shared_consumers(cli, repo, state_file):
    seed(repo)
    put(repo, 'services/b/src/app.py', 'from common import VALUE\n')
    run(cli, 'start', '--spec', 'Repair a shared library while preserving all actual consumers.')
    st = state(state_file)
    contribution(st, ['packages/shared'], lambda: put(repo, 'packages/shared/src/common.py', 'VALUE=2\n'))
    result = T.classify_changes(st)
    assert result['unchanged_preexisting'] == ['services/b/src/app.py']
    inventory = Inventory.inventory(st)
    assert inventory['paths'] == ['packages/shared/src/common.py']
    assert inventory['service_coverage']['selected_services'] == ['services/a', 'services/b']
    assert inventory['service_coverage']['outcome'] == 'identified'


def test_unknown_dependency_still_broadens_only_with_complete_checks(cli, repo, state_file):
    seed(repo)
    run(cli, 'start', '--spec', 'Repair shared loading while preserving complete uncertain dependency coverage.')
    st = state(state_file)
    contribution(st, ['packages/shared'], lambda: put(repo, 'packages/shared/src/common.py', 'def load(name):\n    return __import__(name)\n'))
    assessment = Inventory.inventory(st)['service_coverage']
    assert assessment['outcome'] == 'exhaustive'
    assert assessment['selected_services'] == ['services/a', 'services/b']
    config = repo/'services/b/pyproject.toml'
    config.write_text(config.read_text().split('[tool.al.architecture]')[0])
    assert Inventory.inventory(st)['service_coverage']['outcome'] == 'incomplete'


def test_whole_mr_boundary_keeps_all_changed_paths(cli, repo, state_file):
    (repo/'docs/readme.md').write_text('prior user change\n')
    run(cli, 'start', '--spec', 'Review all selected merge request changes without attribution subtraction.',
        '--review-mode', 'whole-mr', '--target', 'HEAD')
    assert Inventory.inventory(state(state_file))['paths'] == ['docs/readme.md']


def test_small_route_one_writer_retains_independent_fresh_and_comparison_reviews(cli, repo, tmp_path, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Implement a small isolated repair with both independent review stages.')
    run(cli, 'spec-gate', 'SPEC_READY')
    run(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    plan_na_gates(cli, tmp_path, ('svc', 'web'))
    run(cli, 'phase', 'IMPLEMENTATION')
    run(cli, 'writer', 'start', '--id', 'repair', '--role', 'al-engineer', '--files', 'svc')
    before = state_file().read_bytes()
    refused, _, _ = cli('writer', 'start', '--id', 'second', '--role', 'al-builder', '--files', 'web')
    assert 'SMALL permits one writer' in str(refused)
    assert state_file().read_bytes() == before
    (repo/'svc/app.py').write_text('x = 2\n')
    run(cli, 'writer', 'finish', '--id', 'repair', '--outcome', 'success')
    assert T.classify_changes(state(state_file))['task_owned'] == ['svc/app.py']
    run(cli, 'phase', 'VERIFICATION')
    assert plan_and_run_broad(cli) == 0
    assert cli('approved')[0] != 0
    import evidence_helpers
    original_report=evidence_helpers._review_report
    def report(*args, **kwargs):
        value=original_report(*args, **kwargs)
        if value['stage']=='fresh':value.pop('prior_resolutions',None)
        return value
    monkeypatch.setattr(evidence_helpers,'_review_report',report)
    complete_review(cli, tmp_path, blocking=False)
    st = state(state_file)
    import al_artifacts as A
    assert A.read_json(st['review']['fresh'])['invocation_id'] != A.read_json(st['review']['comparison'])['invocation_id']
    run(cli, 'phase', 'FINAL_VERIFICATION')
    run(cli, 'approved')


def test_unrelated_preexisting_unit_is_not_a_new_task_unit_blocker(cli, repo, tmp_path, state_file):
    from test_service_coverage import unit, start, plan
    import al_gates as Gates
    seed(repo)
    unit(repo, 'services/unrelated')
    start(cli)
    plan(cli, repo, tmp_path, ['packages/shared'])
    run(cli, 'gates', 'run', '--stage', 'base')
    run(cli, 'phase', 'IMPLEMENTATION')
    run(cli, 'writer', 'start', '--id', 'shared', '--role', 'al-engineer', '--files', 'packages/shared')
    put(repo, 'packages/shared/src/common.py', 'VALUE=2\n')
    run(cli, 'writer', 'finish', '--id', 'shared', '--outcome', 'success')
    st = state(state_file)
    gaps = Gates.candidate_gaps(st)
    assert not any('new touched unit' in gap for gap in gaps)
    assert any('candidate gate' in gap for gap in gaps)
    assert 'services/unrelated/src/app.py' in T.classify_changes(st)['unchanged_preexisting']
