"""A sourced CI job AL cannot run locally is an unverified, pipeline-verified obligation (#17).

It never makes review unreachable; it has two resolution paths: the planner maps it to the
job's verbatim script, or the supervisor records the pipeline's own result."""
import json
import pytest
import al_artifacts as A
import al_ci_inventory as CI
from conftest import _git
from hook_regression_helpers import invoke
from test_service_coverage import seed, start, discover, put, unit

RULES = ("  rules:\n"
         "    - if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'\n"
         "    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'\n")
# A small single-service repository: lint, unit tests, image build and two manual deploys.
GITLAB = ("stages: [lint, test, build, deploy]\n"
          "default:\n  image: python:3.12\n"
          "lint:\n  stage: lint\n  script:\n    - python3 -m compileall -q services/a/src\n" + RULES +
          "unit_test:\n  stage: test\n  before_script:\n    - python3 -c pass\n  script:\n    - python3 -m compileall -q services/a\n" + RULES +
          "build_image:\n  stage: build\n  script:\n    - docker build -t app .\n" + RULES +
          "deploy_staging:\n  stage: deploy\n  environment: staging\n  when: manual\n  script:\n    - ./deploy.sh staging\n"
          "deploy_production:\n  stage: deploy\n  environment: production\n  when: manual\n  script:\n    - ./deploy.sh production\n")


def repository(repo):
    seed(repo);unit(repo, 'services/a', check=False)
    put(repo, '.gitlab-ci.yml', GITLAB)
    _git(repo, 'add', '.');_git(repo, 'commit', '-qm', 'gitlab service')


def ci_gate(manifest, job):
    return next(g for g in manifest['gates'] if g['category'] == 'ci' and g.get('source', {}).get('job') == job)


def planned(cli, tmp_path, manifest, name='plan.json', ok=True):
    path = tmp_path / name;path.write_text(json.dumps(manifest))
    return invoke(cli, 'gates', 'plan', '--file', str(path), ok=ok)


def test_unresolved_jobs_are_pipeline_verified_not_a_coverage_gap(cli, repo, tmp_path):
    repository(repo);start(cli)
    st, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'identified'
    assert not any(g['code'] == 'missing-verification-check' for g in assessment['gaps'])
    delegated = m['service_coverage']['semantic_requirements']['checks']['pipeline_verified']
    assert [d['unit'] for d in delegated] == ['services/a']
    # Release jobs never verify a service; check and build jobs are left to the pipeline.
    assert {j['job'] for j in delegated[0]['jobs']} == {'build_image', 'lint', 'unit_test'}
    for job in ('lint', 'unit_test', 'build_image'):
        assert ci_gate(m, job)['applicability'] == 'UNRESOLVED'
        assert 'gates ci-evidence' in ci_gate(m, job)['reason']
    # Only jobs with a safe verbatim script offer a local recipe.
    assert ci_gate(m, 'unit_test')['ci_recipe']['argv'] == ['sh', '-e', '-c', 'python3 -c pass\npython3 -m compileall -q services/a']
    assert 'ci_recipe' not in ci_gate(m, 'build_image')
    planned(cli, tmp_path, m)
    status = json.loads(invoke(cli, 'gates', 'status')[1])
    assert not status['coverage_blockers']
    rows = [r for r in status['unverified'] if r.get('pipeline_verified')]
    assert len(rows) == 1 and rows[0]['unit'] == 'services/a' and 'unit_test' in rows[0]['reason']
    assert {r['gate'] for r in status['unverified']} >= {ci_gate(m, j)['id'] for j in ('lint', 'unit_test', 'build_image')}
    import al_gate_inventory as V
    st = json.loads(cli('show')[1])
    assert V.gaps(st, A.read_json(st['gate_manifests'][-1])) == []


def test_a_planner_maps_an_unresolved_job_to_its_verbatim_script(cli, repo, tmp_path):
    repository(repo);start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    gate = ci_gate(m, 'unit_test')
    forged = json.loads(json.dumps(m));g = ci_gate(forged, 'unit_test')
    g.update(applicability='APPLICABLE', argv=['true'])
    planned(cli, tmp_path, forged, 'forged.json', ok=False)
    forged = json.loads(json.dumps(m));g = ci_gate(forged, 'unit_test')
    g['ci_recipe'] = dict(g['ci_recipe'], argv=['true']);g.update(applicability='APPLICABLE', argv=['true'])
    result = planned(cli, tmp_path, forged, 'forged-recipe.json', ok=False)
    assert 'missing sourced CI job .gitlab-ci.yml:unit_test' in ''.join(map(str, result))
    gate.update(applicability='APPLICABLE', argv=gate['ci_recipe']['argv'])
    planned(cli, tmp_path, m)
    invoke(cli, 'gates', 'run', '--stage', 'base', '--id', gate['id'])
    status = json.loads(invoke(cli, 'gates', 'status')[1])
    assert any(r['gate'] == gate['id'] and r['status'] == 'PASS' for r in status['evidence'])
    note = next(r for r in status['unverified'] if r['gate'] == gate['id'])
    assert note['verified_by'] == 'local verbatim CI recipe'


