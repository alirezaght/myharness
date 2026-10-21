"""CI job identity, inherited applicability and current independent coverage."""
import json
import pytest
import al_artifacts as A
import al_ci_inventory as CI
import al_gate_currency as C
from hook_regression_helpers import invoke,finish_review
from batch8_helpers import force_applicable_ci,setup,executable_ci,plan,two_jobs

PATHS=['apps/web/src/app.ts','apps/admin/src/app.ts']


@pytest.mark.parametrize('erasure',['missing','units','job'])
def test_one_job_cannot_claim_two_units_or_impersonate_another(cli,repo,tmp_path,state_file,erasure):
    m=executable_ci(setup(cli,repo,tmp_path,two_jobs(),PATHS))
    jobs=[g for g in m['gates'] if g['category']=='ci'];web=next(g for g in jobs if g['source']['job']=='web-test')
    if erasure=='job':next(g for g in jobs if g['source']['job']=='admin-test')['source']['job']='web-test'
    else:
        m['gates']=[g for g in m['gates'] if g not in jobs]+[web]
        if erasure=='units':web['units']=['apps/web','apps/admin'];web['scope']=['.']
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before


def test_multiple_jobs_for_one_unit_are_independent(cli,repo,tmp_path,state_file):
    text='lint:\n  script: python3 -m json.tool apps/web/package.json\n  rules:\n    - changes: [apps/web/**/*]\ntest:\n  script: python3 -m json.tool apps/web/package.json\n  rules:\n    - changes: [apps/web/**/*]\n'
    m=executable_ci(setup(cli,repo,tmp_path,{'.gitlab-ci.yml':text},PATHS[:1]))
    assert {g['source']['job'] for g in m['gates'] if g['category']=='ci'}=={'lint','test'}
    m['gates']=[g for g in m['gates'] if not (g['category']=='ci' and g['source']['job']=='test')]
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before


def test_shared_job_really_covers_both_units(cli,repo,tmp_path):
    text='shared-test:\n  script:\n    - python3 -m json.tool apps/web/package.json\n    - python3 -m json.tool apps/admin/package.json\n  rules:\n    - changes: [apps/web/**/*, apps/admin/**/*]\n'
    m=executable_ci(setup(cli,repo,tmp_path,{'.gitlab-ci.yml':text},PATHS))
    ci=[g for g in m['gates'] if g['category']=='ci']
    assert len(ci)==1 and ci[0]['units']==['apps/admin','apps/web']
    plan(cli,tmp_path,m)


def test_local_include_anchor_template_and_reference_are_sourced(cli,repo,tmp_path):
    files={'.gitlab-ci.yml':'include:\n  - local: ci/jobs.yml\n',
           'ci/jobs.yml':'include: ci/templates.yml\nweb-test:\n  extends: .verify\n  rules:\n    - !reference [.selected, rules]\n',
           'ci/templates.yml':'.paths: &web_paths\n  - apps/web/**/*\n.selected:\n  rules:\n    - changes: *web_paths\n.verify:\n  stage: test\n  script: python3 -m json.tool apps/web/package.json\n'}
    m=executable_ci(setup(cli,repo,tmp_path,files,PATHS[:1]))
    job=next(g for g in m['gates'] if g['category']=='ci')
    assert job['source']['path']=='ci/jobs.yml' and job['source']['job']=='web-test'
    assert {'ci/templates.yml','ci/jobs.yml','.gitlab-ci.yml'}<=set(job['scope'])
    plan(cli,tmp_path,m)


@pytest.mark.parametrize('rule',["if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'",'changes: {compare_to: main, paths: [apps/web/**/*]}','exists: [config]'])
def test_unknown_pipeline_or_expression_cannot_be_declared_applicable(cli,repo,tmp_path,state_file,rule):
    text='verify:\n  script: echo checked\n  rules:\n    - '+rule+'\n'
    m=setup(cli,repo,tmp_path,{'.gitlab-ci.yml':text},PATHS[:1])
    obligations=CI.inventory(repo,PATHS[:1])['obligations'];assert obligations[0]['applicability']=='UNRESOLVED'
    before=state_file().read_bytes();plan(cli,tmp_path,force_applicable_ci(m),ok=False)
    assert state_file().read_bytes()==before


