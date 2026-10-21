"""Source-conditioned definitions cannot erase possible verification obligations."""
import json
import pytest
import al_artifacts as A
import al_ci_inventory as CI
import al_gate_currency as C
from batch8_helpers import force_applicable_ci,setup,plan,executable_ci

PATHS=['apps/web/app.ts','apps/admin/app.ts']


def definitions(repo,files):
    for name,text in files.items():
        file=repo/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
    return CI.inventory(repo,PATHS)


def conditional(base,override,job='',repeat=False,condition='$OPTIONAL == "yes"'):
    root='include:\n  - local: ci/base.yml\n  - local: ci/controller.yml\n'
    if repeat:root+='  - local: ci/base.yml\n'
    return {'.gitlab-ci.yml':root+job,'ci/base.yml':base,
            'ci/controller.yml':'include:\n  - local: ci/optional.yml\n    rules:\n      - if: \''+condition+'\'\n',
            'ci/optional.yml':override}


@pytest.mark.parametrize('job,stage,expected',[
 ('migrate:check','', 'APPLICABLE'),
 ('release:verify','  stage: test\n','APPLICABLE'),
 ('migrate:check','  stage: build\n','UNRESOLVED'),
 ('verify','  stage: "$STAGE"\n','UNRESOLVED'),
 ('verify','  stage: []\n','UNRESOLVED'),
 ('verify','  stage: {}\n','UNRESOLVED'),
 ('verify','  stage: null\n','UNRESOLVED'),
 ('verify','  stage: ""\n','UNRESOLVED'),
 ('release:deploy','  stage: deploy\n',None),
 ('verify','  stage: release\n',None),
 ('verify','  stage: build\n  environment: staging\n',None),
 ('verify','  stage: build\n  environment: {name: staging, action: verify}\n','APPLICABLE'),
 ('verify','  stage: build\n  environment: "$ENV"\n','UNRESOLVED'),
])
def test_default_inherited_and_explicit_stage_classification(repo,job,stage,expected):
    text='.base:\n'+stage+'  script: echo checked\n'+job+':\n  extends: .base\n'
    jobs=definitions(repo,{'.gitlab-ci.yml':text})['obligations']
    if expected is None:assert jobs==[]
    else:assert len(jobs)==1 and jobs[0]['job']==job and jobs[0]['applicability']==expected


@pytest.mark.parametrize('override',[
 'verify:\n  rules:\n    - when: never\n',
 'verify:\n  rules:\n    - changes: [outside/**/*]\n',
 'verify:\n  stage: deploy\n',
 'verify: null\n',
 'verify:\n  environment: staging\n',
 'verify:\n  environment: {name: staging, action: stop}\n',
 'verify:\n  stage: "$STAGE"\n',
])
def test_uncertain_job_override_precedes_all_exclusion_paths(repo,override):
    data=definitions(repo,conditional('verify:\n  script: echo checked\n',override))
    job=next(j for j in data['obligations'] if j['job']=='verify')
    assert job['applicability']=='UNRESOLVED' and job['units']==['apps/admin','apps/web']
    assert job['inputs']==['apps/admin/app.ts','apps/web/app.ts']
    assert {d['path'] for d in job['dependencies']}=={'.gitlab-ci.yml','ci/base.yml','ci/controller.yml','ci/optional.yml'}


@pytest.mark.parametrize('mechanism',['extends','reference','stage-reference'])
def test_transitive_hidden_template_or_reference_taint_is_retained(repo,mechanism):
    if mechanism=='extends':
        base='.policy:\n  script: echo checked\n.leaf:\n  extends: .policy\nverify:\n  extends: .leaf\n'
        override='.policy:\n  stage: deploy\n'
    elif mechanism=='reference':
        base='.policy:\n  rules:\n    - changes: [apps/web/**/*]\nverify:\n  script: echo checked\n  rules:\n    - !reference [.policy, rules]\n'
        override='.policy:\n  rules:\n    - when: never\n'
    else:
        base='.stage:\n  stage: test\nverify:\n  script: echo checked\n  stage: !reference [.stage, stage]\n'
        override='.stage:\n  stage: deploy\n'
    jobs=definitions(repo,conditional(base,override))['obligations']
    assert len(jobs)==1 and jobs[0]['job']=='verify' and jobs[0]['applicability']=='UNRESOLVED'
    assert jobs[0]['units']==['apps/admin','apps/web']


@pytest.mark.parametrize('condition,expected',[('false','APPLICABLE'),('true',None),('$OPTIONAL == "yes"','UNRESOLVED')])
def test_false_true_and_unknown_include_conditions_merge_honestly(repo,condition,expected):
    data=definitions(repo,conditional('verify:\n  script: echo checked\n','verify:\n  rules:\n    - when: never\n',condition=condition));jobs=data['obligations']
    if expected is None:assert jobs==[]
    else:assert len(jobs)==1 and jobs[0]['applicability']==expected
    if condition=='false':assert 'ci/optional.yml' not in {d['path'] for d in data['dependencies']}


