"""Literal non-deployment actions require independently sourced local checks."""
import pytest
import al_ci_rules as Rules
from test_service_coverage import start,discover,put
from launcher_context import admit_planning


@pytest.mark.parametrize('action',['prepare','access'])
def test_literal_prepare_access_has_bound_local_verification(cli,repo,tmp_path,action):
    put(repo,'.gitlab-ci.yml','validate:\n  stage: test\n  environment:\n    name: staging\n    action: '+action+'\n  script: python3 -m compileall -q svc\n')
    start(cli);_,manifest=discover(cli,tmp_path,['svc/app.py'])
    gates=[g for g in manifest['gates'] if g['category']=='ci' and g.get('source',{}).get('job')=='validate']
    assert len(gates)==1 and gates[0]['required'] is True
    assert gates[0]['applicability']=='APPLICABLE' and gates[0]['argv']
    assert Rules.release('validate',Rules.structure({'stage':'test','environment':{'name':'staging','action':action}})) is False
    binding=next(b for b in manifest['service_coverage']['assessment']['required_bindings'] if b['category']=='ci')
    chain=binding['verification_chain']
    assert chain['environment_profile']['environment']=={'name':'staging','action':action}
    assert chain['environment_profile']['profile']=='isolated-stdlib-environment-checks-v1'
    assert chain['requirements'][0]['original_argv']==['python3','-m','compileall','-q','svc']
    assert chain['requirements'][0]['argv'][1:]==['-I','-S','-m','compileall','-q','svc']


@pytest.mark.parametrize('action',['prepare','access'])
@pytest.mark.parametrize('script',['python3 -m compileall -q svc','python -m json.tool svc/data.json'])
def test_real_base_candidate_and_admission(cli,repo,tmp_path,action,script):
    from ci_environment_helpers import job,planned,run,required_ci,entries
    from hook_regression_helpers import invoke
    put(repo,'svc/data.json','{"valid":true}\n')
    manifest=planned(cli,repo,tmp_path,{'.gitlab-ci.yml':job(action,script)})
    gate=required_ci(manifest);assert gate['required'] and gate['applicability']=='APPLICABLE'
    assert run(cli,'base')[0]==0
    assert entries(cli,gate['id']) and all(e['status']=='PASS' for e in entries(cli,gate['id']))
    admit_planning(cli)
    invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','body','--role','al-engineer','--files','svc/app.py')
    invoke(cli,'writer','finish','--id','body','--outcome','success')
    assert run(cli,'candidate')[0]==0
    invoke(cli,'phase','VERIFICATION')


@pytest.mark.parametrize('script,body,path',[
    ('python3 -m compileall -q svc','invalid python !\n','svc/app.py'),
    ('python3 -m json.tool svc/data.json','not json\n','svc/data.json'),
])
def test_invalid_inputs_fail_real_required_check(cli,repo,tmp_path,script,body,path):
    from ci_environment_helpers import job,planned,run,required_ci,entries,state
    from hook_regression_helpers import invoke
    put(repo,path,body);manifest=planned(cli,repo,tmp_path,{'.gitlab-ci.yml':job(script=script)})
    gate=required_ci(manifest)
    cli('gates','run','--stage','base','--id',gate['id'])
    assert any(e['status']=='FAIL' for e in entries(cli,gate['id']))
    admit_planning(cli);before=state(cli);invoke(cli,'phase','IMPLEMENTATION',ok=False);assert state(cli)==before


def test_architecture_failure_remains_blocking(cli,repo,tmp_path):
    from ci_environment_helpers import job,planned,run,required_ci,entries,state
    from hook_regression_helpers import invoke
    put(repo,'svc/app.py','import forbidden_dependency\n')
    manifest=planned(cli,repo,tmp_path,{'.gitlab-ci.yml':job()});gate=required_ci(manifest)
    invoke(cli,'gates','run','--stage','base','--id',gate['id'])
    assert entries(cli,gate['id']) and all(e['status']=='PASS' for e in entries(cli,gate['id']))
    run(cli,'base')
    assert any(e['status']=='FAIL' for e in state(cli)['evidence']['entries'] if e['area'].startswith('gate-base-imports'))
    admit_planning(cli);before=state(cli);invoke(cli,'phase','IMPLEMENTATION',ok=False);assert state(cli)==before


@pytest.mark.parametrize('script',[
    '\n    - python3 -m compileall -q svc\n    - terraform validate','echo checked','terraform validate','python3 -m compileall -q missing','python3 -m compileall -q ../outside',
    'python3 -m compileall -q svc && echo done','python3 -m compileall $TARGET','env python3 -m compileall svc',
    './python3 -m compileall svc','python3 -m json.tool svc/data.json output.json','python3 -m compileall --help svc',
])
def test_unsupported_recipe_remains_visible(repo,script):
    import al_ci_inventory as CI
    from ci_environment_helpers import job
    put(repo,'.gitlab-ci.yml',job(script=script));value=CI.inventory(repo,['svc/app.py'])['obligations']
    assert len(value)==1 and value[0]['applicability']=='UNRESOLVED' and value[0]['execution'] is None
    assert any(label in value[0]['reason'] for label in ('environment','syntax-check','JSON-check'))


@pytest.mark.parametrize('extra',[
    '  before_script: echo setup\n','  variables: {VALUE: secret}\n','  image: python:latest\n',
    '  services: [database]\n','  needs: [upstream]\n','  after_script: echo cleanup\n',
    '  artifacts: {paths: [result]}\n','  dependencies: [upstream]\n',
])
def test_unavailable_context_is_not_discarded(repo,extra):
    import al_ci_inventory as CI
    from ci_environment_helpers import job
    put(repo,'.gitlab-ci.yml',job(extra=extra));value=CI.inventory(repo,['svc/app.py'])['obligations']
    assert len(value)==1 and value[0]['applicability']=='UNRESOLVED'
    assert 'environment' in value[0]['reason']


