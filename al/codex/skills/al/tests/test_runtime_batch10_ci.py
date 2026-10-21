"""Selection must be trustworthy before unsupported execution identity can disappear."""
import pytest
import al_ci_inventory as CI
from test_runtime_batch9_ci import conditional,definitions
from batch8_helpers import force_applicable_ci,setup,plan,executable_ci
from batch8_context_helpers import prepared,approval


def jobs(repo,fields,rules='    - when: never\n'):
    return definitions(repo,{'.gitlab-ci.yml':'release:prod:\n  script: echo checked\n'+fields+'  rules:\n'+rules})['obligations']


@pytest.mark.parametrize('fields',[
 '  stage: provision-prod\n','  environment: {name: production, action: prepare}\n',
 '  stage: provision-prod\n  environment: {name: production, action: prepare}\n',
 '  environment: {name: production, action: access}\n',
])
def test_false_valid_execution_identity_is_excluded(repo,fields):
    assert jobs(repo,fields)==[]


@pytest.mark.parametrize('fields',[
 '  stage: []\n','  stage: {}\n','  stage: null\n','  stage: true\n','  stage: ""\n',
 '  environment: null\n','  environment: []\n','  environment: true\n',
 '  environment: {name: [], action: prepare}\n','  environment: {name: production, action: []}\n',
 '  environment: {name: production, action: unexpected}\n','  environment: {name: production, extra: value}\n',
])
def test_false_rules_cannot_hide_malformed_semantic_fields(repo,fields):
    actual=jobs(repo,fields)
    assert len(actual)==1 and actual[0]['applicability']=='UNRESOLVED'


@pytest.mark.parametrize('rules',[
 '    - if: 0\n','    - if: 1\n',
 '    - when: never\n    - unsupported: true\n',
 '    - when: never\n    - if: "$FLAG =~ /yes/"\n',
 '    - when: never\n    - changes: []\n',
 '    - when: never\n    - changes: [false]\n',
 '    - when: never\n    - changes: {paths: [apps/web/**/*], compare_to: []}\n',
 '    - when: invalid\n','    - false\n',
])
def test_full_selection_boundary_is_validated_before_false_result(repo,rules):
    actual=jobs(repo,'  stage: deploy\n',rules)
    assert len(actual)==1 and actual[0]['applicability']=='UNRESOLVED'


@pytest.mark.parametrize('legacy',['only','except'])
def test_legacy_selection_cannot_hide_under_false_rules(repo,legacy):
    actual=jobs(repo,'  stage: deploy\n  '+legacy+': [main]\n')
    assert len(actual)==1 and actual[0]['applicability']=='UNRESOLVED'


@pytest.mark.parametrize('fields',['  stage: deploy\n','  environment: production\n'])
def test_unknown_selection_precedes_proven_release_classification(repo,fields):
    actual=jobs(repo,fields,'    - if: \'$OPTIONAL == "yes"\'\n    - when: never\n')
    assert len(actual)==1 and actual[0]['applicability']=='UNRESOLVED'


def test_selected_prepare_requires_supported_checks_but_definite_deploy_is_excluded(repo):
    actual=jobs(repo,'  environment: {name: production, action: prepare}\n','    - when: always\n')
    assert len(actual)==1 and actual[0]['applicability']=='UNRESOLVED'
    assert 'unsupported environment verification command' in actual[0]['reason']
    import al_ci_rules as Rules
    assert Rules.release('validate',Rules.structure({'environment':{'name':'staging','action':'prepare'}})) is False
    assert jobs(repo,'  stage: deploy\n','    - when: always\n')==[]


def test_supported_false_condition_skips_valid_unknown_compare_to(repo):
    rules='    - if: \'$CI_COMMIT_BRANCH == "main"\'\n      changes: {paths: [apps/web/**/*], compare_to: main}\n    - when: never\n'
    root=repo/'.gitlab-ci.yml';root.write_text('release:prod:\n  stage: provision-prod\n  script: echo release\n  rules:\n'+rules)
    assert CI.inventory(repo,['apps/web/app.ts'],'merge_request_quality')['obligations']==[]
    actual=CI.inventory(repo,['apps/web/app.ts'])['obligations']
    assert len(actual)==1 and actual[0]['applicability']=='UNRESOLVED'


def test_false_inherited_and_referenced_jobs_retain_source_dependencies(repo):
    files={'.gitlab-ci.yml':'include: [ci/jobs.yml]\n',
           'ci/jobs.yml':'.base:\n  stage: provision-prod\n  environment: {name: production, action: prepare}\n  script: echo prepare\n.policy:\n  rules:\n    - when: never\nrelease:prod:\n  extends: .base\n  rules: !reference [.policy, rules]\n'}
    actual=definitions(repo,files)
    assert actual['obligations']==[]
    assert {d['path'] for d in actual['dependencies']}=={'.gitlab-ci.yml','ci/jobs.yml'}


def test_conditional_definition_and_workflow_uncertainty_precede_false_exclusion(repo):
    actual=definitions(repo,conditional('release:prod:\n  stage: provision-prod\n  script: echo release\n',
                                       'release:prod:\n  rules:\n    - when: never\n'))
    assert len(actual['obligations'])==1 and actual['obligations'][0]['applicability']=='UNRESOLVED'
    assert len(actual['dependencies'])==4
    actual=definitions(repo,{'.gitlab-ci.yml':'workflow:\n  rules:\n    - if: \'$OPTIONAL == "yes"\'\nrelease:prod:\n  stage: deploy\n  script: echo release\n  rules:\n    - when: never\n'})
    assert any(j['job']=='release:prod' and j['applicability']=='UNRESOLVED' for j in actual['obligations'])


def test_public_unknown_rules_cannot_be_cleared_and_refusal_is_byte_identical(cli,repo,tmp_path,state_file):
    m=setup(cli,repo,tmp_path,{'.gitlab-ci.yml':'release:prod:\n  stage: deploy\n  script: echo release\n  rules:\n    - if: \'$OPTIONAL == "yes"\'\n    - when: never\n'},['svc/app.py'])
    plan(cli,tmp_path,m);force_applicable_ci(m)
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False)
    assert state_file().read_bytes()==before


def test_mr_selected_verification_and_false_prepare_have_full_approval_route(cli,repo,tmp_path):
    text='verify:\n  script: echo checked\n  rules:\n    - if: \'$CI_PIPELINE_SOURCE == "merge_request_event"\'\n      changes: [svc/**/*]\nrelease:prod:\n  stage: provision-prod\n  environment: {name: production, action: prepare}\n  script: echo prepare\n  rules:\n    - when: never\n'
    _,_,m=prepared(cli,repo,tmp_path,{'.gitlab-ci.yml':text},['svc/app.py'])
    assert [g['source']['job'] for g in m['gates'] if g['category']=='ci']==['verify']
    approval(cli,tmp_path,m)