def test_repeated_unconditional_include_cannot_erase_earlier_uncertain_contribution(repo):
    base='verify:\n  script: echo checked\n  rules:\n    - changes: [apps/web/**/*]\n'
    data=definitions(repo,conditional(base,'verify:\n  stage: deploy\n',repeat=True))
    assert len(data['obligations'])==1 and data['obligations'][0]['applicability']=='UNRESOLVED'
    assert data['obligations'][0]['source_path']=='ci/base.yml'


def test_known_true_repeated_include_reapplies_normal_merge_order(repo):
    base='verify:\n  script: echo checked\n  rules:\n    - changes: [apps/web/**/*]\n'
    data=definitions(repo,conditional(base,'verify:\n  rules:\n    - when: never\n',repeat=True,condition='true'))
    assert len(data['obligations'])==1 and data['obligations'][0]['applicability']=='APPLICABLE'
    assert data['obligations'][0]['units']==['apps/web']


@pytest.mark.parametrize('erasure',['APPLICABLE','NOT_APPLICABLE','omit'])
def test_manifest_and_currency_cannot_clear_definition_uncertainty(cli,repo,tmp_path,state_file,erasure):
    files=conditional('verify:\n  script: echo checked\n','verify:\n  stage: deploy\n')
    m=setup(cli,repo,tmp_path,files,PATHS);plan(cli,tmp_path,m)
    for g in m['gates']:
        if g['category']=='ci':
            if erasure=='NOT_APPLICABLE':g.update(applicability='NOT_APPLICABLE',required=False,argv=[],reason='Attempted false N/A')
            else:force_applicable_ci(m)
    if erasure=='omit':m['gates']=[g for g in m['gates'] if g['category']!='ci']
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before
    state=json.loads(cli('show')[1]);state['gate_manifests'].append(A.snapshot_bytes(state['session_id'],json.dumps(m).encode(),'gate-manifest','legacy conditional omission'))
    if erasure=='omit':
        with pytest.raises(SystemExit):C.check(state)
    else:
        current=C.check(state);assert not current['current'] and any('UNRESOLVED' in reason for reason in current['reasons'])


def test_default_test_stage_job_has_complete_public_approval_route(cli,repo,tmp_path):
    from batch8_context_helpers import approval
    m=setup(cli,repo,tmp_path,{'.gitlab-ci.yml':'migrate:check:\n  script: echo verified\n  rules:\n    - changes: [svc/**/*]\n'},['svc/app.py'])
    assert [g['source']['job'] for g in m['gates'] if g['category']=='ci']==['migrate:check']
    approval(cli,tmp_path,m)


@pytest.mark.parametrize('stage',['','  stage: test\n'])
@pytest.mark.parametrize('environment,expected',[
 ('staging',None),('{name: staging}',None),('{name: staging, action: start}',None),
 ('{name: staging, action: stop}',None),('{name: staging, action: verify}','APPLICABLE'),
 ('"$ENV"','UNRESOLVED'),('{name: staging, action: "$ACTION"}','UNRESOLVED'),
 ('{name: staging, action: prepare}','UNRESOLVED'),('null','UNRESOLVED'),
 ('[]','UNRESOLVED'),('{action: verify}','UNRESOLVED'),('{name: staging, script: unknown}','UNRESOLVED'),
])
def test_explicit_environment_semantics_precede_default_or_test_stage(repo,stage,environment,expected):
    text='verify:\n'+stage+'  environment: '+environment+'\n  script: echo checked\n'
    jobs=definitions(repo,{'.gitlab-ci.yml':text})['obligations']
    if expected is None:assert jobs==[]
    else:assert len(jobs)==1 and jobs[0]['applicability']==expected


def test_explicit_release_stage_precedes_verify_environment_but_invalid_stage_is_unknown(repo):
    text='release:deploy:\n  stage: deploy\n  environment: {name: staging, action: verify}\n  script: echo deploy\nverify:\n  stage: "$STAGE"\n  environment: staging\n  script: echo checked\n'
    jobs=definitions(repo,{'.gitlab-ci.yml':text})['obligations']
    assert len(jobs)==1 and jobs[0]['job']=='verify' and jobs[0]['applicability']=='UNRESOLVED'


def test_public_discovery_retains_verify_and_unknown_environment_but_excludes_deployment(cli,repo,tmp_path,state_file):
    text='deploy-default:\n  environment: staging\n  script: echo deploy\ndeploy-test:\n  stage: test\n  environment: {name: staging, action: stop}\n  script: echo deploy\nverify:\n  environment: {name: staging, action: verify}\n  script: echo checked\nunknown:\n  stage: test\n  environment: "$ENV"\n  script: echo unknown\n'
    m=setup(cli,repo,tmp_path,{'.gitlab-ci.yml':text},['svc/app.py'])
    assert {g['source']['job'] for g in m['gates'] if g['category']=='ci'}=={'verify','unknown'}
    plan(cli,tmp_path,m);force_applicable_ci(m)
    before=state_file().read_bytes();plan(cli,tmp_path,m,ok=False);assert state_file().read_bytes()==before