def test_the_supervisor_records_remote_ci_evidence_for_the_reviewer(cli, repo, tmp_path):
    repository(repo);start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py']);planned(cli, tmp_path, m)
    gate = ci_gate(m, 'build_image')
    url = 'https://gitlab.example.invalid/group/app/-/jobs/77'
    invoke(cli, 'gates', 'ci-evidence', '--id', 'loc', '--status', 'success', '--url', url, ok=False)
    invoke(cli, 'gates', 'ci-evidence', '--id', gate['id'], '--status', 'success', '--url', 'not-a-url', ok=False)
    invoke(cli, 'gates', 'ci-evidence', '--id', gate['id'], '--status', 'success', '--url', url, '--job-id', 'x', ok=False)
    head = _git_head(repo)
    invoke(cli, 'gates', 'ci-evidence', '--id', gate['id'], '--status', 'failed', '--url', url,
           '--pipeline-id', '12', '--job-id', '77', '--commit', head)
    status = json.loads(invoke(cli, 'gates', 'status')[1])
    rows = [r for r in status['unverified'] if r['gate'] == gate['id']]
    assert len(rows) == 1 and rows[0]['verified_by'] == 'remote CI evidence (supervisor-recorded)'
    assert rows[0]['remote_ci']['status'] == 'failed' and rows[0]['remote_ci']['commit_matches_head'] is True
    assert rows[0]['remote_ci']['current'] is True and rows[0]['remote_ci']['verified_by_al'] is False
    assert 'remote CI reported failed' in rows[0]['reason'] and 'job 77' in rows[0]['reason']
    # A recorded result is a note: it never blocks and never becomes a passing gate.
    assert not status['candidate'] or not any(gate['id'] in g for g in status['candidate'])
    put(repo, 'services/a/src/app.py', 'VALUE=2\n')
    status = json.loads(invoke(cli, 'gates', 'status')[1])
    row = next(r for r in status['unverified'] if r['gate'] == gate['id'])
    assert row['remote_ci']['current'] is False and 'stale' in row['reason']


def _git_head(repo):
    import subprocess
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_unknown_inventory_and_mapped_non_covering_jobs_keep_the_gap(cli, repo, tmp_path):
    seed(repo);unit(repo, 'services/a', check=False)
    put(repo, '.gitlab-ci.yml', 'verify:\n  script: echo verify\n  rules:\n    - changes: [services/a/**/*]\n')
    start(cli);_, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    assert any(g['code'] == 'missing-verification-check' for g in m['service_coverage']['assessment']['gaps'])
    put(repo, '.gitlab-ci.yml', 'include:\n  - remote: https://example.invalid/jobs.yml\nverify:\n  image: python:3.12\n  script: pytest\n')
    _, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    gaps = {g['code'] for g in m['service_coverage']['assessment']['gaps']}
    assert 'unknown-ci-inventory' in gaps and 'missing-verification-check' in gaps


@pytest.mark.parametrize('definition,offered', [
    ('job:\n  image: python:3.12\n  script: [pytest -q]\n', True),
    ('job:\n  variables:\n    MODE: x\n  script:\n    - test "$MODE" = x && pytest\n', True),
    ('job:\n  image: docker\n  script: [docker push app]\n', False),
    ('job:\n  stage: deploy\n  environment: production\n  script: [./run.sh]\n', False),
    ('job:\n  script: [pytest]\n  rules:\n    - if: \'$A == "1"\'\n', True),
])
def test_only_safe_literal_job_scripts_offer_a_local_recipe(tmp_path, definition, offered):
    (tmp_path / '.gitlab-ci.yml').write_text(definition)
    job = next(o for o in CI.inventory(tmp_path, ['src/a.py'])['obligations'] if o['job'] == 'job')
    assert bool(job.get('local_recipe')) is offered
    if offered:assert job['local_recipe']['argv'][:3] == ['sh', '-e', '-c']