def test_remote_job_must_equal_sourced_identity(cli,repo,tmp_path,state_file):
    m=executable_ci(setup(cli,repo,tmp_path,two_jobs(),PATHS))
    g=next(g for g in m['gates'] if g['category']=='ci')
    g.update(execution_kind='remote',argv=['glab','api','projects/1/jobs/2'],remote={'provider':'gitlab','project_id':1,'job_name':'different-job'})
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before


def test_currency_independently_checks_job_coverage(cli,repo,tmp_path):
    m=executable_ci(setup(cli,repo,tmp_path,two_jobs(),PATHS));plan(cli,tmp_path,m)
    state=json.loads(cli('show')[1]);assert C.check(state)['current']
    m['gates']=[g for g in m['gates'] if g['category']!='ci' or g['source']['job']!='admin-test']
    state['gate_manifests'].append(A.snapshot_bytes(state['session_id'],json.dumps(m).encode(),'gate-manifest','legacy incomplete manifest'))
    with pytest.raises(SystemExit, match='corrupt service coverage'):
        C.check(state)
    state['gate_manifests'].pop()
    pipeline=repo/'.gitlab-ci.yml'
    pipeline.write_text(pipeline.read_text()+'extra-test:\n  script: python3 -m json.tool apps/admin/package.json\n  rules:\n    - changes: [apps/admin/**/*]\n')
    currency=C.check(state)
    assert not currency['current'] and any('extra-test' in gap for gap in currency['reasons'])


def test_changed_template_and_new_unit_invalidate_old_completeness(cli,repo,tmp_path):
    m=executable_ci(setup(cli,repo,tmp_path,two_jobs(),PATHS[:1]));plan(cli,tmp_path,m)
    state=json.loads(cli('show')[1]);assert C.check(state)['current']
    target=repo/'apps/admin/src/app.ts';target.parent.mkdir(parents=True);target.write_text('changed\n')
    assert not C.check(state)['current']
    target.unlink();(repo/'.gitlab-ci.yml').write_text((repo/'.gitlab-ci.yml').read_text()+'# changed dependency\n')
    assert not C.check(state)['current']


def test_release_jobs_do_not_require_deployment_execution(repo):
    (repo/'.gitlab-ci.yml').write_text('release:prod:\n  stage: deploy\n  script: deploy-production\nverify:\n  stage: test\n  script: echo checked\n')
    jobs=CI.inventory(repo,['svc/app.py'])['obligations']
    assert [j['job'] for j in jobs]==['verify']


def test_complete_local_job_route_can_reach_approval(cli,repo,tmp_path):
    m=executable_ci(setup(cli,repo,tmp_path,{'.gitlab-ci.yml':'verify:\n  script: python3 -m compileall -q svc\n  rules:\n    - changes: [svc/**/*]\n'},['svc/app.py']))
    for g in m['gates']:
        if g['category'] not in {'ci','lint-imports'} and not g.get('obligation_ids'):
            g.update(applicability='NOT_APPLICABLE',required=False,source=m['rules'][0],reason='No such obligation exists in this disposable repository')
    plan(cli,tmp_path,m);invoke(cli,'gates','run','--stage','base')
    invoke(cli,'phase','IMPLEMENTATION');invoke(cli,'writer','start','--id','engineer','--role','al-engineer','--files','svc/app.py')
    invoke(cli,'writer','finish','--id','engineer','--outcome','success');invoke(cli,'phase','VERIFICATION')
    invoke(cli,'gates','run','--stage','candidate')
    import sys
    invoke(cli,'evidence','plan','--area','unit','--tier','broad','--kind','check','--scope','svc/app.py','--',sys.executable,'-c','print("verified")')
    invoke(cli,'evidence','run','--area','unit');finish_review(cli,tmp_path)
    invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')


