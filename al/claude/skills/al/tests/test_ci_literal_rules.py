"""Ordinary GitLab jobs map without borrowing repository-specific settings."""
import pytest

import al_ci_expression as E
import al_ci_inventory as CI
import al_ci_rules as R


@pytest.mark.parametrize('condition', [
    '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH',
    '$CI_COMMIT_BRANCH != null',
])
def test_plain_lint_and_unit_jobs_with_ordered_rules_have_local_recipes(repo, condition):
    (repo / '.gitlab-ci.yml').write_text('''lint:
  stage: lint
  script: ruff check src
  rules:
    - if: CONDITION
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
unit_test:
  script:
    - python3 -m pytest tests
  rules:
    - if: CONDITION
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
'''.replace('CONDITION', condition))
    result = CI.inventory(repo, ['svc/app.py'], 'merge_request_quality')
    assert not result['deferred']
    jobs = {item['job']: item for item in result['obligations']}
    assert set(jobs) == {'lint', 'unit_test'}
    for item in jobs.values():
        assert item['applicability'] == 'APPLICABLE'
        assert item['execution']['execution_kind'] == 'local'
        assert item['execution']['argv'][:3] == ['sh', '-eu', '-c']
    assert jobs['lint']['execution']['argv'][3] == 'ruff check src'
    assert jobs['unit_test']['execution']['argv'][3] == 'python3 -m pytest tests'


@pytest.mark.parametrize('expression, profile, expected', [
    ('$CI_COMMIT_BRANCH == null', 'merge_request_quality', True),
    ('$CI_COMMIT_BRANCH != null', 'merge_request_quality', False),
    ('$CI_PIPELINE_SOURCE == $CI_PIPELINE_SOURCE', 'merge_request_quality', True),
    ('$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH', 'merge_request_quality', None),
    ('$UNKNOWN == null', None, None),
    ('$UNKNOWN != $OTHER', None, None),
    ('$CI_MERGE_REQUEST_ID == null', 'merge_request_quality', False),
    ('$CI_MERGE_REQUEST_ID != null', 'merge_request_quality', True),
])
def test_literal_and_variable_comparisons_preserve_unknown_values(expression, profile, expected):
    assert E.evaluate(expression, profile, strict=True) is expected


def test_missing_pipeline_context_remains_unverified_not_a_parser_failure():
    result = R.evaluate_rules([{'if': '$BRANCH == $DEFAULT_BRANCH'}], ['svc/app.py'])
    assert result['selected'] is None
    assert result['reason_code'] == 'context-unresolved'
    assert result['context_fields_used'] == ['BRANCH', 'DEFAULT_BRANCH']
    assert not result['complete']


@pytest.mark.parametrize('expression', [
    '$BRANCH == nullsuffix', '$BRANCH == 1', '$BRANCH == $(command)', '$BRANCH == $',
])
def test_unsupported_syntax_stays_an_observation_with_no_execution(expression):
    result = R.evaluate_rules([{'if': expression}], ['svc/app.py'])
    assert result['selected'] is None and result['reason_code'] == 'unsupported-rules'
