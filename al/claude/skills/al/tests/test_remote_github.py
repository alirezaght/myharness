"""Mock public API shapes prove refusal semantics, not live GitHub qualification."""
import copy
import json
import sys
from pathlib import Path
import pytest
import al_ci_github as CI
import al_remote_github as Remote
import al_remote_gates
import al_evidence
from al_gate_process import Owner
from conftest import _git
from test_ci_conventional import project,WORKFLOW


def configured(repo):
    project(repo)
    _git(repo,'remote','add','origin','https://github.com/example/project.git')
    body=WORKFLOW.replace('uv run pytest','uv run pytest -m "not browser"').replace('      - run: uv python install','      - run: sudo apt-get update && sudo apt-get install -y ripgrep\n      - run: uv python install')
    (repo/'.github/workflows/quality.yml').write_text(body)
    _git(repo,'add','.');_git(repo,'commit','-qm','literal remote quality')
    return body


def inventory(repo):return CI.github_inventory(repo,['ordinary_project/__init__.py'],'merge_request_quality')


def gate_for(repo):
    configured(repo);job=inventory(repo)['obligations'][0];execution=job['execution']
    assert job['applicability']=='APPLICABLE',job
    return {**execution,'source':{'path':job['source_path'],'job':job['job'],'sha256':job['source_sha256']},'remote':{'provider':'github','repository_id':42,'run_id':11,'attempt':2,'job_id':9}}


def fixtures(gate,sha):
    source=gate['remote_source'];base='https://api.github.com/repos/example/project/actions/'
    repo={'id':42,'full_name':'example/project','name':'project','owner':{'login':'example'}}
    run={'id':11,'run_attempt':2,'repository':repo,'head_repository':repo,'head_sha':sha,'event':'push','path':source['workflow_path'],'workflow_id':7,'status':'completed','conclusion':'success'}
    steps=[{'number':1,'name':'Set up job'},*source['steps'],{'number':len(source['steps'])+2,'name':'Complete job'}]
    job={'id':9,'run_id':11,'run_attempt':2,'head_sha':sha,'name':source['job_name'],'status':'completed','conclusion':'success','url':base+'jobs/9','run_url':base+'runs/11','labels':[source['runner_label']],'steps':[{**s,'status':'completed','conclusion':'success'} for s in steps]}
    return {'repos/example/project':repo,'repos/example/project/actions/runs/11':run,'repos/example/project/actions/runs/11/attempts/2':copy.deepcopy(run),'repos/example/project/actions/runs/11/attempts/2/jobs?per_page=100&page=1':{'total_count':1,'jobs':[copy.deepcopy(job)]},'repos/example/project/actions/jobs/9':job}


def execute(repo,tmp_path,monkeypatch,gate,data,*,mode='normal',stage='candidate',timeout=5,mutation=None,base_oid=None):
    monkeypatch.setattr(al_evidence,'_commit',lambda *args:None)
    stub=tmp_path/'stub.py';payload=tmp_path/'responses.json';payload.write_text(json.dumps(data))
    stub.write_text('import json,sys,time\ndata=json.load(open(sys.argv[1]));mode=sys.argv[2]\nif mode=="timeout":time.sleep(2)\nif mode=="auth":print("SECRET",file=sys.stderr);sys.exit(1)\nif mode=="oversize":print("x"*2200000);sys.exit(0)\nif mode=="malformed":print("SECRETinvalid");sys.exit(0)\nif mode=="duplicate-json":print(\'{"id":42,"id":42}\');sys.exit(0)\nif mode=="nonfinite-json":print(\'{"id":NaN}\');sys.exit(0)\nprint(json.dumps(data[sys.argv[3]]))\n')
    calls={}
    class StubOwner(Owner):
        def spawn(self,argv,**kwargs):
            assert argv[:6]==['gh','api','--hostname','github.com','--method','GET']
            assert argv[6] in data
            calls[argv[6]]=calls.get(argv[6],0)+1
            if mutation:mutation(argv[6],calls[argv[6]],data,payload)

            proc=super().spawn([sys.executable,str(stub),str(payload),mode,argv[6]],**kwargs)
            if mode=='interrupt':raise KeyboardInterrupt
            return proc
    sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    log=tmp_path/'remote.log'
    with StubOwner(log) as owner:
        result=al_remote_gates.execute_remote({'identity':{'merge_base':base_oid or sha}},gate,stage,log,timeout,'test',{},owner)
        assert owner.proc is None or owner.proc.poll() is not None
    assert 'SECRET' not in log.read_text()
    return result