def test_inherited_sources_and_global_cache_are_bound(repo):
    import al_ci_inventory as CI
    put(repo,'.gitlab-ci.yml','include: [ci/checks.yml]\n')
    put(repo,'ci/checks.yml','.base:\n  environment: {name: staging, action: access}\n  script: python3 -m compileall svc\nvalidate:\n  extends: .base\n')
    value=CI.inventory(repo,['svc/app.py']);job=value['obligations'][0]
    assert job['execution'] and {d['path'] for d in job['dependencies']}=={'.gitlab-ci.yml','ci/checks.yml'}
    put(repo,'.gitlab-ci.yml','include: [ci/checks.yml]\ncache: {paths: [shared]}\n')
    blocked=CI.inventory(repo,['svc/app.py'])['obligations'][0]
    assert blocked['applicability']=='UNRESOLVED' and 'settings' in blocked['reason']


def test_isolated_modules_ignore_repository_shadowing(cli,repo,tmp_path,monkeypatch):
    from ci_environment_helpers import job,planned,run,required_ci,entries
    marker=tmp_path/'unwanted-import'
    for name in ('json','compileall','sitecustomize'):
        put(repo,'svc/'+name+'.py','from pathlib import Path\nPath('+repr(str(marker))+').write_text("unsafe")\n')
    put(repo,'svc/data.json','{}\n');monkeypatch.setenv('PYTHONPATH',str(repo/'svc'))
    text=job(script='python3 -m compileall -q svc').replace('  script: python3 -m compileall -q svc','  script:\n    - python3 -m compileall -q svc\n    - python3 -m json.tool svc/data.json')
    manifest=planned(cli,repo,tmp_path,{'.gitlab-ci.yml':text});run(cli,'base')
    assert entries(cli,required_ci(manifest)['id']) and all(e['status']=='PASS' for e in entries(cli,required_ci(manifest)['id'])) and not marker.exists()


@pytest.mark.parametrize('change',['argv','scope','job','profile'])
def test_binding_changes_cannot_be_forged(cli,repo,tmp_path,change):
    import copy
    from ci_environment_helpers import job,planned,state,required_ci
    from batch8_helpers import plan
    from al_repository_facts import digest
    import al_service_coverage as Coverage
    manifest=planned(cli,repo,tmp_path,{'.gitlab-ci.yml':job()});forged=copy.deepcopy(manifest);gate=required_ci(forged)
    if change=='argv':gate['argv']=['python3','-m','compileall','svc']
    elif change=='scope':gate['scope']=['svc/app.py']
    elif change=='job':put(repo,'.gitlab-ci.yml',job('access'))
    else:
        declaration=forged['service_coverage'];semantic=declaration['semantic_requirements']
        binding=next(b for b in semantic['checks']['bindings'] if b['category']=='ci')
        binding['verification_chain']['environment_profile']['environment']['name']='forged'
        binding['execution_signature']=digest({k:v for k,v in binding.items() if k!='execution_signature'})
        declaration['semantic_sha256']=digest(semantic);declaration['assessment']=Coverage.assess({**semantic,'scan':{'stable':semantic['stable']}})
        for link in declaration['bindings']:
            if link['gate_id']==gate['id']:link['execution_signature']=binding['execution_signature']
        gate['execution_signature']=binding['execution_signature']
    before=state(cli);plan(cli,tmp_path,forged,ok=False);assert state(cli)==before


@pytest.mark.parametrize('mutation',['name','command','setup','target','interpreter'])
def test_independent_inventory_reconstructs_profile_currency(repo,monkeypatch,mutation):
    import al_ci_inventory as CI
    import al_ci_environment as Environment
    from ci_environment_helpers import job
    text=job();put(repo,'.gitlab-ci.yml',text)
    first=CI.inventory(repo,['svc/app.py'])['obligations'][0]['execution']
    if mutation=='name':put(repo,'.gitlab-ci.yml',text.replace('staging','preview'))
    elif mutation=='command':put(repo,'.gitlab-ci.yml',text.replace('-q svc','-qq svc'))
    elif mutation=='setup':put(repo,'.gitlab-ci.yml',job(extra='  before_script: echo setup\n'))
    elif mutation=='target':
        import shutil
        shutil.rmtree(repo/'svc')
    else:
        pin=Environment.interpreter();pin['sha256']='0'*64;monkeypatch.setattr(Environment,'interpreter',lambda:pin)
    current=CI.inventory(repo,['svc/app.py'])['obligations'][0]
    assert current['execution']!=first


@pytest.mark.parametrize('boundary',['opaque','symlink'])
def test_unavailable_targets_cannot_get_a_local_binding(repo,tmp_path,boundary):
    import al_ci_inventory as CI
    from ci_environment_helpers import job
    put(repo,'.gitlab-ci.yml',job())
    if boundary=='symlink':
        outside=tmp_path/'outside.py';outside.write_text('raise RuntimeError("must not execute")\n')
        (repo/'svc/link.py').symlink_to(outside)
    value=CI.inventory(repo,['svc/app.py'],opaque=['svc'] if boundary=='opaque' else [])['obligations']
    assert len(value)==1 and value[0]['applicability']=='UNRESOLVED' and value[0]['execution'] is None
    assert boundary in value[0]['reason']
