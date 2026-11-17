"""Source/package contracts; framework fixtures are contracts, not live framework certification."""
import copy
import json
import shlex
import subprocess

import pytest

import al_package_checks as Package
import al_package_profiles as Profiles
import al_repository_facts as Facts
from conftest import _git
from test_service_coverage import seed,start,discover,put


def js(repo,scripts,tools=None,manager='npm@11.9.0'):
    seed(repo)
    package={'name':'web-fixture','version':'1.0.0','scripts':scripts}
    if manager:package['packageManager']=manager
    put(repo,'apps/web/package.json',json.dumps(package))
    packages={'':{'name':'web-fixture','version':'1.0.0'}}
    packages.update({'node_modules/'+name:{'version':version} for name,version in (tools or {}).items()})
    put(repo,'apps/web/package-lock.json',json.dumps({'name':'web-fixture','version':'1.0.0','lockfileVersion':3,'packages':packages}))
    put(repo,'apps/web/src/app.js','module.exports = 1;\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','authored package-check fixture')


def observed(repo):
    base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo).decode().strip()
    return Facts.collect(repo,base_oid=base,paths=['apps/web/src/app.js'],opaque=[])


def profiles(facts):return {p['script']:p for p in facts['checks']['package_scripts'] if p['unit']=='apps/web'}


def test_unknown_framework_is_visible_executable_with_pending_semantic_proof(cli,repo,tmp_path):
    js(repo,{'test':'unknown-runner execute','lint':'eslint .'},tools={'eslint':'9.17.0'})
    start(cli);_,manifest=discover(cli,tmp_path,['apps/web/src/app.js'])
    checks=[g for g in manifest['gates'] if g.get('package_script')]
    test=next(g for g in checks if g['package_script']['script']=='test')
    assert test['argv']==['npm','run','test'] and test['cwd']=='apps/web' and test['applicability']=='APPLICABLE'
    assert test['package_script']['execution']['status']=='READY'
    assert test['package_script']['semantic_coverage']['status']=='UNVERIFIED'
    assert any(g['code']=='package-semantic-unverified' for g in manifest['service_coverage']['assessment']['gaps'])
    assert not any(g.get('argv')==['npm','run','typecheck'] for g in manifest['gates'])
    assert test['source']['path']=='apps/web/package.json' and test['source']['job']=='scripts.test'


@pytest.mark.parametrize('body,ready',[
    ('vitest',False),('vitest --watch',False),('vitest run',True),('jest --watchAll',False),
    ('custom-runner check',True),('node scripts/custom.js',True),('echo checked || true',False),
    ('npm run test',False),('eslint --fix .',False),('curl https://example.invalid',False),
])
def test_original_execution_closure_is_not_rewritten(repo,body,ready):
    js(repo,{'test':body})
    value=profiles(observed(repo))['test']
    assert (value['execution']['status']=='READY') is ready
    assert value['body_sha256']==__import__('hashlib').sha256(body.encode()).hexdigest()


def test_hooks_and_nested_script_provenance_are_bound(repo):
    js(repo,{'pretest':'custom-precheck','test':'npm run inner','preinner':'custom-inner-pre','inner':'node --test','posttest':'custom-postcheck'})
    profile=profiles(observed(repo))['test']
    assert profile['execution']['status']=='READY'
    assert {s['selector'] for s in profile['closure']['sources']}=={'scripts.pretest','scripts.test','scripts.preinner','scripts.inner','scripts.posttest'}
    assert profile['semantic_coverage']['status']=='UNVERIFIED'


@pytest.mark.parametrize('defect',['multiple','contradiction','missing','member'])
def test_manager_ambiguity_and_missing_membership_stay_pending(repo,defect):
    js(repo,{'check':'node --check src/app.js'})
    if defect=='multiple':put(repo,'apps/web/pnpm-lock.yaml','lockfileVersion: "9.0"\nimporters: {}\n')
    elif defect=='contradiction':
        p=repo/'apps/web/package.json';data=json.loads(p.read_text());data['packageManager']='pnpm@10.0.0';p.write_text(json.dumps(data))
    elif defect=='missing':(repo/'apps/web/package-lock.json').unlink()
    else:
        p=repo/'apps/web/package-lock.json';data=json.loads(p.read_text());data['packages']={};p.write_text(json.dumps(data))
    assert profiles(observed(repo))['check']['execution']['status']=='PENDING'


