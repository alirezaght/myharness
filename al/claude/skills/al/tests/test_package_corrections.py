"""Public independent-review counterexamples; no real credentials or providers."""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

import al_package_profiles as Profiles
from test_package_checks import js
from test_service_coverage import start,discover,put
from conftest import _git


@pytest.mark.parametrize('command,tool',[('./node --test',None),('/other/node --test',None),('./eslint .','eslint')])
def test_authored_executable_alias_cannot_borrow_basename_qualification(command,tool):
    versions={tool:'9.17.0'} if tool else {}
    assert Profiles.classify([shlex.split(command)],'apps/web',versions,lambda _:None)['status']=='UNVERIFIED'


@pytest.mark.parametrize('name,body',[
    ('eslint.config.js',"const fs=require('node:fs'); export default JSON.parse(fs.readFileSync('/arbitrary/config.json'));"),
    ('.eslintrc.json','{"plugins":["unqualified-external-plugin"]}'),
])
def test_config_requires_positive_finite_closure(name,body):
    config={name:body}
    assert Profiles.classify([['eslint','.']],'apps/web',{'eslint':'9.17.0'},config.get)['status']=='UNVERIFIED'


def test_unqualified_framework_version_is_not_universally_supported():
    assert Profiles.classify([['jest','--ci']],'apps/web',{'jest':'1.2.3'},lambda _:None)['status']=='UNVERIFIED'


def test_execution_environment_is_checked_before_setup_is_attempted(cli,repo,tmp_path,monkeypatch):
    import al_check_setup as Setup
    js(repo,{'test':'unknown-runner'})
    start(cli);_,manifest=discover(cli,tmp_path,['apps/web/src/app.js'])
    gate=next(g for g in manifest['gates'] if g.get('package_script',{}).get('script')=='test')
    attempted=[]
    def forbidden(*args,**kwargs):
        attempted.append('setup');raise Setup.SetupError('public fixture setup was attempted')
    monkeypatch.setattr(Setup,'execute',forbidden)
    monkeypatch.setenv('npm_config_ignore_scripts','true')
    cli('gates','run','--stage','base','--id',gate['id'])
    assert attempted==[]


def test_public_credential_sentinel_never_enters_new_durable_package_output(cli,repo,tmp_path,monkeypatch):
    import shutil
    if not shutil.which('node') or not shutil.which('npm'):pytest.skip('local offline Node/npm unavailable')
    sentinel='LC_PUBLIC_CREDENTIAL_SENTINEL_0123'
    version=subprocess.check_output(['npm','--version'],text=True).strip()
    js(repo,{'test':'node scripts/custom.js --credential='+sentinel},manager='npm@'+version)
    put(repo,'apps/web/scripts/custom.js',"console.log(process.argv[2]); console.error('"+sentinel+"');\n")
    _git(repo,'add','.');_git(repo,'commit','-qm','public credential output fixture')
    monkeypatch.setenv('npm_config_offline','true');monkeypatch.setenv('npm_config_audit','false');monkeypatch.setenv('npm_config_fund','false');monkeypatch.setenv('npm_config_cache',str(tmp_path/'npm-cache'))
    start(cli);_,manifest=discover(cli,tmp_path,['apps/web/src/app.js'])
    gate=next(g for g in manifest['gates'] if g.get('package_script',{}).get('script')=='test')
    response=cli('gates','run','--stage','base','--id',gate['id'])
    assert sentinel not in str(response)
    state=json.loads(cli('show')[1]);entry=next(e for e in reversed(state['evidence']['entries']) if e['area']=='gate-base-'+gate['id'])
    assert sentinel not in Path(entry['log']).read_text()
    # New package artifacts are profile/metadata packets. Existing generic source
    # archives are a separate explicitly documented surface, not a forensic claim.
    assert sentinel not in json.dumps(manifest)
    assert sentinel not in json.dumps(entry)


