"""Complete sourced conventional checks through public discovery and admission."""
import copy
import json
from pathlib import Path
import pytest
import al_artifacts as A
import al_ci_inventory as CI
import al_ci_verification as Verification
from conftest import _git
from hook_regression_helpers import invoke, manifest_for
from test_service_coverage import put, start, discover
from launcher_context import admit_planning

WORKFLOW='''name: Quality
on: [push, pull_request]
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - run: uv python install
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest
      - run: uv build
'''


def project(repo):
    (repo/'svc/app.py').unlink(missing_ok=True);(repo/'web/app.js').unlink(missing_ok=True)
    put(repo,'pyproject.toml','[project]\nname="ordinary-project"\nversion="0.1.0"\nrequires-python=">=3.13"\n[build-system]\nrequires=["uv_build"]\nbuild-backend="uv_build"\n[tool.uv.build-backend]\nmodule-root=""\n')
    put(repo,'ordinary_project/__init__.py','VALUE = 1\n')
    put(repo,'uv.lock','version = 1\n');put(repo,'.python-version','3.13\n')
    put(repo,'.github/workflows/quality.yml',WORKFLOW)
    _git(repo,'add','.');_git(repo,'commit','-qm','conventional project with sourced quality chain')


def test_complete_conventional_ci_is_independently_bound(cli,repo,tmp_path):
    project(repo);start(cli)
    st,m=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assessment=m['service_coverage']['assessment']
    assert assessment['outcome']=='identified' and assessment['check_discovery']['status']=='complete'
    binding=next(b for b in assessment['required_bindings'] if b['category']=='ci')
    assert 'verification-root' in binding['obligation_ids']
    assert binding['argv'][:2]==['sh','-eu']
    assert binding['argv'][2]=='-c' and 'al_pytest_runner.py' in binding['argv'][3]
    assert all(line in binding['argv'][3].splitlines() for line in ('uv python install','uv sync --locked','uv run ruff check .','uv run ruff format --check .','uv build'))
    chain=binding['verification_chain']
    assert [r['purpose'] for r in chain['requirements']]==['lint','format-check','test','build']
    assert [s['kind'] for s in chain['setup']]==['checkout','uv-tool-setup','python-install','locked-sync']
    assert __import__('al_repository_facts').all_within(binding['required_scope'],['pyproject.toml','uv.lock','.python-version','.github/workflows/quality.yml'])
    assert {'PATH','UV_PYTHON','UV_CONFIG_FILE'}<=set(binding['env_names'])
    planned=manifest_for(st,repo,['ordinary_project/__init__.py'])
    file=tmp_path/'planned.json';file.write_text(json.dumps(planned));invoke(cli,'gates','plan','--file',str(file))
    stored=A.read_json(json.loads(cli('show')[1])['gate_manifests'][-1])
    assert stored['service_coverage']==planned['service_coverage']
    # Even a self-consistent declaration cannot change independently sourced execution.
    forged=copy.deepcopy(stored)
    gate=next(g for g in forged['gates'] if g.get('obligation_ids'))
    gate['argv']=['sh','-eu','-c','uv run pytest']
    file.write_text(json.dumps(forged));before=json.loads(cli('show')[1])
    invoke(cli,'gates','plan','--file',str(file),ok=False)
    assert json.loads(cli('show')[1])==before


@pytest.mark.parametrize('change',[
    lambda s:s.replace('astral-sh/setup-uv@v6','unknown/setup@v1'),
    lambda s:s.replace('actions/checkout@v4','actions/checkout@v7'),
    lambda s:s.replace('runs-on: ubuntu-latest','runs-on: ${{ matrix.os }}'),
    lambda s:s.replace('uv sync --locked','uv sync'),
    lambda s:s.replace('uv run pytest','uv run pytest --collect-only'),
    lambda s:s.replace('ruff format --check .','ruff format .'),
    lambda s:s.replace('uv build','uv build || true'),
    lambda s:s.replace('- run: uv build','- if: success()\n        run: uv build'),
    lambda s:s.replace('on: [push, pull_request]','on: {pull_request: {paths: [src/**]}}'),
])
def test_unsupported_chain_stays_incomplete_and_is_reported_unverified(cli,repo,tmp_path,change):
    project(repo);put(repo,'.github/workflows/quality.yml',change(WORKFLOW));start(cli)
    st,m=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    a=m['service_coverage']['assessment']
    assert a['outcome']=='incomplete'
    assert any(g['code']=='missing-verification-check' for g in a['gaps'])
    file=tmp_path/'incomplete.json';file.write_text(json.dumps(manifest_for(st,repo,['ordinary_project/__init__.py'])))
    invoke(cli,'gates','plan','--file',str(file))
    # Incomplete coverage is an unverified check reported to the user, not a blocker.
    unverified=json.loads(cli('gates','status')[1])['unverified']
    assert any(u['category']=='lint-imports' for u in unverified)
    admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')