def test_manager_identity_can_be_sourced_from_one_lock_without_inventing_declaration(repo):
    js(repo,{'check':'node --check src/app.js'},manager=None)
    profile=profiles(observed(repo))['check']
    assert profile['manager']['name']=='npm' and profile['manager']['version'] is None
    assert profile['execution']['status']=='READY'


def test_pnpm_workspace_membership_lock_and_manager_are_sourced(repo):
    js(repo,{'test':'vitest run'},tools={'vitest':'2.1.9'})
    (repo/'apps/web/package-lock.json').unlink()
    put(repo,'package.json',json.dumps({'name':'workspace','packageManager':'pnpm@10.0.0','workspaces':['apps/*']}))
    p=repo/'apps/web/package.json';data=json.loads(p.read_text());data.pop('packageManager');p.write_text(json.dumps(data))
    put(repo,'pnpm-workspace.yaml','packages:\n  - apps/*\n')
    put(repo,'pnpm-lock.yaml','lockfileVersion: "9.0"\nimporters:\n  apps/web:\n    devDependencies:\n      vitest:\n        specifier: "^2.0.0"\n        version: 2.1.9\n')
    profile=profiles(observed(repo))['test']
    assert profile['execution']['status']=='READY'
    assert profile['manager']['name']=='pnpm' and profile['manager']['versions']['vitest']=='2.1.9'
    assert {'package.json','pnpm-lock.yaml','pnpm-workspace.yaml','apps/web/package.json'}<={r['path'] for r in profile['manager']['inputs']}


@pytest.mark.parametrize('command,tool,config,expected',[
    ('node --check src/app.js',None,{},'SUPPORTED'),('node --test',None,{},'SUPPORTED'),
    ('tsc --noEmit','typescript',{'tsconfig.json':'{"compilerOptions":{"strict":true}}'},'SUPPORTED'),
    ('tsc --noEmit','typescript',{'tsconfig.json':'{"compilerOptions":{"noCheck":true}}'},'UNVERIFIED'),
    ('eslint . --max-warnings 0','eslint',{},'SUPPORTED'),('eslint . --fix','eslint',{},'UNVERIFIED'),
    ('vitest run','vitest',{},'SUPPORTED'),('vitest','vitest',{},'UNVERIFIED'),
    ('vitest run --passWithNoTests','vitest',{},'UNVERIFIED'),('jest --ci --runInBand','jest',{},'SUPPORTED'),
    ('jest --watch','jest',{},'UNVERIFIED'),('jest --listTests','jest',{},'UNVERIFIED'),
])
def test_common_framework_invocation_contracts(command,tool,config,expected):
    # These are parser/lock contracts, not claims of live framework execution.
    versions={tool:{'typescript':'5.6.3','eslint':'9.17.0','vitest':'3.0.0','jest':'30.0.0'}[tool]} if tool else {}
    value=Profiles.classify([shlex.split(command)],'apps/web',versions,config.get)
    assert value['status']==expected


@pytest.mark.parametrize('body,output,code,verdict',[
    ('node --test','# pass 2\n# fail 0\n',0,'VERIFIED'),
    ('node --test','# pass 0\n# fail 0\n',0,'EMPTY'),
    ('node --test','# pass 1\n# fail 1\n',0,'FAIL'),
    ('vitest run','Test Files 2 passed (2)\n',0,'EMPTY'),
    ('vitest run','Tests 2 passed (2)\n',0,'VERIFIED'),
    ('jest --ci','Test Suites: 1 passed\nTests: 2 passed\n',0,'VERIFIED'),
    ('jest --ci','No tests found\n',0,'EMPTY'),
    ('unknown-runner','finished but no known summary\n',0,'UNVERIFIED'),
    ('unknown-runner','Tests 2 passed\n',0,'UNVERIFIED'),
    ('jest --ci','Tests 2 passed\n',127,'INFRA'),
])
def test_execution_exit_and_semantic_test_evidence_are_separate(repo,body,output,code,verdict):
    js(repo,{'test':body},tools={'jest':'30.0.0','vitest':'3.0.0'})
    profile=profiles(observed(repo))['test']
    value=Profiles.result(profile,code,output)
    assert value['semantic_verdict']==verdict
    assert value['execution']==('PASS' if code==0 else 'INFRA')