def test_local_node_and_manager_shadows_cannot_borrow_host_qualification(repo):
    import al_package_checks as Package
    js(repo,{'check':'node --check src/app.js'})
    from test_package_checks import profiles,observed
    profile=profiles(observed(repo))['check']
    put(repo,'apps/web/node_modules/.bin/node','#!/bin/sh\nexit 0\n')
    (repo/'apps/web/node_modules/.bin/node').chmod(0o755)
    assert 'unqualified package-local Node shadow' in Package.runtime_tools(repo,profile)['qualification_reasons']
    put(repo,'apps/web/node_modules/.bin/npm','#!/bin/sh\nexit 0\n')
    (repo/'apps/web/node_modules/.bin/npm').chmod(0o755)
    assert 'unqualified package-local manager shadow' in Package.runtime_tools(repo,profile)['qualification_reasons']


def test_nonempty_config_is_refused_by_metadata_before_child_spawn(repo,tmp_path,monkeypatch):
    import al_package_checks as Package
    from al_check_setup import SetupError
    from test_package_checks import profiles,observed
    js(repo,{'check':'node --check src/app.js'});profile=profiles(observed(repo))['check']
    config=tmp_path/'public.npmrc';config.write_text('public fixture opaque configuration\n')
    monkeypatch.setenv('npm_config_userconfig',str(config))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('preflight must not spawn'))
    with pytest.raises(SetupError,match='nonempty'):
        Package.preflight(repo,profile)


def test_mixed_root_selects_exact_child_package_setup_without_changing_default(repo):
    import al_package_checks as Package
    import al_check_setup as Setup
    from test_package_checks import profiles,observed
    js(repo,{'check':'node --check src/app.js'})
    put(repo,'pyproject.toml','[project]\nname="public-root"\nversion="1.0"\n')
    put(repo,'uv.lock','version = 1\n')
    profile=profiles(observed(repo))['check']
    assert Setup.plan(repo,'apps/web')['manager']=='uv'
    assert Package.prepare_setup(repo,profile)['manager']=='npm'
    assert Setup.plan(repo,'apps/web',package_root='apps/web')['cwd']=='apps/web'


@pytest.mark.parametrize('body',['unknown-install-runner','node -e "process.exit(0)"'])
def test_unknown_install_lifecycle_stays_pending_before_spawn(repo,body):
    import al_package_checks as Package
    from al_check_setup import SetupError
    from test_package_checks import profiles,observed
    js(repo,{'test':'node --test','install':body})
    profile=profiles(observed(repo))['test']
    with pytest.raises(SetupError,match='lifecycle'):
        Package.preflight(repo,profile)


