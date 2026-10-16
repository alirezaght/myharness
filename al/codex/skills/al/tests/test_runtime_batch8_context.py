"""Declared MR scenarios resolve source rules without inventing pipeline proof."""
import json
from pathlib import Path
import pytest
import al_artifacts as A
import al_ci_context as CC
import al_ci_expression as E
import al_ci_inventory as CI
import al_gate_currency as C
import al_gate_discovery as D
from batch8_helpers import setup,plan,executable_ci,two_jobs
from batch8_context_helpers import prepared,declaration,approval
from hook_regression_helpers import invoke

PROFILE='merge_request_quality'
QUALITY='workflow:\n  rules:\n    - if: \'$CI_PIPELINE_SOURCE == "merge_request_event"\'\n      auto_cancel: {on_new_commit: interruptible}\n    - when: never\n  auto_cancel: {on_new_commit: none, on_job_failure: none}\ninclude:\n  - local: ci/quality.yml\n    rules:\n      - if: \'$BRANCH_ENV_CLEANUP != "true" && $SONAR_SCAN != "true"\'\n'
JOBS='verify:\n  script: echo checked\n  rules:\n    - if: \'$FASTLANE == "true"\'\n      when: never\n    - if: \'$CI_MERGE_REQUEST_ID\'\n      changes: [svc/**/*]\n'


@pytest.mark.parametrize('expression,expected',[
 ('$CI_PIPELINE_SOURCE == "merge_request_event"',True),('$CI_COMMIT_BRANCH',False),
 ('$CI_MERGE_REQUEST_ID',True),('$CI_MERGE_REQUEST_ID == "123"',None),
 ('$UNKNOWN == "yes"',None),('$UNKNOWN || $CI_MERGE_REQUEST_ID',True),
 ('$UNKNOWN && $CI_COMMIT_BRANCH',False),
 ('($FASTLANE != "true" && $SONAR_SCAN != "true") && $CI_MERGE_REQUEST_ID',True),
 ('$FASTLANE == "true" || $BRANCH_ENV_CLEANUP == "true" || $SONAR_SCAN == "true"',False),
 ('$CI_PIPELINE_SOURCE =~ /merge_request/',None),('__import__("os")',None),
 ('$CI_MERGE_REQUEST_ID == $OTHER',None),('('*34+'$CI_MERGE_REQUEST_ID'+')'*34,None),
])
def test_fixed_scenario_expression_semantics(expression,expected):
    assert E.evaluate(expression,PROFILE) is expected


def test_explicit_profile_runs_local_checks_to_approval(cli,repo,tmp_path):
    st,context,m=prepared(cli,repo,tmp_path,{'.gitlab-ci.yml':QUALITY,'ci/quality.yml':JOBS},['svc/app.py'])
    assert CC.profile(st,context)==PROFILE
    jobs=CI.inventory(repo,['svc/app.py'],PROFILE)['obligations']
    assert len(jobs)==1 and jobs[0]['job']=='verify' and jobs[0]['applicability']=='APPLICABLE'
    approval(cli,tmp_path,m)


def test_quality_profile_cannot_select_fastlane_to_erase_verification(repo):
    (repo/'.gitlab-ci.yml').write_text(JOBS)
    jobs=CI.inventory(repo,['svc/app.py'],PROFILE)['obligations']
    assert len(jobs)==1 and jobs[0]['job']=='verify' and jobs[0]['applicability']=='APPLICABLE'


@pytest.mark.parametrize('changes',[{'run_id':'another-run'},{'session_id':'different-run'},{'task_hash':'0'*64},{'selection':{'label':''}}])
def test_unbound_context_refuses_without_state_mutation(cli,repo,tmp_path,state_file,changes):
    setup(cli,repo,tmp_path,two_jobs(),['apps/web/app.ts']);st=json.loads(cli('show')[1]);m=D.discover(st,['apps/web/app.ts'])
    m['ci_context']=declaration(st,**changes)
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before


def test_context_content_tampering_refuses_admission_and_currency(cli,repo,tmp_path,state_file):
    st,context,m=prepared(cli,repo,tmp_path,{'.gitlab-ci.yml':QUALITY,'ci/quality.yml':JOBS},['svc/app.py'])
    m=executable_ci(m);plan(cli,tmp_path,m);st=json.loads(cli('show')[1]);assert C.check(st)['current']
    artifact=Path(context['source']['stored_path']);data=json.loads(artifact.read_bytes());data['selection']['label']='Altered declared selection';artifact.write_text(json.dumps(data))
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before
    with pytest.raises(SystemExit):C.check(st)


def test_context_replacement_requires_fresh_manifest_bound_base_evidence(cli,repo,tmp_path,state_file):
    st,context,m=prepared(cli,repo,tmp_path,{'.gitlab-ci.yml':QUALITY,'ci/quality.yml':JOBS},['svc/app.py'])
    m=executable_ci(m)
    for g in m['gates']:
        if g['category']!='ci':g.update(applicability='NOT_APPLICABLE',required=False,source=m['rules'][0],reason='No such obligation in the disposable repository')
    plan(cli,tmp_path,m);invoke(cli,'gates','run','--stage','base')
    replacement=declaration(st,selection={'label':'A new explicit planning declaration'})
    m['ci_context']=replacement;plan(cli,tmp_path,m)
    before=state_file().read_bytes();invoke(cli,'phase','IMPLEMENTATION',ok=False);assert state_file().read_bytes()==before
    invoke(cli,'gates','run','--stage','base');invoke(cli,'phase','IMPLEMENTATION')