def test_syntax_one_file_does_not_certify_unchecked_member_sources(repo):
    js(repo,{'check':'node --check src/app.js'})
    put(repo,'apps/web/src/unchecked.js','const unchecked=1;\n')
    value=observed(repo)
    assert any(g['code']=='missing-verification-check' and g['unit']=='apps/web' for g in value['checks']['gaps'])


def test_python_apps_require_positive_supported_literal_context(cli,repo,tmp_path):
    from test_service_coverage import unit
    seed(repo);unit(repo,'apps/pdf');start(cli)
    _,manifest=discover(cli,tmp_path,['services/a/src/app.py'])
    assert not any(g['code']=='unsupported-unit-layout' and g.get('unit')=='apps/pdf' for g in manifest['service_coverage']['assessment']['gaps'])
    (repo/'apps/pdf/pyproject.toml').unlink()
    _,manifest=discover(cli,tmp_path,['services/a/src/app.py'])
    assert any(g['code']=='missing-manifest' and g.get('unit')=='apps/pdf' for g in manifest['service_coverage']['assessment']['gaps'])


def test_closed_profile_and_exact_gate_reconstruction_refuse_tampering(cli,repo,tmp_path):
    from hook_regression_helpers import invoke
    js(repo,{'test':'unknown-runner check'});start(cli);_,manifest=discover(cli,tmp_path,['apps/web/src/app.js'])
    gate=next(g for g in manifest['gates'] if g.get('package_script'))
    changed=copy.deepcopy(gate['package_script']);changed['extra']='authority'
    with pytest.raises(ValueError):Package.validate_profile(changed)
    gate['package_script']['semantic_coverage']['status']='SUPPORTED'
    path=tmp_path/'tampered-package.json';path.write_text(json.dumps(manifest))
    before=json.loads(cli('show')[1]);invoke(cli,'gates','plan','--file',str(path),ok=False)
    assert json.loads(cli('show')[1])==before


def test_custom_script_is_selected_only_from_exact_existing_ci(repo):
    js(repo,{'quality:custom':'unknown-runner verify'})
    value=observed(repo)
    profile=profiles(value)['quality:custom'];identifier='package-'+Facts.digest(['apps/web','quality:custom'])[:16]
    assert not any(o['id']==identifier for o in value['checks']['obligations'])
    ci={'obligations':[{'source_path':'.gitlab-ci.yml','source_sha256':'a'*64,'job':'web-quality','execution':{'verification_chain':{'requirements':[{'argv':['npm','--prefix','apps/web','run','quality:custom'],'cwd':'.'}]}}}]}
    Package.select_ci(value,ci)
    assert any(b['id']==identifier and b['argv']==['npm','run','quality:custom'] for b in value['checks']['bindings'])
    assert profile['selected_by'][0]['selector']=='web-quality'
    assert any(g['code']=='package-semantic-unverified' for g in value['checks']['gaps'])


@pytest.mark.parametrize('mode,expected', [('tests','PASS'),('empty','EMPTY'),('unknown','INFRA'),('failed-output','FAIL')])
def test_real_offline_node_package_gate_keeps_execution_and_semantics_separate(cli,repo,tmp_path,monkeypatch,mode,expected):
    import shutil
    if not shutil.which('node') or not shutil.which('npm'):pytest.skip('local Node/npm unavailable; no installation is attempted')
    version=subprocess.check_output(['npm','--version'],text=True).strip()
    if mode in {'tests','empty'}:script='node --test'
    else:script='node scripts/custom.js'
    js(repo,{'test':script},manager='npm@'+version)
    if mode=='tests':put(repo,'apps/web/test/basic.test.js',"const test=require('node:test'); const assert=require('node:assert/strict'); test('own fixture',()=>assert.equal(1,1));\n")
    elif mode=='unknown':put(repo,'apps/web/scripts/custom.js',"console.log('completed custom verification');\n")
    elif mode=='failed-output':put(repo,'apps/web/scripts/custom.js',"console.log('Tests: 1 failed');\n")
    _git(repo,'add','.');_git(repo,'commit','--allow-empty','-qm','offline Node check inputs')
    monkeypatch.setenv('npm_config_offline','true');monkeypatch.setenv('npm_config_audit','false');monkeypatch.setenv('npm_config_fund','false')
    monkeypatch.setenv('npm_config_cache',str(tmp_path/'npm-cache'))
    start(cli);_,manifest=discover(cli,tmp_path,['apps/web/src/app.js'])
    gate=next(g for g in manifest['gates'] if g.get('package_script',{}).get('script')=='test')
    result=cli('gates','run','--stage','base','--id',gate['id'])
    state=json.loads(cli('show')[1]);entry=next(e for e in reversed(state['evidence']['entries']) if e['area']=='gate-base-'+gate['id'])
    assert entry['status']==expected,(result,entry)
    assert entry['package_script_result']['execution']=='PASS'
    assert entry['package_script_result']['exit']==0
    assert entry['setup'] and all(row['status']=='PASS' for row in entry['setup'])
    assert not entry.get('execution_receipt') and not entry.get('execution_association')
    if expected=='PASS':
        assert entry['package_script_result']['semantic_verdict']=='VERIFIED'
        assert entry['package_script_result']['counts']['passed']>=1
        assert Package.result_gaps(gate['package_script'],entry)==[]
    else:
        assert entry['package_script_result']['semantic_verdict'] in {'UNVERIFIED','EMPTY','FAIL'}
        assert Package.result_gaps(gate['package_script'],entry)
    # A real successful package process does not erase strict v1 uncertainty,
    # unrelated root obligations, or establish final approval by itself.
    assert state['status']=='RUNNING' and not state.get('approval')



