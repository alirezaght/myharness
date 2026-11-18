"""Actual guarded pytest execution and fail-closed platform policy qualification."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
import al_pytest_policy as Policy
import al_pytest_profile as Profile
import al_pytest_runner as Runner

SCRIPTS=Path(Runner.__file__).resolve().parent
CONTEXT={k:k+'-fixture' for k in ('run','task','manifest','gate','stage','base','candidate','invocation')}


def fixture_project(root,body,platforms=None):
    (root/'tests').mkdir(exist_ok=True)
    (root/'tests/test_case.py').write_text(body)
    import importlib.metadata
    (root/'uv.lock').write_text('[[package]]\nname="pytest"\nversion="'+importlib.metadata.version('pytest')+'"\n')
    config='[tool.pytest.ini_options]\ntestpaths=["tests"]\n'
    if platforms is not None:config+='[tool.al.pytest-verification]\npolicy="platform-aware-v1"\nplatforms='+json.dumps(platforms)+'\n'
    (root/'pyproject.toml').write_text(config)


def execute(root,*,context=CONTEXT,pytest_args=()):
    resources={name:{'path':str(SCRIPTS/name),'sha256':hashlib.sha256((SCRIPTS/name).read_bytes()).hexdigest()} for name in Policy.RESOURCE_NAMES}
    env={**os.environ,'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
    env.pop('PYTEST_ADDOPTS',None);env.pop('PYTEST_PLUGINS',None);env.pop(Policy.CONTEXT_ENV,None)
    if context is not None:env[Policy.CONTEXT_ENV]=json.dumps(context)
    proc=subprocess.run([sys.executable,'-I','-B',str(SCRIPTS/'al_pytest_runner.py'),'--resources',json.dumps(resources),'--','-q',*pytest_args],cwd=root,env=env,capture_output=True,text=True)
    lines=[line.removeprefix('AL_PYTEST_EXECUTION ') for line in proc.stdout.splitlines() if line.startswith('AL_PYTEST_EXECUTION ')]
    return proc,json.loads(lines[0]) if len(lines)==1 else None


def test_actual_current_platform_pass_and_source_proven_os_skip(tmp_path):
    opposite='darwin' if sys.platform!='darwin' else 'linux'
    fixture_project(tmp_path,'import sys\nimport pytest\n@pytest.mark.skipif(sys.platform != '+repr(opposite)+', reason="other OS")\ndef test_other():assert False\ndef test_real():assert True\n',[sys.platform])
    proc,report=execute(tmp_path)
    assert proc.returncode==0,proc.stdout+proc.stderr
    q=report['qualification']
    assert q['complete'] and q['passed']==1 and q['not_applicable']==1
    assert {n['status'] for n in q['nodes']}=={'PASS','NOT_APPLICABLE'}
    assert q['context']==CONTEXT and 'tests/test_case.py' in report['inputs']


def test_actual_strict_policy_still_refuses_os_skips(tmp_path):
    fixture_project(tmp_path,'import os\nimport pytest\n@pytest.mark.skipif(os.name == '+repr(os.name)+', reason="OS")\ndef test_other():pass\n')
    proc,report=execute(tmp_path)
    assert proc.returncode==126 and report['qualification']['policy']=={'version':'strict-v1'}
    assert report['qualification']['not_applicable']==0


@pytest.mark.parametrize('declaration',[
    '@pytest.mark.skip(reason="macOS only")',
    '@pytest.mark.skipif(True, reason="macOS only")',
    '@pytest.mark.skipif("sys.platform != \\"darwin\\"", reason="OS")',
    '@pytest.mark.skipif(__import__("shutil").which("missing-native-provider-fixture") is None, reason="native unavailable")',
    '@pytest.mark.xfail(reason="known")',
    '@pytest.mark.skipif(sys.platform != "impossible", reason="unknown OS")',
    '@pytest.mark.skipif((lambda: True)(), reason="dynamic")',
])
def test_actual_unknown_native_and_xfail_do_not_qualify(tmp_path,declaration):
    fixture_project(tmp_path,'import pytest\nimport sys\n'+declaration+'\ndef test_other():assert False\n',[sys.platform])
    proc,report=execute(tmp_path)
    assert proc.returncode!=0
    if report:assert not report['qualification']['complete'] and report['qualification']['not_applicable']==0


def test_actual_marker_alias_does_not_qualify(tmp_path):
    fixture_project(tmp_path,'import pytest\nimport os\nonly=pytest.mark.skipif(os.name == '+repr(os.name)+', reason="OS")\n@only\ndef test_other():pass\n',[sys.platform])
    proc,report=execute(tmp_path)
    assert proc.returncode==126 and report['qualification']['not_applicable']==0


def test_actual_missing_platform_execution_is_unverified(tmp_path):
    opposite='darwin' if sys.platform!='darwin' else 'linux'
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform,opposite])
    proc,report=execute(tmp_path)
    assert proc.returncode==126 and report['qualification']['verdict']=='UNVERIFIED'
    assert any(r['platform']==opposite and r['status']=='UNVERIFIED' for r in report['qualification']['obligations'])


def test_actual_missing_protected_context_is_unverified(tmp_path):
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform])
    proc,report=execute(tmp_path,context=None)
    assert proc.returncode==126 and 'protected task/candidate/base launch context missing' in report['qualification']['errors']


@pytest.mark.parametrize('body',[
    'from pathlib import Path\ndef test_mutation():\n Path("tests/test_case.py").write_text("def test_other():pass\\n")\n',
    'from pathlib import Path\ndef test_mutation():\n Path("tests/new_fixture.json").write_text("[]")\n',
    'import pytest\ndef test_plugin(request):\n request.config.pluginmanager.register(object(),"late-fixture")\n',
])
def test_actual_source_fixture_and_plugin_drift_do_not_qualify(tmp_path,body):
    fixture_project(tmp_path,body,[sys.platform])
    proc,report=execute(tmp_path)
    assert proc.returncode!=0
    if report:assert not report['qualification']['complete']


def test_synthetic_observed_metadata_is_a_unit_fixture_only():
    # This fixture calls the pure AST classifier; it never changes real process
    # metadata and cannot issue a guarded execution or satisfy another platform.
    raw=b'import sys\nimport pytest\n@pytest.mark.skipif(sys.platform != "darwin", reason="OS")\ndef test_case():pass\n'
    def item(value):return SimpleNamespace(cls=None,name='test_case',originalname='test_case',iter_markers=lambda name:[SimpleNamespace(args=(value,),kwargs={'reason':'OS'})] if name=='skipif' else [])
    assert Policy.source_skip_proof(raw,item(True),{'sys_platform':'linux','os_name':'posix'})
    assert Policy.source_skip_proof(raw,item(False),{'sys_platform':'darwin','os_name':'posix'}) is None
    assert Policy.source_skip_proof(raw,item(True),{'sys_platform':'darwin','os_name':'posix'}) is None


@pytest.mark.parametrize('mutation',['missing','duplicate','extra','skip_call','teardown_failure','xfail'])
def test_exact_stage_and_identity_accounting(mutation):
    reports=[{'nodeid':'case','when':stage,'outcome':'passed','xfail':False} for stage in ('setup','call','teardown')]
    if mutation=='missing':reports.pop()
    if mutation=='duplicate':reports.append(copy.deepcopy(reports[1]))
    if mutation=='extra':reports.append({**reports[1],'nodeid':'unknown'})
    if mutation=='skip_call':reports[1]['outcome']='skipped'
    if mutation=='teardown_failure':reports[2]['outcome']='failed'
    if mutation=='xfail':reports[1]['xfail']=True
    nodes,errors=Policy.accounting(['case'],reports,{'case':[{'value':True}]},{'version':Policy.PLATFORM})
    assert errors or nodes[0]['status'] not in {'PASS','NOT_APPLICABLE'}


def test_plain_json_ci_green_and_old_guard_cannot_be_admitted(tmp_path):
    requirement={'pytest_profile':{'outcome_policy':{'version':Policy.PLATFORM}},'guard_resources':{}}
    log=tmp_path/'log';log.write_text('AL_PYTEST_EXECUTION '+json.dumps({'conclusion':'success','tests':1})+'\n')
    entry={'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'exit':0,'pytest_guard_completion':{'complete':True},'pytest_guard_context':CONTEXT}
    assert Policy.result_gaps([requirement],entry)
    assert Policy.result_gaps([requirement],{**entry,'remote_provenance':{'conclusion':'success'}})
    assert Policy.result_gaps([requirement],{**entry,'pytest_guard_completion':{}})


def test_actual_guarded_transcript_admission_and_stale_inventory_refusal(tmp_path):
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform])
    proc,report=execute(tmp_path)
    assert proc.returncode==0
    log=tmp_path/'observed.log';log.write_text(proc.stdout)
    requirement={'pytest_profile':report['profile'],'guard_resources':report['resources']}
    entry={'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'exit':0,'pytest_guard_completion':{'complete':True},'pytest_guard_context':CONTEXT}
    assert Policy.result_gaps([requirement],entry)==[]
    for field in ('context','binding_sha256','policy','complete'):
        modified=copy.deepcopy(report)
        modified['qualification'][field]=None
        log.write_text('AL_PYTEST_EXECUTION '+json.dumps(modified)+'\n')
        entry['log_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
        assert Policy.result_gaps([requirement],entry)
    stale=copy.deepcopy(requirement);stale['guard_resources']['al_pytest_policy.py']['sha256']='0'*64
    log.write_text(proc.stdout);entry['log_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
    assert Policy.result_gaps([stale],entry)
    log.write_text(proc.stdout+proc.stdout);entry['log_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
    assert Policy.result_gaps([requirement],entry)


def test_source_declaration_is_the_only_policy_selector(tmp_path,monkeypatch):
    fixture_project(tmp_path,'def test_real():pass\n')
    monkeypatch.setenv('AL_PYTEST_POLICY','platform-aware-v1')
    read=lambda name:(tmp_path/name).read_bytes()
    assert Profile.profile(tmp_path,[],read)['outcome_policy']=={'version':Policy.STRICT}
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform])
    value=Profile.profile(tmp_path,[],read)
    assert value['outcome_policy']['version']==Policy.PLATFORM and 'tests/test_case.py' in value['source_dependencies']
    (tmp_path/'pyproject.toml').write_text((tmp_path/'pyproject.toml').read_text().replace('platform-aware-v1','allow-skips'))
    with pytest.raises(ValueError,match='unsupported pytest outcome policy'):Profile.profile(tmp_path,[],read)


def test_actual_module_and_class_os_predicates_are_supported(tmp_path):
    fixture_project(tmp_path,'import os\nimport pytest\npytestmark=pytest.mark.skipif(os.name == '+repr(os.name)+', reason="OS")\nclass TestCases:\n def test_other(self):assert False\n',[sys.platform])
    proc,report=execute(tmp_path)
    assert proc.returncode==0 and report['qualification']['not_applicable']==1


def test_collection_marker_binding_and_outcome_identity_refuse_drift(tmp_path):
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform])
    proc,report=execute(tmp_path);assert proc.returncode==0
    requirement={'pytest_profile':report['profile'],'guard_resources':report['resources']}
    log=tmp_path/'observed.log'
    for change in ('collection','marker','outcome','matrix','input'):
        modified=copy.deepcopy(report)
        if change=='collection':modified['collection'].append('tests/test_case.py::missing')
        elif change=='marker':modified['profile']['marker_expression']='excluded'
        elif change=='outcome':modified['reports'].append(copy.deepcopy(modified['reports'][0]))
        elif change=='matrix':modified['qualification']['policy']['platforms'].append('linux' if sys.platform!='linux' else 'darwin')
        else:modified['inputs']['tests/test_case.py']='0'*64
        log.write_text('AL_PYTEST_EXECUTION '+json.dumps(modified)+'\n')
        entry={'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'exit':0,'pytest_guard_completion':{'complete':True},'pytest_guard_context':CONTEXT}
        assert Policy.result_gaps([requirement],entry),change


def test_protected_gate_context_comes_from_task_and_source_binding(cli,repo,tmp_path,monkeypatch):
    from test_ci_pytest_profile import configured
    from test_service_coverage import start,discover
    from al_task import task_hash
    import al_gates as Gates
    configured(repo)
    p=repo/'pyproject.toml';p.write_text(p.read_text()+'[tool.al.pytest-verification]\npolicy="platform-aware-v1"\nplatforms='+json.dumps([sys.platform])+'\n')
    start(cli);st,manifest=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    binding=next(b for b in manifest['service_coverage']['assessment']['required_bindings'] if b['category']=='ci')
    gate={'id':'fixture-gate','execution_signature':binding['execution_signature']}
    requirements=Policy.gate_requirements(manifest,gate)
    assert len(requirements)==1 and requirements[0]['pytest_profile']['outcome_policy']['version']==Policy.PLATFORM
    monkeypatch.setenv(Policy.CONTEXT_ENV,json.dumps({'run':'forged'}))
    context=Policy.launch_context(st,'manifest',gate,'candidate','base','candidate','invocation')
    assert context['run']==st['run_id'] and context['task']==task_hash(st)
    # A provider-only legacy PASS with a real log cannot become guarded credit.
    log=tmp_path/'legacy.log';log.write_text('CI green\n')
    entry={'id':'e','status':'PASS','log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'env':[],'sources':[]}
    gate.update(applicability='APPLICABLE',env_names=[],execution_kind='local')
    run={'gate':gate['id'],'stage':'base','manifest':'manifest','evidence':'e','fingerprint':'base','sources':[],'env_fingerprint':'env'}
    state={**st,'gate_manifests':[{'sha256':'manifest'}],'gate_runs':[run]}
    monkeypatch.setattr(Gates.L,'find',lambda *_:entry)
    monkeypatch.setattr(Gates.L,'scope_fingerprint',lambda *_:'env')
    monkeypatch.setattr(Gates.ReviewState,'gaps',lambda *_:[])
    assert Gates.gate_result(state,manifest,gate,'base','base')!='valid'


@pytest.mark.parametrize('declaration',[
    'pytest.skip("native unavailable", allow_module_level=True)',
    'pytest.importorskip("al_guard_fixture_missing_native_provider_c63b0f")',
])
def test_actual_collection_skip_cannot_disappear_behind_a_passing_test(tmp_path,declaration):
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform])
    (tmp_path/'tests/test_native.py').write_text('import pytest\n'+declaration+'\ndef test_native():assert False\n')
    proc,report=execute(tmp_path)
    assert proc.returncode==126,proc.stdout+proc.stderr
    assert report['outcomes']['passed']==1
    assert report['qualification']['verdict']=='UNVERIFIED' and not report['qualification']['complete']
    assert report['qualification']['not_applicable']==0
    assert any(r['nodeid']=='tests/test_native.py' and r['outcome']=='skipped' for r in report['collection_proof']['reports'])
    # Even pretending the process exited zero cannot make this collector inventory
    # acceptable at the gate's guarded-transcript boundary.
    log=tmp_path/'observed.log';log.write_text(proc.stdout)
    requirement={'pytest_profile':report['profile'],'guard_resources':report['resources']}
    entry={'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'exit':0,'pytest_guard_completion':{'complete':True},'pytest_guard_context':CONTEXT}
    assert 'collection remains unverified' in Policy.result_gaps([requirement],entry)[0]


@pytest.mark.parametrize('change',['missing','unfinished','skipped','failed','missing_child','duplicate_collector'])
def test_gate_requires_current_complete_collection_proof(tmp_path,change):
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform])
    proc,report=execute(tmp_path);assert proc.returncode==0
    requirement={'pytest_profile':report['profile'],'guard_resources':report['resources']}
    modified=copy.deepcopy(report);proof=modified['collection_proof']
    if change=='missing':modified.pop('collection_proof')
    elif change=='unfinished':proof['finished']=False
    elif change in {'skipped','failed'}:proof['reports'][0]['outcome']=change
    elif change=='missing_child':
        for row in proof['reports']:row['children']=[n for n in row['children'] if n not in modified['collection']]
    else:proof['reports'].append(copy.deepcopy(proof['reports'][0]))
    log=tmp_path/'observed.log';log.write_text('AL_PYTEST_EXECUTION '+json.dumps(modified)+'\n')
    entry={'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'exit':0,'pytest_guard_completion':{'complete':True},'pytest_guard_context':CONTEXT}
    assert 'collect' in Policy.result_gaps([requirement],entry)[0]


def test_historical_strict_qualification_does_not_gain_platform_interpretation():
    reports=[{'nodeid':'case','when':stage,'outcome':'passed','xfail':False} for stage in ('setup','call','teardown')]
    observed={'sys_platform':sys.platform,'os_name':os.name}
    strict=Policy.qualification({'version':Policy.STRICT},['case'],reports,{},observed,None,{})
    assert strict['complete'] and strict['verdict']=='VERIFIED'
    platform=Policy.qualification({'version':Policy.PLATFORM,'platforms':[sys.platform]},['case'],reports,{},observed,CONTEXT,{})
    assert not platform['complete'] and 'collection proof missing' in platform['errors'][0]


def test_collection_graph_requires_all_sibling_collectors_and_accounted_leaves():
    def proof(rows):return {'schema_version':1,'finished':True,'reports':[{'nodeid':node,'outcome':'passed','children':children} for node,children in rows]}
    rows=[('', ['tests']),('tests',['tests/test_pass.py','tests/test_native.py']),('tests/test_pass.py',['tests/test_pass.py::test_real'])]
    # A passing sibling cannot hide a native module whose collectreport vanished.
    assert Policy.collection_gaps(proof(rows),['tests/test_pass.py::test_real'],[])
    rows.append(('tests/test_native.py',['tests/test_native.py::test_native']))
    # The native item must then appear in selected or declared marker deselection.
    assert Policy.collection_gaps(proof(rows),['tests/test_pass.py::test_real'],[])
    assert not Policy.collection_gaps(proof(rows),['tests/test_pass.py::test_real'],['tests/test_native.py::test_native'])
    rows.append(('tests/test_orphan.py',[]))
    assert Policy.collection_gaps(proof(rows),['tests/test_pass.py::test_real'],['tests/test_native.py::test_native'])


@pytest.mark.parametrize('roots',[['.'],['tests'],['tests/test_case.py','tests/test_empty.py']])
def test_actual_runner_collection_graph_handles_roots_nested_and_empty_collectors(tmp_path,roots):
    fixture_project(tmp_path,'class TestCases:\n def test_real(self):pass\n',[sys.platform])
    (tmp_path/'tests/test_empty.py').write_text('# A real empty collected module is legitimate.\n')
    path=tmp_path/'pyproject.toml';path.write_text(path.read_text().replace('testpaths=["tests"]','testpaths='+json.dumps(roots)))
    proc,report=execute(tmp_path)
    assert proc.returncode==0,proc.stdout+proc.stderr
    proof=report['collection_proof']
    assert Policy.collection_gaps(proof,report['collection'],report['outcomes'].get('deselected',[]))==[]
    assert any(r['nodeid']=='tests/test_empty.py' and r['children']==[] for r in proof['reports'])
    assert any(r['nodeid']=='tests/test_case.py::TestCases' for r in proof['reports'])


@pytest.mark.parametrize('change',['missing_sibling_collector','unaccounted_leaf','missing_session','cycle'])
def test_gate_refuses_incomplete_or_unaccounted_collection_branches(tmp_path,change):
    fixture_project(tmp_path,'def test_real():pass\n',[sys.platform])
    proc,report=execute(tmp_path);assert proc.returncode==0
    requirement={'pytest_profile':report['profile'],'guard_resources':report['resources']}
    modified=copy.deepcopy(report);rows=modified['collection_proof']['reports']
    directory=next(r for r in rows if r['nodeid']=='tests')
    if change=='missing_sibling_collector':directory['children'].append('tests/test_native.py')
    elif change=='unaccounted_leaf':next(r for r in rows if r['nodeid']=='tests/test_case.py')['children'].append('tests/test_case.py::test_disappeared')
    elif change=='missing_session':rows[:]=[r for r in rows if r['nodeid']!='']
    else:directory['children'].append('')
    log=tmp_path/'observed.log';log.write_text('AL_PYTEST_EXECUTION '+json.dumps(modified)+'\n')
    entry={'log':str(log),'log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),'exit':0,'pytest_guard_completion':{'complete':True},'pytest_guard_context':CONTEXT}
    assert 'collect' in Policy.result_gaps([requirement],entry)[0]


def test_actual_collection_graph_accounts_for_declared_marker_deselection(tmp_path):
    fixture_project(tmp_path,'import pytest\n@pytest.mark.chosen\ndef test_selected():pass\ndef test_deselected():pass\n',[sys.platform])
    path=tmp_path/'pyproject.toml';path.write_text(path.read_text().replace('[tool.pytest.ini_options]','[tool.pytest.ini_options]\nmarkers=["chosen: fixture marker"]'))
    proc,report=execute(tmp_path,pytest_args=['-m','chosen'])
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert report['collection']==['tests/test_case.py::test_selected']
    assert report['outcomes']['deselected']==['tests/test_case.py::test_deselected']
    assert Policy.collection_gaps(report['collection_proof'],report['collection'],report['outcomes']['deselected'])==[]