def assert_attestation(result):
    assert result[:2]==('INFRA',126),result
    provenance=result[2]
    assert provenance['semantics_version']==2 and provenance['pytest_guard_status']=='UNAVAILABLE'
    proof=provenance['ci_job_attestation']
    assert proof['kind']=='CI_JOB_ATTESTATION' and proof['status']=='PASS'
    assert proof['pytest_guard_status']=='UNAVAILABLE'


def assert_no_attestation(result):
    assert result[0]=='INFRA'
    assert result[2].get('ci_job_attestation',{}).get('status')!='PASS'


def test_two_platforms_are_distinct_remote_required_source_chains(repo):
    body=configured(repo)
    mac=body.split('jobs:\n')[1].replace('quality:','quality-macos:').replace('ubuntu-latest','macos-latest').replace('sudo apt-get update && sudo apt-get install -y ripgrep','brew install ripgrep')
    (repo/'.github/workflows/quality.yml').write_text(body+mac)
    jobs=inventory(repo)['obligations']
    assert len(jobs)==2 and all(j['applicability']=='APPLICABLE' for j in jobs)
    assert {j['execution']['remote_source']['runner_label'] for j in jobs}=={'ubuntu-latest','macos-latest'}
    assert all(j['execution']['execution_kind']=='remote' and j['execution']['argv']==['gh','api'] for j in jobs)
    assert all('sudo' not in j['execution']['argv'] and 'brew' not in j['execution']['argv'] for j in jobs)
    assert jobs[0]['execution']['remote_source']!=jobs[1]['execution']['remote_source']


@pytest.mark.parametrize('change',[
    lambda s:s.replace('install -y ripgrep','install -y other'),
    lambda s:s.replace('uv sync --locked','uv sync'),
    lambda s:s.replace('actions/checkout@v4','actions/checkout@v4\n        with:\n          ref: main'),
    lambda s:s.replace('uv build','uv build || true'),
    lambda s:s.replace('uv run ruff check .','uv run ruff check other'),
    lambda s:s.replace('uv python install','echo unknown && uv python install'),
    lambda s:s.replace('runs-on: ubuntu-latest','runs-on: macos-latest'),
    lambda s:s.replace('- run: uv build','- name: Build\n        run: uv build'),
    lambda s:s.replace('- run: uv build','- continue-on-error: true\n        run: uv build'),
])
def test_unknown_or_incomplete_source_refuses(repo,change):
    body=configured(repo);(repo/'.github/workflows/quality.yml').write_text(change(body))
    assert inventory(repo)['obligations'][0]['applicability']=='UNRESOLVED'


@pytest.mark.parametrize('path',['uv.lock','pyproject.toml'])
def test_missing_dependency_refuses(repo,path):
    configured(repo);(repo/path).unlink()
    assert inventory(repo)['obligations'][0]['applicability']=='UNRESOLVED'


def test_exact_mocked_push_evidence_and_closed_provenance(repo,tmp_path,monkeypatch):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    status,code,proof=execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha))
    assert_attestation((status,code,proof))
    attestation=proof['ci_job_attestation']
    assert attestation['repository_id']==42 and attestation['attempt']==2 and len(attestation['responses'])==7


@pytest.mark.parametrize('mutation',[
    'repo','owner','headrepo','workflow','sha','attempt','event','runstatus','jobid','jobname','runner','jobattempt','stepfailed','stepskipped','stepmissing','steporder','duplicatejob','duplicatename','truncated','malformedlisting','directmismatch',
])
def test_foreign_or_incomplete_remote_evidence_refuses(repo,tmp_path,monkeypatch,mutation):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip();data=fixtures(gate,sha)
    run=data['repos/example/project/actions/runs/11'];job=data['repos/example/project/actions/jobs/9'];listing=data['repos/example/project/actions/runs/11/attempts/2/jobs?per_page=100&page=1']
    if mutation=='repo':data['repos/example/project']['id']=99
    elif mutation=='owner':data['repos/example/project']['owner']['login']='foreign'
    elif mutation=='headrepo':run['head_repository']={'id':1}
    elif mutation=='workflow':run['path']='.github/workflows/other.yml'
    elif mutation=='sha':run['head_sha']='0'*40
    elif mutation=='attempt':run['run_attempt']=3
    elif mutation=='event':run['event']='pull_request'
    elif mutation=='runstatus':run['conclusion']='neutral'
    elif mutation=='jobid':job['id']=8
    elif mutation=='jobname':job['name']='foreign'
    elif mutation=='runner':job['labels']=['self-hosted']
    elif mutation=='jobattempt':job['run_attempt']=1
    elif mutation=='stepfailed':job['steps'][3]['conclusion']='failure'
    elif mutation=='stepskipped':job['steps'][3]['conclusion']='skipped'
    elif mutation=='stepmissing':del job['steps'][3]
    elif mutation=='steporder':job['steps'][3:5]=reversed(job['steps'][3:5])
    elif mutation=='duplicatejob':listing.update(total_count=2,jobs=listing['jobs']*2)
    elif mutation=='duplicatename':listing.update(total_count=2,jobs=[listing['jobs'][0],{**listing['jobs'][0],'id':10}])
    elif mutation=='truncated':listing['total_count']=101
    elif mutation=='malformedlisting':listing['jobs']={}
    elif mutation=='directmismatch':job['steps'][-1]['name']='unknown wrapper'
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,data))