def test_fixed_profile_cannot_be_extended_with_caller_variables(cli,repo,tmp_path,state_file):
    st,context,m=prepared(cli,repo,tmp_path,two_jobs(),['apps/web/app.ts'])
    context['variables']={'FASTLANE':'true'};m['ci_context']=context
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before


@pytest.mark.parametrize('text',[
 'verify:\n  script: echo checked\n  rules:\n    - if: "$UNKNOWN"\n',
 'include: ci/missing.yml\n',
 'workflow:\n  rules:\n    - when: never\nverify:\n  script: echo checked\n',
 'workflow:\n  rules:\n    - if: "$CI_MERGE_REQUEST_ID"\n      auto_cancel: {script: arbitrary}\nverify:\n  script: echo checked\n',
])
def test_unknown_or_excluded_context_is_never_empty_satisfied_inventory(repo,text):
    (repo/'.gitlab-ci.yml').write_text(text)
    jobs=CI.inventory(repo,['svc/app.py'],PROFILE)['obligations']
    assert jobs and any(j['applicability']=='UNRESOLVED' for j in jobs)


def test_unsupported_profile_remains_explicitly_unresolved(cli,repo,tmp_path):
    st,context,m=prepared(cli,repo,tmp_path,{'.gitlab-ci.yml':QUALITY,'ci/quality.yml':JOBS},['svc/app.py'],profile='unsupported_profile')
    assert CC.profile(st,context) is None
    assert all(g['applicability']=='UNRESOLVED' for g in m['gates'] if g['category']=='ci')
    plan(cli,tmp_path,m)


def test_context_still_requires_each_both_unit_job(cli,repo,tmp_path,state_file):
    st,context,m=prepared(cli,repo,tmp_path,two_jobs(),['apps/web/app.ts','apps/admin/app.ts'])
    m=executable_ci(m);plan(cli,tmp_path,m)
    m['gates']=[g for g in m['gates'] if g['category']!='ci' or g['source']['job']=='web-test']
    g=next(g for g in m['gates'] if g['category']=='ci');g['units']=['apps/web','apps/admin'];g['scope']=['.']
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before


def test_folded_script_list_data_preserves_source_rule_selection(repo):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script:\n    - pnpm exec playwright test\n      tests/suite.spec.ts\n      --project lifecycle\n  rules:\n    - if: \'$CI_MERGE_REQUEST_ID\'\n      changes: [apps/web/**/*]\n')
    jobs=CI.inventory(repo,['apps/web/app.ts'],PROFILE)['obligations']
    assert len(jobs)==1 and jobs[0]['job']=='verify' and jobs[0]['applicability']=='APPLICABLE'
    assert CI.inventory(repo,['apps/admin/app.ts'],PROFILE)['obligations']==[]


def test_literal_block_script_and_indentless_reports_do_not_erase_job_rules(repo):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script:\n    - |\n      python3 - <<"DONE"\n      print("checked")\n      DONE\n  artifacts:\n    reports:\n      junit:\n      - report.xml\n      - integration.xml\n  rules:\n    - changes: [apps/web/**/*]\n')
    jobs=CI.inventory(repo,['apps/web/app.ts'],PROFILE)['obligations']
    assert len(jobs)==1 and jobs[0]['applicability']=='APPLICABLE'
    assert CI.inventory(repo,['apps/admin/app.ts'],PROFILE)['obligations']==[]


@pytest.mark.parametrize('path,expected',[('.','UNRESOLVED'),('apps/web','UNRESOLVED'),('apps/web/src','UNRESOLVED'),('apps/admin','UNRESOLVED'),('services/shortlist-service',None),('apps/web/src/app.ts','APPLICABLE'),('apps/web/no-match.ts',None)])
def test_anticipated_directories_keep_possible_wildcard_jobs(repo,path,expected):
    if path=='apps/web/src':(repo/path).mkdir(parents=True)
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script: echo checked\n  rules:\n    - changes: [apps/*/src/**/*]\n')
    jobs=CI.inventory(repo,[path],PROFILE)['obligations']
    if expected is None:assert jobs==[]
    else:assert len(jobs)==1 and jobs[0]['applicability']==expected


@pytest.mark.parametrize('corruption',['source','stored_path','kind','profile'])
def test_malformed_typed_context_refuses_atomically(cli,repo,tmp_path,state_file,corruption):
    st,context,m=prepared(cli,repo,tmp_path,two_jobs(),['apps/web/app.ts'])
    if corruption=='source':context['source']='prose is not typed evidence'
    elif corruption=='profile':context['profile']={}
    else:context['source'][corruption]=42
    m['ci_context']=context;before=state_file().read_bytes()
    plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before


@pytest.mark.parametrize('pattern',['apps/we*/src/**/*','apps/we?/src/**/*'])
def test_partial_component_wildcard_cannot_erase_anticipated_directory(repo,pattern):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script: echo checked\n  rules:\n    - changes: ["'+pattern+'"]\n')
    jobs=CI.inventory(repo,['apps/web'],PROFILE)['obligations']
    assert len(jobs)==1 and jobs[0]['applicability']=='UNRESOLVED'
    assert CI.inventory(repo,['services/shortlist-service'],PROFILE)['obligations']==[]
