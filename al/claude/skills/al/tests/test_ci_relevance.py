"""Relevance comes from sourced applicability, never diagnostic wording."""
import al_ci_inventory as CI
from test_ci_conventional import project,WORKFLOW
from test_service_coverage import start,discover,put


def test_unrelated_gitlab_job_is_deferred_before_unsupported_execution(repo):
    put(repo,'.gitlab-ci.yml','''verify:
  script: python3 -m compileall -q services/a
  rules:
    - changes: [services/a/**/*]
other:
  image: unrelated/custom-runner:1
  script: unsupported-tool
  rules:
    - changes: [apps/other/**/*]
''')
    result=CI.inventory(repo,['services/a/src/main.py'])
    assert [o['job'] for o in result['obligations']]==['verify']
    assert result['deferred'][0]['job']=='other'
    assert result['deferred'][0]['dependencies']


def test_disjoint_github_workflow_stays_in_currency_dependencies(repo):
    put(repo,'.github/workflows/other.yml','''on:
  pull_request:
    paths: [apps/other/**]
jobs:
  other:
    uses: unknown/unsupported@v1
''')
    result=CI.inventory(repo,['services/a/src/main.py'],'merge_request_quality')
    assert not result['obligations'] and len(result['deferred'])==1
    assert result['dependencies'][0]['path']=='.github/workflows/other.yml'
    # Shared/unknown effects are not proved irrelevant by a folder name.
    result=CI.inventory(repo,['apps/other/main.py'],'merge_request_quality')
    assert result['obligations'][0]['applicability']=='UNRESOLVED'


def test_concrete_extra_job_with_workflow_word_is_not_unknown_inventory(cli,repo,tmp_path):
    project(repo)
    # An unsupported concrete job's name and diagnostic cannot conceal jobs.
    put(repo,'.github/workflows/extra.yml',WORKFLOW.replace('uv run pytest','workflow-check .'))
    start(cli);_,manifest=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assessment=manifest['service_coverage']['assessment']
    assert assessment['outcome']=='identified'
    assert not any(g['code']=='unknown-ci-inventory' for g in assessment['gaps'])