@pytest.mark.parametrize('mode',['auth','oversize','malformed','duplicate-json','nonfinite-json','timeout'])
def test_transport_failures_redacted_and_settled(repo,tmp_path,monkeypatch,mode):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha),mode=mode,timeout=.05 if mode=='timeout' else 5))


@pytest.mark.parametrize('mutation',['dirty','untracked','source','origin','base','selection'])
def test_local_commit_source_identity_refuses(repo,tmp_path,monkeypatch,mutation):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    if mutation=='dirty':(repo/'ordinary_project/__init__.py').write_text('CHANGED=1\n')
    elif mutation=='untracked':(repo/'untracked').write_text('x')
    elif mutation=='source':gate['remote_source']['verification_chain']['source_chain'][0]['sha256']='0'*64
    elif mutation=='origin':_git(repo,'remote','set-url','origin','https://github.com/foreign/project.git')
    elif mutation=='base':gate['remote_source']['verification_chain']['source_chain'][0]['sha256']='1'*64
    elif mutation=='selection':gate['remote']['attempt']=True
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha)))


def test_source_bound_discovery_requires_both_platforms_and_selection(cli,repo,tmp_path):
    import al_gate_discovery as Discovery
    import al_service_coverage as Coverage
    from test_service_coverage import start,discover
    body=configured(repo)
    mac=body.split('jobs:\n')[1].replace('quality:','quality-macos:').replace('ubuntu-latest','macos-latest').replace('sudo apt-get update && sudo apt-get install -y ripgrep','brew install ripgrep')
    (repo/'.github/workflows/quality.yml').write_text(body+mac)
    _git(repo,'add','.');_git(repo,'commit','-qm','both platforms')
    start(cli);st,manifest=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    bindings=[b for b in manifest['service_coverage']['assessment']['required_bindings'] if b['category']=='ci']
    assert len(bindings)==2
    assessment=manifest['service_coverage']['assessment']
    assert assessment['outcome']=='incomplete'
    assert any(g['code']=='missing-verification-check' for g in assessment['gaps'])
    assert all('verification-root' not in b['obligation_ids'] for b in bindings)
    gates=[g for g in manifest['gates'] if g.get('remote_source')]
    assert len(gates)==2
    for gate in gates:
        gate['remote']={'provider':'github','repository_id':42,'run_id':11,'attempt':2,'job_id':9}
    Discovery.validate_manifest(st,manifest)
    # Removing a platform, changing source facts or forging a local mapping
    # cannot be repaired by remote selection or a declaration-only edit.
    for mutation in ('omit','source','local','selection'):
        forged=copy.deepcopy(manifest);gate=next(g for g in forged['gates'] if g.get('remote_source'))
        if mutation=='omit':forged['gates'].remove(gate)
        elif mutation=='source':gate['remote_source']['runner_label']='macos-latest'
        elif mutation=='local':gate['execution_kind']='local';gate['argv']=['true']
        else:gate['remote']['attempt']=True
        with pytest.raises((SystemExit,ValueError)):Discovery.validate_manifest(st,forged)


@pytest.mark.parametrize('kind',['source','origin','attempt','repository','workflow-membership'])
def test_changes_during_lookup_refuse(repo,tmp_path,monkeypatch,kind):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    def mutate(endpoint,count,data,payload):
        if endpoint=='repos/example/project/actions/runs/11' and count==2:
            if kind=='source':(repo/'uv.lock').write_text('changed')
            elif kind=='origin':_git(repo,'remote','set-url','origin','https://github.com/foreign/project.git')
            elif kind=='workflow-membership':(repo/'.github/workflows/new.yml').write_text('on: push')
            elif kind=='attempt':data[endpoint]['run_attempt']=3
            elif kind=='repository':data['repos/example/project']['id']=99
            payload.write_text(json.dumps(data))
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha),mutation=mutate))


