"""Closed conventional pytest profile through discovery, planning and admission."""
import copy
import json
from pathlib import Path
import pytest
import al_artifacts as A
import al_service_coverage as Coverage
from al_repository_facts import digest
import al_pytest_profile as Profile
from hook_regression_helpers import invoke,manifest_for
from test_ci_conventional import project,WORKFLOW
from test_service_coverage import start,discover,put
from launcher_context import admit_planning


def configured(repo):
    project(repo);put(repo,'tests/test_case.py','def test_real():assert True\n')
    p=repo/'pyproject.toml';p.write_text(p.read_text()+'[tool.pytest.ini_options]\ntestpaths=["tests"]\naddopts="-q --strict-markers"\n')
    put(repo,'tests/conftest.py','import pytest\n@pytest.fixture\ndef value():return 42\n')


def test_mandated_collection_is_distinct_from_application_scope(cli,repo,tmp_path,monkeypatch):
    configured(repo);monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD','1');start(cli)
    st,m=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert m['service_coverage']['assessment']['outcome']=='identified'
    binding=next(b for b in m['service_coverage']['assessment']['required_bindings'] if b['category']=='ci')
    requirement=next(r for r in binding['verification_chain']['requirements'] if r['purpose']=='test')
    profile=requirement['pytest_profile']
    assert profile['collection_roots']==['tests'] and profile['plugin_mode']=='disabled'
    assert profile['conftest']['files']==['tests/conftest.py']
    assert {'tests/conftest.py','pyproject.toml','uv.lock'}<=set(binding['required_scope'])
    assert set(Profile.ENV)<=set(binding['env_names'])
    assert 'al_pytest_runner.py' in requirement['guarded_argv'][5]
    assert requirement['targets'][0]['collection_roots']==['tests']
    assert requirement['targets'][0]['application_scope']['claim']=='required suite execution, not per-source test coverage'
    value=manifest_for(st,repo,['ordinary_project/__init__.py']);file=tmp_path/'complete.json';file.write_text(json.dumps(value))
    invoke(cli,'gates','plan','--file',str(file));stored=A.read_json(json.loads(cli('show')[1])['gate_manifests'][-1])
    for field in ('env','scope','profile'):
        forged=copy.deepcopy(stored);gate=next(g for g in forged['gates'] if g.get('obligation_ids'))
        if field=='env':gate['env_names'].remove('PYTEST_ADDOPTS')
        elif field=='scope':gate['scope']=['ordinary_project']
        else:
            declaration=forged['service_coverage'];semantics=declaration['semantic_requirements']
            binding=next(b for b in semantics['checks']['bindings'] if b['category']=='ci')
            requirement=next(r for r in binding['verification_chain']['requirements'] if r['purpose']=='test')
            requirement['pytest_profile']['plugin_mode']='forged'
            binding['execution_signature']=digest({k:v for k,v in binding.items() if k!='execution_signature'})
            declaration['semantic_sha256']=digest(semantics)
            declaration['assessment']=Coverage.assess({**semantics,'scan':{'stable':semantics['stable']}})
            for link in declaration['bindings']:
                if link['gate_id']==gate['id']:link['execution_signature']=binding['execution_signature']
            gate['execution_signature']=binding['execution_signature']
        file.write_text(json.dumps(forged));before=json.loads(cli('show')[1]);invoke(cli,'gates','plan','--file',str(file),ok=False)
        assert json.loads(cli('show')[1])==before


@pytest.mark.parametrize('value',['--co','--collect-only','-k selected','-m selected','--lf','--ff','--ignore tests','--deselect tests/test_case.py','-p plugin','-c elsewhere','--rootdir ..','"unterminated','tests'])
def test_unsupported_addopts_is_incomplete_and_reported_unverified(cli,repo,tmp_path,monkeypatch,value):
    configured(repo);monkeypatch.setenv('PYTEST_ADDOPTS',value);start(cli)
    st,m=discover(cli,tmp_path,['ordinary_project/__init__.py']);assert m['service_coverage']['assessment']['outcome']=='incomplete'
    file=tmp_path/'incomplete.json';file.write_text(json.dumps(manifest_for(st,repo,['ordinary_project/__init__.py'])))
    invoke(cli,'gates','plan','--file',str(file))
    # A masked pytest profile is never claimed as verification: it is reported as unverified, not blocking.
    assert any(u['category']=='lint-imports' for u in json.loads(cli('gates','status')[1])['unverified'])