def test_modern_js_sources_and_configuration_remain_in_current_base_provenance(repo):
    js(repo,{'lint':'eslint .'},tools={'eslint':'9.17.0'})
    put(repo,'apps/web/src/module.mjs','export const value=1;\n')
    put(repo,'apps/web/src/module.cjs','module.exports=1;\n')
    put(repo,'apps/web/eslint.config.mjs','export default [];\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','modern JS capture fixture')
    base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo).decode().strip()
    value=Facts.collect(repo,base_oid=base,paths=['apps/web/src/module.mjs'],opaque=[])
    provenance={(r['snapshot'],r['path']) for r in value['scan_inputs']}
    for path in ('apps/web/src/module.mjs','apps/web/src/module.cjs','apps/web/eslint.config.mjs'):
        assert ('current',path) in provenance and ('base',path) in provenance
    assert any(u['code']=='uncertain-language-impact' and u['path'].endswith('.mjs') for u in value['dependencies']['uncertainties'])
    profile=profiles(value)['lint']
    assert any(r['path']=='apps/web/eslint.config.mjs' for r in profile['closure']['inputs'])



@pytest.mark.parametrize('argv',[
    ['npm','--workspace','web-fixture','run','quality:custom'],
    ['npm','--workspace=web-fixture','run','quality:custom'],
    ['npm','--prefix','apps/web','run','quality:custom'],
    ['pnpm','--filter','web-fixture','run','quality:custom'],
    ['pnpm','-C','apps/web','quality:custom'],
])
def test_ci_custom_workspace_selection_uses_literal_actual_member(repo,argv):
    js(repo,{'quality:custom':'unknown-runner verify'})
    ci={'obligations':[{'source_path':'.gitlab-ci.yml','source_sha256':'a'*64,'job':'quality','execution':{'verification_chain':{'requirements':[{'argv':argv,'cwd':'.'}]}}}]}
    selected=Package.selected_custom(ci,'apps/web',argv[0],'web-fixture')
    assert selected['quality:custom'][0]['supported_invocation'] is True
    assert Package.selected_custom(ci,'apps/other',argv[0],'other')=={}



def test_python_app_workspace_is_a_container_and_root_ci_remains_required(cli,repo,tmp_path):
    from test_service_coverage import unit
    seed(repo);unit(repo,'apps/pdf')
    put(repo,'pyproject.toml','[tool.uv.workspace]\nmembers=["services/*","packages/*","apps/*"]\n')
    put(repo,'.gitlab-ci.yml','root-quality:\n  script: python3 -m compileall -q apps/pdf\n')
    start(cli);_,manifest=discover(cli,tmp_path,['services/a/src/app.py'])
    units=manifest['service_coverage']['semantic_requirements']['units']
    assert '.' not in {u['id'] for u in units}
    assert any(g.get('source',{}).get('job')=='root-quality' and g['required'] for g in manifest['gates'] if g.get('source'))