def test_complete_bounded_pagination(repo,tmp_path,monkeypatch):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip();data=fixtures(gate,sha)
    first='repos/example/project/actions/runs/11/attempts/2/jobs?per_page=100&page=1'
    data[first]['total_count']=101
    data[first]['jobs'].extend({'id':n,'name':'other-'+str(n)} for n in range(100,199))
    data[first.replace('&page=1','&page=2')]={'total_count':101,'jobs':[{'id':199,'name':'other-199'}]}
    assert_attestation(execute(repo,tmp_path,monkeypatch,gate,data))


def test_uncertain_cleanup_remains_unavailable(repo,tmp_path,monkeypatch):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    original=Owner.completion
    monkeypatch.setattr(Owner,'completion',lambda owner:dict(original(owner),complete=False))
    # Transport itself still settles; only evidence reuse refuses the uncertain proof.
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha)))


def test_source_bound_disjoint_collect_ignore_is_recorded_and_rechecked(repo):
    import al_pytest_profile as Profile
    configured(repo)
    (repo/'tests').mkdir();(repo/'tests/test_case.py').write_text('def test_case():assert True\n')
    config=repo/'pyproject.toml';config.write_text(config.read_text()+'[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    (repo/'conftest.py').write_text('collect_ignore = [".claude"]\n')
    value=Profile.profile(repo,['-m','not browser'],lambda p:(repo/p).read_bytes())
    assert value['conftest']['disjoint_collect_ignore']==[{'source':'conftest.py','paths':['.claude'],'disjoint_collection_roots':['tests']}]
    before=inventory(repo)['obligations'][0]['execution']['remote_source']
    assert 'conftest.py' in value['source_dependencies']
    (repo/'conftest.py').write_text('collect_ignore = ["tests"]\n')
    assert inventory(repo)['obligations'][0]['applicability']=='UNRESOLVED'
    assert before['verification_chain']['requirements'][2]['pytest_profile']==value


@pytest.mark.parametrize('declaration',[
    'collect_ignore=["tests"]','collect_ignore=["tests/test_case.py"]','collect_ignore=["."]',
    'collect_ignore=["../outside"]','collect_ignore=["/absolute"]','collect_ignore=["tests/../.claude"]',
    'collect_ignore=[".claude/"]','collect_ignore=[".cla*"]','collect_ignore=[".cla?de"]',
    'collect_ignore_glob=[".claude"]','collect_ignore=helper()','collect_ignore=[];collect_ignore.append("tests")',
    'pytest_plugins=[]','def pytest_collection_modifyitems(items):items.clear()',
])
def test_overlapping_or_dynamic_collection_controls_remain_unavailable(repo,declaration):
    import al_pytest_profile as Profile
    configured(repo);(repo/'tests').mkdir();(repo/'tests/test_case.py').write_text('def test_case():pass\n')
    config=repo/'pyproject.toml';config.write_text(config.read_text()+'[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    (repo/'conftest.py').write_text(declaration+'\n')
    with pytest.raises(ValueError):Profile.profile(repo,['-m','not browser'],lambda p:(repo/p).read_bytes())


def test_default_root_and_nested_ancestor_ignore_refuse(repo):
    import al_pytest_sources as Sources
    configured(repo);(repo/'conftest.py').write_text('collect_ignore=[".claude"]\n')
    with pytest.raises(ValueError):Sources.fixture_sources(repo,['.'],lambda p:(repo/p).read_bytes())
    (repo/'conftest.py').write_text('collect_ignore=["tests"]\n')
    with pytest.raises(ValueError):Sources.fixture_sources(repo,['tests/nested'],lambda p:(repo/p).read_bytes())


@pytest.mark.parametrize('origin',['https://example.com/example/project','ssh://git@github.com/example/project.git','https://github.com/example/../project','https://github.com/example/project?token=SECRET','local/repo'])
def test_unsupported_or_rebound_origin_refuses(repo,origin):
    configured(repo);_git(repo,'remote','set-url','origin',origin)
    assert inventory(repo)['obligations'][0]['applicability']=='UNRESOLVED'


def test_exact_macos_mock_evidence(repo,tmp_path,monkeypatch):
    body=configured(repo);path=repo/'.github/workflows/quality.yml'
    path.write_text(body.replace('ubuntu-latest','macos-latest').replace('sudo apt-get update && sudo apt-get install -y ripgrep','brew install ripgrep'))
    _git(repo,'add','.');_git(repo,'commit','-qm','macOS recipe')
    job=inventory(repo)['obligations'][0]
    gate={**job['execution'],'source':{'path':job['source_path'],'job':job['job'],'sha256':job['source_sha256']},'remote':{'provider':'github','repository_id':42,'run_id':11,'attempt':2,'job_id':9}}
    sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    assert_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha)))