@pytest.mark.parametrize('failure',[False,True])
def test_offline_setup_and_check_capture_never_persist_public_sentinel(repo,tmp_path,monkeypatch,failure):
    import shutil
    import al_check_setup as Setup
    import al_package_checks as Package
    from al_package_output import PrivateOwner,Capture
    from test_package_checks import profiles,observed
    if not shutil.which('node') or not shutil.which('npm'):pytest.skip('offline Node/npm unavailable')
    sentinel='LC_PUBLIC_CREDENTIAL_SENTINEL_0123'
    version=subprocess.check_output(['npm','--version'],text=True).strip()
    js(repo,{'test':'node --test','prepare':'echo '+sentinel},manager='npm@'+version)
    monkeypatch.setenv('npm_config_offline','true');monkeypatch.setenv('npm_config_audit','false');monkeypatch.setenv('npm_config_fund','false')
    profile=profiles(observed(repo))['test'];Package.preflight(repo,profile)
    rows=[];log=tmp_path/'private.log';capture=Capture()
    with PrivateOwner(log) as owner:
        Setup.execute(repo,'apps/web',owner,log,30,rows,package_root='apps/web',private_output=capture)
        proc=owner.spawn(['node','-e',"console.log('"+sentinel+"');console.error('"+sentinel+"');process.exit("+str(int(failure))+")"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        code,raw,metadata=capture.run(proc,10)
        assert sentinel.encode() in raw and code==int(failure)
        from al_package_output import write_metadata
        write_metadata(log,'FIXTURE-OUTPUT',metadata)
    assert rows[0]['status']=='PASS'
    assert sentinel not in log.read_text() and sentinel not in json.dumps(rows)
    # Only output metadata is persisted by the new private package path.
    for line in log.read_text().splitlines():
        payload=json.loads(line.split(' ',3)[3])
        assert sentinel not in json.dumps(payload)


def test_private_capture_budget_is_bounded_and_never_creates_raw_log(tmp_path,monkeypatch):
    import al_package_output as Output
    monkeypatch.setattr(Output,'MAX_BYTES',64)
    log=tmp_path/'private.log'
    with Output.PrivateOwner(log) as owner:
        proc=owner.spawn([__import__('sys').executable,'-c','print("x"*4096)'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        _,raw,metadata=Output.Capture().run(proc,10)
    assert len(raw)<=64 and metadata['truncated'] and not metadata['complete']
    assert not log.exists()


@pytest.mark.parametrize('script,tool,name',[
    ('eslint .','eslint','eslint.config.ts'),('eslint .','eslint','.eslintrc.js'),
    ('vitest run','vitest','vite.config.mts'),('jest --ci','jest','.babelrc.js'),
])
def test_unsupported_standard_auto_configuration_cannot_be_silently_ignored(repo,script,tool,name):
    from test_package_checks import profiles,observed
    js(repo,{'test':script},tools={tool:{'eslint':'9.17.0','vitest':'3.0.0','jest':'30.0.0'}[tool]})
    put(repo,'apps/web/'+name,'export default opaqueConfiguration;\n')
    assert profiles(observed(repo))['test']['semantic_coverage']['status']=='UNVERIFIED'


def test_vite_import_cannot_borrow_a_different_package_contract(repo):
    from test_package_checks import profiles,observed
    js(repo,{'test':'vitest run'},tools={'vitest':'3.0.0'})
    put(repo,'apps/web/vitest.config.ts','import { defineConfig } from "vite"; export default defineConfig({"test":{}});')
    assert profiles(observed(repo))['test']['semantic_coverage']['status']=='UNVERIFIED'


def test_script_path_observes_npm_ancestor_bins_outside_checkout(repo):
    import al_package_checks as Package
    from test_package_checks import profiles,observed
    js(repo,{'check':'node --check src/app.js'});profile=profiles(observed(repo))['check']
    shadow=repo.parent/'node_modules/.bin/node';shadow.parent.mkdir(parents=True)
    shadow.write_text('#!/bin/sh\nexit 0\n');shadow.chmod(0o755)
    assert 'unqualified package-local Node shadow' in Package.runtime_tools(repo,profile)['qualification_reasons']


def test_nested_manager_alias_does_not_borrow_script_semantics(repo):
    from test_package_checks import profiles,observed
    js(repo,{'test':'./npm run inner','inner':'node --test'})
    assert profiles(observed(repo))['test']['semantic_coverage']['status']=='UNVERIFIED'


@pytest.mark.parametrize('body',['./node --test','/other/node --test','./eslint .'])
def test_source_authored_alias_keeps_original_executable_identity(repo,body):
    from test_package_checks import profiles,observed
    js(repo,{'test':body},tools={'eslint':'9.17.0'})
    profile=profiles(observed(repo))['test']
    assert profile['execution']['status']=='READY'
    assert profile['semantic_coverage']['status']=='UNVERIFIED'


def test_install_node_hook_requires_preinstallation_resolution_contract(repo):
    import al_package_checks as Package
    from al_check_setup import SetupError
    from test_package_checks import profiles,observed
    js(repo,{'test':'node --test','prepare':'node --version'})
    profile=profiles(observed(repo))['test']
    with pytest.raises(SetupError,match='lifecycle'):
        Package.preflight(repo,profile)


def test_automatic_native_install_context_stays_pending(repo):
    import al_package_checks as Package
    from al_check_setup import SetupError
    from test_package_checks import profiles,observed
    js(repo,{'test':'node --test'})
    put(repo,'apps/web/binding.gyp','{"targets":[]}')
    profile=profiles(observed(repo))['test']
    with pytest.raises(SetupError,match='native-install'):
        Package.preflight(repo,profile)


@pytest.mark.parametrize('mode,expected_semantic,expected_status,expected_execution',[
    ('reported-failure','FAIL','FAIL','PASS'),
    ('empty','EMPTY','EMPTY','PASS'),
    ('process-failure','FAIL','FAIL','FAIL'),
    ('process-infra','INFRA','INFRA','INFRA'),
    ('verified','UNVERIFIED','INFRA','PASS'),
])
def test_unqualified_runtime_preserves_observed_negative_outcome(cli,repo,tmp_path,monkeypatch,mode,expected_semantic,expected_status,expected_execution):
    import shutil
    import al_package_contracts as Contracts
    if not shutil.which('node') or not shutil.which('npm'):pytest.skip('local offline Node/npm unavailable')
    version=subprocess.check_output(['npm','--version'],text=True).strip()
    script='node --test' if mode=='verified' else 'node scripts/result.js'
    js(repo,{'test':script},manager='npm@'+version)
    if mode=='verified':
        put(repo,'apps/web/test/basic.test.js',"const test=require('node:test'); test('own fixture',()=>{});\n")
    else:
        output='Tests: 1 failed' if mode=='reported-failure' else 'No tests found' if mode=='empty' else ''
        code=127 if mode=='process-infra' else 1 if mode=='process-failure' else 0
        put(repo,'apps/web/scripts/result.js','console.log('+json.dumps(output)+');process.exit('+str(code)+');\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','negative outcome qualification fixture')
    monkeypatch.setenv('npm_config_offline','true');monkeypatch.setenv('npm_config_audit','false');monkeypatch.setenv('npm_config_fund','false');monkeypatch.setenv('npm_config_cache',str(tmp_path/'npm-cache'))
    # Contract-only unknown-version fixture; actual authored command/tool are unchanged.
    monkeypatch.setattr(Contracts,'NODE_VERSIONS',set())
    start(cli);_,manifest=discover(cli,tmp_path,['apps/web/src/app.js'])
    gate=next(g for g in manifest['gates'] if g.get('package_script',{}).get('script')=='test')
    response=cli('gates','run','--stage','base','--id',gate['id'])
    state=json.loads(cli('show')[1]);entry=next(e for e in reversed(state['evidence']['entries']) if e['area']=='gate-base-'+gate['id'])
    assert entry.get('log'),response
    assert entry['package_runtime_tools']['qualification_reasons']
    assert entry['package_script_result']['execution']==expected_execution
    assert entry['package_script_result']['semantic_verdict']==expected_semantic
    assert entry['status']==expected_status
    packet=[json.loads(line[len('AL PACKAGE RESULT '):]) for line in Path(entry['log']).read_text().splitlines() if line.startswith('AL PACKAGE RESULT ')]
    assert packet==[entry['package_script_result']]


@pytest.mark.parametrize('config_path,command',[
    ('tsconfig.json','tsc --noEmit'),
    ('apps/tsconfig.json','tsc --noEmit'),
    ('apps/web/config/tsconfig.json','tsc --noEmit --project config/tsconfig.json'),
])
def test_non_member_local_tsconfig_cannot_credit_member_relative_sources(repo,config_path,command):
    from test_package_checks import profiles,observed
    js(repo,{'typecheck':command},tools={'typescript':'5.6.3'})
    (repo/'apps/web/src/app.js').unlink()
    put(repo,'apps/web/src/app.ts','export const member: number=1;\n')
    put(repo,config_path,json.dumps({'files':['src/app.ts'],'compilerOptions':{'strict':True}}))
    put(repo,str(Path(config_path).parent/'src/app.ts'),'export const configRelative: number=1;\n')
    facts=observed(repo);profile=profiles(facts)['typecheck']
    assert any(ref['path']==config_path for ref in profile['closure']['inputs'])
    assert profile['execution']['status']=='READY'
    assert profile['semantic_coverage']['status']=='UNVERIFIED'
    binding=next(row for row in facts['checks']['bindings'] if row.get('package_script',{}).get('script')=='typecheck')
    assert 'verification-apps-web' not in binding['obligation_ids']
    assert any(g['code']=='missing-verification-check' and g.get('unit')=='apps/web' for g in facts['checks']['gaps'])


@pytest.mark.parametrize('boundary',['capture','parse','probe','recheck','setup-version','setup-output','runtime-version'])
@pytest.mark.parametrize('error_type',[ValueError,OSError])
def test_package_inspection_error_finalizes_safe_infra_and_reaps_child(cli,repo,tmp_path,monkeypatch,boundary,error_type):
    _inspection_failure_case(cli,repo,tmp_path,monkeypatch,boundary,error_type)


@pytest.mark.parametrize('outcome',['FAIL','EMPTY'])
def test_post_check_inspection_error_preserves_observed_negative(cli,repo,tmp_path,monkeypatch,outcome):
    _inspection_failure_case(cli,repo,tmp_path,monkeypatch,'recheck',ValueError,outcome=outcome)


def _inspection_failure_case(cli,repo,tmp_path,monkeypatch,boundary,error_type,outcome=None,uncertain=False):
    import os,select,shutil
    import al_package_checks as Package
    from al_package_output import Capture,PrivateOwner
    from al_gate_process import stop
    if not shutil.which('node') or not shutil.which('npm'):pytest.skip('local offline Node/npm unavailable')
    sentinel='LC_PUBLIC_CREDENTIAL_SENTINEL_0123'
    version=subprocess.check_output(['npm','--version'],text=True).strip()
    js(repo,{'test':'node scripts/result.js'},manager='npm@'+version)
    text='Tests: 1 failed' if outcome=='FAIL' else 'No tests found' if outcome=='EMPTY' else sentinel
    body='console.log('+json.dumps(text)+');console.error('+json.dumps(sentinel)+');'
    if boundary=='capture':body+='setInterval(()=>{},1000);'
    put(repo,'apps/web/scripts/result.js',body+'\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','inspection failure fixture')
    monkeypatch.setenv('npm_config_offline','true');monkeypatch.setenv('npm_config_audit','false');monkeypatch.setenv('npm_config_fund','false');monkeypatch.setenv('npm_config_cache',str(tmp_path/'npm-cache'))
    original_capture=Capture.run;original_tools=Package.runtime_tools;original_parse=Package.observed_result;original_completion=PrivateOwner.completion
    children=[];tool_calls=[];injected=[]
    def captured(self,proc,timeout):
        children.append(proc)
        trigger=boundary=='capture' and list(proc.args)==['npm','run','test'] or boundary=='setup-version' and len(children)==1 or boundary=='setup-output' and list(proc.args)[1:]==['ci'] or boundary=='runtime-version' and len(children)==3
        if trigger:
            if boundary=='capture':assert select.select([proc.stdout],[],[],5)[0]
            injected.append(boundary);raise error_type(sentinel)
        return original_capture(self,proc,timeout)
    def tools(*args,**kwargs):
        tool_calls.append(True)
        if boundary=='probe' and len(tool_calls)==1 or boundary=='recheck' and len(tool_calls)==2:
            injected.append(boundary);raise error_type(sentinel)
        return original_tools(*args,**kwargs)
    def parsed(*args,**kwargs):
        if boundary=='parse':injected.append(boundary);raise error_type(sentinel)
        return original_parse(*args,**kwargs)
    def completed(owner):
        if uncertain and injected:return {'complete':False,'group_absent':False,'unresolved':True,'errors':['public synthetic ownership uncertainty']}
        return original_completion(owner)
    monkeypatch.setattr(PrivateOwner,'completion',completed)
    monkeypatch.setattr(Capture,'run',captured);monkeypatch.setattr(Package,'runtime_tools',tools);monkeypatch.setattr(Package,'observed_result',parsed)
    start(cli);_,manifest=discover(cli,tmp_path,['apps/web/src/app.js'])
    gate=next(g for g in manifest['gates'] if g.get('package_script',{}).get('script')=='test')
    try:
        try:response=cli('gates','run','--stage','base','--id',gate['id'])
        except OSError:response=('uncaught OSError',)
        state=json.loads(cli('show')[1]);entry=next(e for e in reversed(state['evidence']['entries']) if e['area']=='gate-base-'+gate['id'])
        (tmp_path/'observed-evidence.json').write_text(json.dumps({'status':entry['status'],'log':entry['log'],'injected':injected,'ended_at':entry.get('ended_at')},indent=2))
        assert injected==[boundary]
        assert entry['status']==(outcome or 'INFRA')
        assert entry.get('ended_at') and entry.get('log')
        log=Path(entry['log'])
        # AL's existing log digest binds the executable bit as well as bytes.
        prefix=b'\0x' if log.stat().st_mode & 0o111 else b'\0-'
        assert entry['log_sha256']==__import__('hashlib').sha256(prefix+log.read_bytes()).hexdigest()
        assert entry['package_failure']['status']=='INFRA'
        assert entry['package_failure']['reason']=='package output/runtime inspection unavailable'
        assert entry['package_failure']['cleanup']['complete'] is (not uncertain)
        assert sentinel not in str(response) and sentinel not in log.read_text() and sentinel not in json.dumps(entry)
        if outcome:assert entry['package_script_result']['semantic_verdict']==outcome
        assert not entry.get('execution_receipt') and not entry.get('execution_association')
        for proc in children:
            assert proc.poll() is not None
            with pytest.raises(ProcessLookupError):os.killpg(proc.pid,0)
        return state,manifest,gate,entry
    finally:
        # The red fixture must never leave its intentionally unhandled live child.
        for proc in children:
            stop(proc)
            for pipe in (proc.stdout,proc.stderr):
                if pipe is not None and not pipe.closed:pipe.close()


@pytest.mark.parametrize('control',[SystemExit,KeyboardInterrupt])
def test_package_inspection_boundary_does_not_catch_control_refusal(tmp_path,control):
    from al_package_output import inspect_call
    log=tmp_path/'uncreated.log'
    def refused():raise control('public control refusal')
    with pytest.raises(control):inspect_call(None,log,'parse',refused)
    assert not log.exists()


@pytest.mark.parametrize('outcome',['FAIL','EMPTY'])
def test_uncertain_package_cleanup_blocks_baseline_and_continuation(cli,repo,tmp_path,monkeypatch,outcome):
    import copy
    import al_gates as Gates
    import al_gate_continuation as Continuation
    from al_writers import operation_gaps
    from al_recovery_subjects import operations
    state,manifest,gate,entry=_inspection_failure_case(cli,repo,tmp_path,monkeypatch,'recheck',ValueError,outcome=outcome,uncertain=True)
    if outcome=='FAIL':
        result=cli('gates','decision','--id',gate['id'],'--action','fix',
                   '--reason','Repair this public fixture baseline failure.',
                   '--source','Explicit synthetic developer baseline-fix decision')
        assert result[0]==0,result
        state=json.loads(cli('show')[1]);entry=next(e for e in state['evidence']['entries'] if e['id']==entry['id'])
    observed=Gates.gate_result(state,manifest,gate,'base',manifest['base_oid'])
    (tmp_path/'cleanup-gate-observation.json').write_text(json.dumps({'result':observed,'status':entry['status'],'cleanup':entry.get('cleanup'),'package_cleanup':entry['package_failure']['cleanup'],'interrupted':entry.get('interrupted')},indent=2))
    assert observed=='unresolved'
    assert entry['status']==outcome and entry['package_script_result']['semantic_verdict']==outcome
    assert entry['cleanup']==entry['package_failure']['cleanup']
    assert entry['cleanup']['unresolved'] and entry['cleanup']['complete'] is False
    assert entry['interrupted'] and not entry.get('reconciliation')
    assert any(entry['id'] in gap for gap in operation_gaps(state))
    assert any(issue['code']=='CHECK_UNRESOLVED' for issue in operations(state))
    for current in (state,copy.deepcopy(state)):
        if current is not state:
            acknowledged=next(e for e in current['evidence']['entries'] if e['id']==entry['id'])
            acknowledged['reconciliation']={'note':'Historical acknowledgement is not positive cleanup proof.'}
        assert Gates.gate_result(current,manifest,gate,'base',manifest['base_oid'])=='unresolved'
        report=Continuation.stage_report(current,'base')
        assert not report['stage_ready'] and gate['id'] in report['unresolved']
        assert 'operation:'+entry['id'] in report['unresolved']
        assert not report['base_decisions'] and not report['accepted_existing_failures']
        assert gate['id'] not in {g['id'] for g in Continuation.select_continuation(current,manifest,'base',requested_id=gate['id'],retry_failed=True)}
        context={'stage':'base','gate':gate['id'],'manifest':current['gate_manifests'][-1]['sha256'],'resume':True,'explicit':True,'retry_failed':True}
        with pytest.raises(SystemExit,match='cleanup.*unresolved'):
            Continuation.admit_destination(current,context)