def test_forwarded_ci_arguments_are_visible_pending_and_never_removed(repo):
    js(repo,{'quality:custom':'unknown-runner'})
    value=observed(repo)
    ci={'obligations':[{'source_path':'.gitlab-ci.yml','source_sha256':'a'*64,'job':'quality','execution':{'verification_chain':{'requirements':[{'argv':['npm','--prefix','apps/web','run','quality:custom','--','--run'],'cwd':'.'}]}}}]}
    Package.select_ci(value,ci)
    profile=profiles(value)['quality:custom']
    assert profile['selected_by'] and profile['execution']['status']=='PENDING'
    identifier='package-'+Facts.digest(['apps/web','quality:custom'])[:16]
    assert not any(b['id']==identifier for b in value['checks']['bindings'])
    assert any(o['id']==identifier for o in value['checks']['obligations'])


def test_successful_execution_cannot_forge_semantic_result_or_use_global_framework(repo,tmp_path):
    js(repo,{'test':'unknown-runner'})
    profile=profiles(observed(repo))['test']
    log=tmp_path/'actual.log';log.write_text('AL PACKAGE CHECK OUTPUT\nunknown summary\n')
    raw=b'unknown summary\n';metadata={'schema_version':1,'sha256':__import__('hashlib').sha256(raw).hexdigest(),'bytes':len(raw),'complete':True,'truncated':False,'timed_out':False}
    value=Package.observed_result(profile,0,raw,metadata)
    from al_package_output import write_metadata
    log.write_text('');write_metadata(log,'RESULT',value)
    entry={'exit':0,'log':str(log),'package_script_result':copy.deepcopy(value)}
    entry['package_script_result']['semantic_verdict']='VERIFIED'
    assert Package.result_gaps(profile,entry)
    known=copy.deepcopy(profile)
    known['semantic_coverage']={'status':'SUPPORTED','records':[{'profile':'jest','kind':'test','targets':['apps/web'],'tool':'jest','version':'30.0.0','config':[]}],'kind':'test','reasons':[]}
    assert Package.runtime_tools(repo,known)['qualification_reasons']



def test_qualified_typecheck_does_not_cover_unchecked_javascript_or_excluded_ts(repo):
    js(repo,{'typecheck':'tsc --noEmit'},tools={'typescript':'5.6.3'})
    put(repo,'apps/web/src/typed.ts','export const typed: number=1;\n')
    put(repo,'apps/web/tsconfig.json','{"compilerOptions":{"strict":true},"exclude":["src/typed.ts"]}')
    value=observed(repo)
    profile=profiles(value)['typecheck'];assert profile['semantic_coverage']['status']=='SUPPORTED'
    assert any(g['code']=='missing-verification-check' and g['unit']=='apps/web' for g in value['checks']['gaps'])



@pytest.mark.parametrize('lock',['not-json','[]','{"packages":[1,2]}'])
def test_malformed_lock_preserves_authored_check_as_pending_inventory(repo,lock):
    js(repo,{'test':'unknown-runner'})
    put(repo,'apps/web/package-lock.json',lock)
    profile=profiles(observed(repo))['test']
    assert profile['script']=='test' and profile['execution']['status']=='PENDING'
    assert any(r['path']=='apps/web/package-lock.json' for r in profile['manager']['inputs'])


def test_local_result_parser_handles_color_and_explicit_no_tests_without_rewriting(repo):
    js(repo,{'test':'jest --ci'},tools={'jest':'30.0.0'})
    profile=profiles(observed(repo))['test']
    value=Profiles.result(profile,0,'\x1b[32mTest Suites: 1 passed\x1b[0m\n\x1b[32mTests: 2 passed\x1b[0m\n')
    assert value['semantic_verdict']=='VERIFIED' and value['counts']['passed']==2
    unknown=copy.deepcopy(profile);unknown['intent']='check';unknown['semantic_coverage']={'status':'UNVERIFIED','records':[],'kind':'check','reasons':['unqualified framework']}
    assert Profiles.result(unknown,0,'No tests found')['semantic_verdict']=='EMPTY'



def test_changed_materialized_script_cannot_claim_current_profile_or_start_setup(repo):
    js(repo,{'test':'node --test'})
    profile=profiles(observed(repo))['test']
    Package.prepare_setup(repo,profile)
    package=repo/'apps/web/package.json';value=json.loads(package.read_text());value['scripts']['test']='node scripts/different.js';package.write_text(json.dumps(value))
    from al_check_setup import SetupError
    with pytest.raises(SetupError,match='materialized source'):
        Package.prepare_setup(repo,profile)