def test_absent_selection_is_remote_required_and_cannot_pass(repo,tmp_path,monkeypatch):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip();del gate['remote']
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha)))


def test_interrupted_spawn_settles_without_raw_pipe_fallback(repo,tmp_path,monkeypatch):
    import al_harness_process
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    children=[];popen=al_harness_process.popen
    def tracked(*args,**kwargs):
        proc=popen(*args,**kwargs);children.append(proc);return proc
    monkeypatch.setattr(al_harness_process,'popen',tracked)
    with pytest.raises(KeyboardInterrupt):execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha),mode='interrupt')
    assert children and all(proc.poll() is not None and proc.stdout is None for proc in children)
    assert 'SECRET' not in (tmp_path/'remote.log').read_text()


def test_base_must_match_independent_pinned_commit(repo,tmp_path,monkeypatch):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    assert_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha),stage='base'))
    base=Remote.S.git_bytes('rev-parse','HEAD^').decode().strip()
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,base),stage='base',base_oid=base))


@pytest.mark.parametrize('name',['.ruff.toml','ruff.toml','.python-version','pytest.ini','tests/conftest.py'])
def test_new_ignored_effective_config_during_lookup_refuses(repo,tmp_path,monkeypatch,name):
    gate=gate_for(repo)
    if name=='.python-version':
        (repo/name).unlink();_git(repo,'add','-u');_git(repo,'commit','-qm','no explicit version')
        job=inventory(repo)['obligations'][0];gate.update(job['execution'])
    # Bind ignore rules before observation; ignored semantic inputs still refuse.
    with (repo/'.gitignore').open('a') as stream:stream.write(name+'\n')
    _git(repo,'add','.gitignore');_git(repo,'commit','-qm','ignored config fixture')
    sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    def mutate(endpoint,count,data,payload):
        if endpoint=='repos/example/project/actions/runs/11' and count==2:
            path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('')
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha),mutation=mutate))


def test_missing_committed_source_is_closed_unavailable(repo,tmp_path,monkeypatch):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    original=Remote.S.git_bytes
    def missing(*args,**kwargs):
        if args[0]=='show':raise SystemExit('SECRET missing source')
        return original(*args,**kwargs)
    monkeypatch.setattr(Remote.S,'git_bytes',missing)
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha)))


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('claim',['UNAVAILABLE','PASS'])
def test_historical_or_self_asserted_provider_pass_never_qualifies_guard(repo,tmp_path,monkeypatch,version,claim):
    import al_gates as Gates
    gate=gate_for(repo);gate.update(id='remote',applicability='APPLICABLE',env_names=[],sources=[],scope=['.'])
    gate['remote_source']['semantics_version']=version
    log=tmp_path/'historical.log';log.write_text('historical provider success')
    evidence={'id':'E1','status':'PASS','run':'run','log':str(log),'log_sha256':Gates.L.file_sha(log),'env':[],'sources':[],'argv':gate['argv'],'cwd':'.','scope':['.'],'remote_provenance':{'provider':'github','semantics_version':version,'pytest_guard_status':claim}}
    state={'run_id':'run','gate_manifests':[{'sha256':'manifest'}],'gate_runs':[{'gate':'remote','stage':'base','manifest':'manifest','evidence':'E1','fingerprint':'base','env_fingerprint':'env','sources':[],'execution_kind':'remote'}]}
    monkeypatch.setattr(Gates.L,'find',lambda *args:evidence)
    monkeypatch.setattr(Gates.L,'scope_fingerprint',lambda *args:'env')
    monkeypatch.setattr(Gates.ReviewState,'gaps',lambda *args:[])
    assert Gates.gate_result(state,{'base_oid':'base'},gate,'base','base')!='valid'
    assert Remote.result_gaps(gate,evidence)


def test_changed_semantics_cannot_reuse_old_source_contract(repo):
    gate=gate_for(repo);del gate['remote_source']['semantics_version']
    with pytest.raises(ValueError):Remote.selection(gate)


def test_source_profile_guard_drift_cannot_create_attestation(repo,tmp_path,monkeypatch):
    gate=gate_for(repo);sha=Remote.S.git_bytes('rev-parse','HEAD').decode().strip()
    gate['remote_source']['verification_chain']['requirements'][2]['pytest_profile']['execution_guard']='optional'
    assert_no_attestation(execute(repo,tmp_path,monkeypatch,gate,fixtures(gate,sha)))