@pytest.mark.parametrize('name,body',[('pytest.ini','[pytest]\n'),('.pytest.ini','[pytest]\n'),('pytest.toml',''),('.pytest.toml',''),('setup.cfg','[tool:pytest]\n'),('tox.ini','[pytest]\n')])
def test_new_or_competing_configuration_invalidates_profile(cli,repo,tmp_path,name,body):
    configured(repo);start(cli);_,before=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    put(repo,name,body);_,after=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert before['service_coverage']['assessment']['outcome']=='identified'
    assert after['service_coverage']['assessment']['outcome']=='incomplete'


@pytest.mark.parametrize('body',['def pytest_collection_modifyitems(items):items.clear()\n','pytest_plugins=[]\n','collect_ignore=["test_case.py"]\n','import unknown_pytest_plugin\n','VALUE=configure_collection()\n'])
def test_unmodeled_conftest_cannot_gain_authority_from_hashes(cli,repo,tmp_path,body):
    configured(repo);put(repo,'tests/conftest.py',body);start(cli)
    _,m=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'


@pytest.mark.parametrize('name',['../outside','missing','tests/test_case.py::test_real','tests/*'])
def test_unsafe_missing_or_unsupported_collection_roots_refuse(repo,name):
    configured(repo)
    with pytest.raises(ValueError):Profile.profile(Path(repo),[name],lambda p:(repo/p).read_bytes())


def test_nonmasking_options_share_one_grammar(repo,monkeypatch):
    configured(repo);monkeypatch.setenv('PYTEST_ADDOPTS','-v -x --disable-warnings');monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD','1')
    value=Profile.profile(Path(repo),['-qq','--strict-config'],lambda p:(repo/p).read_bytes())
    assert value['collection_roots']==['tests']
    with pytest.raises(ValueError):Profile.options(['--collect-only'])


def test_actual_root_configuration_uses_the_generic_profile():
    import al_ci_inventory as CI
    root=Path(__file__).resolve().parents[5]
    # The repository's CI runs its non-browser suite through a literal marker expression.
    value=Profile.profile(root,['-m','not browser'],lambda p:(root/p).read_bytes())
    assert value['collection_roots']==['tests'] and value['marker_expression']=='not browser'
    assert value['conftest']['files']==['tests/conftest.py']
    assert value['conftest']['dependencies']==['tests/conftest.py']
    observed=CI.inventory(root,['tests/conftest.py'],'merge_request_quality')
    job=next(o for o in observed['obligations'] if o['source_path']=='.github/workflows/ci.yml')
    assert job['applicability']=='APPLICABLE' and job['execution']['verification_chain']['complete']
    requirement=next(r for r in job['execution']['verification_chain']['requirements'] if r['purpose']=='test')
    assert requirement['pytest_profile']==value
    assert set(Profile.ENV)<=set(job['execution']['env_names'])


@pytest.mark.parametrize('declaration',[
    'def helper() -> (collect_ignore := ["test_hidden.py"]):pass\n',
    'VALUE: (collect_ignore := ["test_hidden.py"]) = 1\n',
    'def helper(value: unknown.attribute):pass\n',
    'def helper() -> unknown["effect"]:pass\n',
])
def test_executable_annotation_is_not_a_fixture_declaration(repo,declaration):
    configured(repo);put(repo,'tests/conftest.py',declaration)
    with pytest.raises(ValueError,match='nonliteral pytest helper declaration'):
        Profile.profile(repo,[],lambda p:(repo/p).read_bytes())


@pytest.mark.parametrize('expression',['not browser','(slow or browser) and not flaky','browser'])
def test_literal_marker_expressions_are_profiled(repo,expression):
    configured(repo)
    value=Profile.profile(Path(repo),['-q','-m',expression],lambda p:(repo/p).read_bytes())
    assert value['marker_expression']==expression and value['collection_roots']==['tests']


@pytest.mark.parametrize('arguments',[['-m'],['-m','a.b'],['-m','x;y'],['-m','$(rm)'],['-m','a','-m','b'],['-k','a']])
def test_unsupported_marker_arguments_refuse(repo,arguments):
    configured(repo)
    with pytest.raises(ValueError):Profile.profile(Path(repo),arguments,lambda p:(repo/p).read_bytes())


def test_runner_deselection_requires_the_declared_marker_expression(repo):
    import al_pytest_runner as Runner
    class Item:
        def __init__(self,nodeid):self.nodeid=nodeid
    plain=Runner.Outcomes(Path(repo),[])
    plain.pytest_deselected([Item('tests/test_case.py::test_browser')])
    assert plain.errors==['pytest required selection was deselected']
    marked=Runner.Outcomes(Path(repo),[],'not browser')
    marked.pytest_deselected([Item('tests/test_case.py::test_browser')])
    assert not marked.errors
    assert marked.summary()['deselected']==['tests/test_case.py::test_browser'] and marked.summary()['marker_expression']=='not browser'
