"""Source-derived generated GitLab definitions remain source, never execution."""
import al_ci_inventory as CI
import al_ci_rules as Rules
from al_ci_yaml import load
from test_service_coverage import put


def test_merge_defaults_allow_explicit_override_but_not_duplicate_explicit_keys():
    data = load('.defaults: &defaults\n  script: echo inherited\njob:\n  <<: *defaults\n  script: echo explicit\n')
    assert data['job']['script'] == 'echo explicit'
    bad = load('job:\n  script: echo one\n  script: echo two\n')
    assert '__unsupported__' in bad['job']


def generated(repo):
    put(repo, '.gitlab-ci.yml', "include:\n  - local: ci/templates/python.yml\n  - local: ci/generated/b.yml\n    rules:\n      - if: '$OPTIONAL_GENERATED'\n")
    put(repo, 'ci/templates/python.yml', """.python_template:
  image: python:3.13
  cache:
    paths: [.cache/]
  artifacts:
    paths: [report.xml]
  after_script: publish-report
.routing:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      changes: [services/b/**/*]
    - changes: [services/b/**/*]
""")
    put(repo, 'ci/generated/b.yml', """.fast_defaults: &fast_defaults
  rules:
    - changes: [services/b/**/*]
b:fast_check:
  <<: *fast_defaults
  extends: .python_template
  variables:
    APP_DIR: services/b
  rules: !reference [.routing, rules]
  script:
    - python3 -m ruff check services/b
    - python3 -m mypy services/b
    - python3 -m pytest services/b/tests
    - if "$SEMGREP"; then semgrep services/b; fi
""")


def test_generated_literal_identity_and_full_requirements_stay_unmapped(repo):
    generated(repo)
    value = CI.inventory(repo, ['services/b/src/main.py'], 'merge_request_quality')
    job = next(o for o in value['obligations'] if o['job'] == 'b:fast_check')
    assert job['execution'] is None
    assert {d['path'] for d in job['dependencies']} == {'.gitlab-ci.yml', 'ci/templates/python.yml', 'ci/generated/b.yml'}
    present = [v for v in job['definition_variants'] if v['present']]
    assert present and all(v['job']['variables']['APP_DIR'] == 'services/b' for v in present)
    assert all(len(v['job']['script']) == 4 and v['job']['after_script'] == 'publish-report' for v in present)


def test_every_supported_generated_variant_is_disjoint_for_docs(repo):
    generated(repo)
    value = CI.inventory(repo, ['docs/readme.md'])
    assert value['obligations'] == []
    assert [o['job'] for o in value['deferred']] == ['b:fast_check']
    assert all(o['definition_universe'] == 'complete' for o in value['deferred'])


def test_conditional_override_can_make_the_same_job_global(repo):
    put(repo, '.gitlab-ci.yml', "include:\n  - local: ci/override.yml\n    rules:\n      - if: '$OPTIONAL'\njob:\n  script: python3 -m compileall -q .\n  rules:\n    - changes: [services/b/**/*]\n")
    put(repo, 'ci/override.yml', 'job:\n  rules:\n    - when: always\n')
    # Included definitions precede root definitions: put the conditional override last
    # in a separate include after the base, instead of changing GitLab merge order.
    put(repo, '.gitlab-ci.yml', "include:\n  - local: ci/base.yml\n  - local: ci/override.yml\n    rules:\n      - if: '$OPTIONAL'\n")
    put(repo, 'ci/base.yml', 'job:\n  script: python3 -m compileall -q .\n  rules:\n    - changes: [services/b/**/*]\n')
    value = CI.inventory(repo, ['docs/readme.md'])
    assert [o['job'] for o in value['obligations']] == ['job']
    assert not value['deferred']


def test_unknown_include_universe_cannot_be_deferred(repo):
    put(repo, '.gitlab-ci.yml', 'include:\n  - remote: https://example.invalid/jobs.yml\n')
    value = CI.inventory(repo, ['docs/readme.md'])
    assert any(o['impact']['inventory_complete'] is False for o in value['obligations'])


def test_variant_overflow_is_an_explicit_unknown_universe(repo, monkeypatch):
    monkeypatch.setattr(CI.Inventory, 'MAX_WORLDS', 2)
    put(repo, '.gitlab-ci.yml', "include:\n  - local: ci/a.yml\n    rules:\n      - if: '$A'\n  - local: ci/b.yml\n    rules:\n      - if: '$B'\n")
    for name in ['a', 'b']:
        put(repo, 'ci/'+name+'.yml', name+':\n  script: echo verify\n')
    value = CI.inventory(repo, ['docs/readme.md'])
    assert any(o['impact']['inventory_complete'] is False and 'world' in o['reason'] for o in value['obligations'])


def test_prior_definite_mr_arm_does_not_require_compare_to():
    value = [{'if': '$CI_PIPELINE_SOURCE == "merge_request_event"', 'changes': ['services/b/**/*']},
             {'changes': {'paths': ['services/b/**/*'], 'compare_to': 'refs/heads/main'}}]
    selected, _, _ = Rules.rules(value, ['services/b/main.py'], 'merge_request_quality')
    assert selected is True
    selected, _, _ = Rules.rules(value[1:], ['services/b/main.py'])
    assert selected is None