@pytest.mark.parametrize('line',['uv run ruff check .','uv run ruff format --check .','uv run pytest','uv build'])
def test_omitted_requirement_from_binding_refuses(cli,repo,tmp_path,line):
    project(repo);start(cli);st,_=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    m=manifest_for(st,repo,['ordinary_project/__init__.py'])
    gate=next(g for g in m['gates'] if g.get('obligation_ids'))
    gate['argv'][-1]='\n'.join(v for v in gate['argv'][-1].splitlines() if ('al_pytest_runner.py' not in v if line=='uv run pytest' else v!=line))
    file=tmp_path/'omitted.json';file.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(file),ok=False)


def test_all_jobs_and_workflow_membership_are_required(cli,repo,tmp_path):
    project(repo);start(cli);_,before=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    put(repo,'.github/workflows/second.yml',WORKFLOW.replace('uv run pytest','unmodeled-check .'))
    _,after=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert before['service_coverage']['assessment']['outcome']=='identified'
    assert after['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['code']=='unresolved-ci-check' for g in after['service_coverage']['assessment']['gaps'])


def test_declared_architecture_does_not_use_ci_as_a_replacement(cli,repo,tmp_path):
    project(repo);p=repo/'pyproject.toml';p.write_text(p.read_text()+'[tool.al.architecture]\nforbidden=[]\n');start(cli)
    _,m=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert any(g['code']=='missing-architecture-check' for g in m['service_coverage']['assessment']['gaps'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'


def test_release_only_workflow_is_excluded_only_with_quality_context(repo):
    project(repo);put(repo,'.github/workflows/release.yml','on:\n  push:\n    tags: ["v*"]\n  workflow_dispatch:\npermissions:\n  contents: read\njobs:\n  release:\n    uses: unsupported/remote.yml@v1\n')
    known=CI.inventory(Path(repo),['ordinary_project/__init__.py'],'merge_request_quality')
    assert len(known['obligations'])==1 and known['obligations'][0]['applicability']=='APPLICABLE'
    unknown=CI.inventory(Path(repo),['ordinary_project/__init__.py'])
    assert any(o['applicability']=='UNRESOLVED' for o in unknown['obligations'])


def test_check_recognition_preserves_nonmasking_semantics():
    assert Verification.observed(['uv','build'])
    assert Verification.observed(['uv','run','ruff','format','--check','.'])
    for args in (['uv','build','--help'],['ruff','format','.'],['ruff','check','--exit-zero','.']):
        assert Verification.observed(args)==[]


def test_conventional_ci_must_cover_every_source_root(cli,repo,tmp_path):
    project(repo)
    put(repo,'one/a.py','VALUE=1\n');put(repo,'two/b.py','VALUE=2\n')
    (repo/'ordinary_project/__init__.py').unlink()
    p=repo/'pyproject.toml'
    p.write_text('[project]\nname="multi-root-project"\nrequires-python=">=3.13"\n[tool.setuptools.packages.find]\nwhere=["one","two"]\n')
    put(repo,'.gitlab-ci.yml','quality:\n  script: python -m compileall -q one\n')
    (repo/'.github/workflows/quality.yml').unlink();start(cli)
    _,missing=discover(cli,tmp_path,['one/a.py'])
    assert missing['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['code']=='missing-verification-check' for g in missing['service_coverage']['assessment']['gaps'])
    put(repo,'.gitlab-ci.yml','quality:\n  script: python -m compileall -q one two\n')
    _,complete=discover(cli,tmp_path,['one/a.py'])
    assert complete['service_coverage']['assessment']['outcome']=='identified'


def test_conventional_ci_does_not_erase_unchecked_language(cli,repo,tmp_path):
    project(repo);put(repo,'package.json','{"name":"mixed-project"}');put(repo,'web/app.js','const value=1;\n');start(cli)
    _,m=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['code']=='missing-verification-check' for g in m['service_coverage']['assessment']['gaps'])


@pytest.mark.parametrize('name,value',[('PYTEST_ADDOPTS','--collect-only'),('PYTEST_PLUGINS','foreign_plugin'),('PYTEST_DISABLE_PLUGIN_AUTOLOAD','unsupported')])
def test_pytest_ambient_execution_controls_cannot_be_unbound(cli,repo,tmp_path,monkeypatch,name,value):
    project(repo);start(cli)
    _,before=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    binding=next(b for b in before['service_coverage']['assessment']['required_bindings'] if b['category']=='ci')
    # Every supported influence must at least be an evidence dependency; an
    # unsupported masking/extension profile must instead remain incomplete.
    assert name in binding['env_names']
    monkeypatch.setenv(name,value)
    _,after=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert after['service_coverage']['assessment']['outcome']=='incomplete'
    m=manifest_for(json.loads(cli('show')[1]),repo,['ordinary_project/__init__.py'])
    file=tmp_path/'ambient-control.json';file.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(file))
    # The masked check is never claimed as verification: it is reported as unverified.
    unverified=json.loads(cli('gates','status')[1])['unverified']
    assert any(u['category']=='lint-imports' for u in unverified)


def test_collection_only_pytest_configuration_is_not_verification(cli,repo,tmp_path):
    project(repo);p=repo/'pyproject.toml'
    p.write_text(p.read_text()+'[tool.pytest.ini_options]\naddopts="--collect-only"\n')
    start(cli);_,m=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