@pytest.mark.parametrize('pattern,path,expected',[
    ('apps/web/**/*','apps/web/app.ts','APPLICABLE'),
    ('apps/web/**/*','apps/web/src/app.ts','APPLICABLE'),
    ('apps/web/**/*','apps/web/.hidden.ts','APPLICABLE'),
    ('apps/web/*','apps/web/src/app.ts',None),
    ('**/*.json','config.json','APPLICABLE'),
    ('**/*.json','apps/web/config.json','APPLICABLE'),
    ('apps/{web,admin}/**/*','apps/web/app.ts','UNRESOLVED'),
])
def test_gitlab_globs_never_erase_direct_nested_or_unknown_inputs(repo,pattern,path,expected):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script: echo checked\n  rules:\n    - changes: ["'+pattern+'"]\n')
    jobs=CI.inventory(repo,[path])['obligations']
    if expected is None:assert jobs==[]
    else:assert len(jobs)==1 and jobs[0]['applicability']==expected


def test_mixed_known_and_unknown_patterns_keep_uncertain_unit_coverage(repo):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script: echo checked\n  rules:\n    - changes: [apps/web/**/*, "$OTHER_INPUTS"]\n')
    job=CI.inventory(repo,PATHS)['obligations'][0]
    assert job['applicability']=='UNRESOLVED' and job['units']==['apps/admin','apps/web']


def test_nonlocal_include_is_explicitly_unresolved(repo):
    (repo/'.gitlab-ci.yml').write_text('include:\n  - remote: https://example.invalid/ci.yml\n')
    jobs=CI.inventory(repo,['svc/app.py'])['obligations']
    assert jobs and all(j['applicability']=='UNRESOLVED' for j in jobs)


def test_environment_verification_is_not_misclassified_as_release(repo):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  stage: test\n  environment:\n    name: staging\n    action: verify\n  script: echo checked\n')
    assert [j['job'] for j in CI.inventory(repo,['svc/app.py'])['obligations']]==['verify']


def test_reasoned_na_is_allowed_when_all_sourced_verification_jobs_are_unselected(cli,repo,tmp_path):
    m=setup(cli,repo,tmp_path,{'.gitlab-ci.yml':'web-test:\n  script: echo checked\n  rules:\n    - changes: [apps/web/**/*]\n'},[PATHS[1]])
    for g in m['gates']:
        if g['category']=='ci':g.update(applicability='NOT_APPLICABLE',required=False,reason='The literal rules select only web; this task anticipates admin',source=m['rules'][0])
    plan(cli,tmp_path,m)


def test_ci_gate_scope_cannot_omit_consulted_inherited_definition(cli,repo,tmp_path,state_file):
    files={'.gitlab-ci.yml':'include: ci/base.yml\nverify:\n  extends: .base\n',
           'ci/base.yml':'.base:\n  script: echo checked\n  rules:\n    - changes: [apps/web/**/*]\n'}
    m=executable_ci(setup(cli,repo,tmp_path,files,PATHS[:1]))
    for g in m['gates']:
        if g['category']=='ci':g['scope']=[PATHS[0],'.gitlab-ci.yml']
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False)
    assert state_file().read_bytes()==before


def test_inherited_template_from_conditional_include_keeps_missing_context_unresolved(repo):
    (repo/'ci').mkdir()
    (repo/'.gitlab-ci.yml').write_text('include:\n  - local: ci/base.yml\n    rules:\n      - if: "$USE_TEMPLATE"\nverify:\n  extends: .base\n')
    (repo/'ci/base.yml').write_text('.base:\n  script: echo checked\n  rules:\n    - changes: [apps/web/**/*]\n')
    job=CI.inventory(repo,PATHS[:1])['obligations'][0]
    assert job['job']=='verify' and job['applicability']=='UNRESOLVED'


def test_workflow_in_local_include_cannot_be_ignored_by_root_jobs(repo):
    (repo/'ci').mkdir()
    (repo/'.gitlab-ci.yml').write_text('include: ci/workflow.yml\nverify:\n  script: echo checked\n')
    (repo/'ci/workflow.yml').write_text('workflow:\n  rules:\n    - if: "$PIPELINE_ALLOWED"\n')
    assert CI.inventory(repo,PATHS[:1])['obligations'][0]['applicability']=='UNRESOLVED'
