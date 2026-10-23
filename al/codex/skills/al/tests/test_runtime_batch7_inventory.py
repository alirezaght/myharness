"""Admission/currency derive concrete sources beyond submitted manifest claims."""
import json
from test_service_coverage import unit
from test_repo_adapters import contract
from conftest import _git
import al_artifacts as A
import al_gate_currency as C
import al_store
from hook_regression_helpers import begin,invoke


def discover(cli,tmp_path,paths):
    file=tmp_path/'paths.json';file.write_text(json.dumps(paths))
    invoke(cli,'gates','discover','--paths-file',str(file))
    state=json.loads(cli('show')[1]);return state,A.read_json(state['gate_manifests'][-1])


def test_prior_anticipated_paths_and_rules_cannot_be_omitted(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo)
    (repo/'svc/AGENTS.md').write_text('Retain the existing service instruction.\n')
    state,m=discover(cli,tmp_path,['svc/future.py'])
    assert 'svc/future.py' in m['paths'] and 'svc/AGENTS.md' in {s['path'] for s in m['rules']}
    before=state_file().read_bytes();m.update(paths=[],units=[],rules=[])
    file=tmp_path/'erased.json';file.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(file),ok=False)
    assert state_file().read_bytes()==before


def test_new_independent_instruction_source_invalidates_currency(cli,repo,tmp_path):
    begin(cli,tmp_path,repo)
    state=json.loads(cli('show')[1]);assert C.check(state)['current']
    (repo/'AGENTS.md').write_text('A new independently discovered instruction.\n')
    currency=C.check(state)
    assert not currency['current'] and any('AGENTS.md' in reason for reason in currency['reasons'])


def test_concrete_contract_obligation_cannot_be_na(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo)
    (repo/'package.json').write_text(json.dumps({'scripts':{'generate:contracts':'sh contracts.sh'}}))
    state,m=discover(cli,tmp_path,['svc/app.py'])
    for gate in m['gates']:
        if gate['category']=='contracts':gate.update(applicability='NOT_APPLICABLE',reason='No contract obligation',required=False)
    file=tmp_path/'false-na.json';file.write_text(json.dumps(m));before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(file),ok=False);assert state_file().read_bytes()==before


def test_known_transitive_shared_consumer_retains_unknown_remainder(cli,repo,tmp_path):
    (repo/'pyproject.toml').unlink();(repo/'svc/app.py').unlink();(repo/'web/app.js').unlink()
    files={'packages/shared/src/common/context.py':'VALUE = 1\n',
           'packages/bridge/src/bridge/api.py':'from common.context import VALUE\n',
           'services/example-service/src/example/main.py':'from bridge.api import VALUE\n',
           }
    for path,content in files.items():
        target=repo/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
    for path in ('packages/shared','packages/bridge','services/example-service'):unit(repo,path,check=path!='services/example-service')
    _git(repo,'add','.');_git(repo,'commit','-qm','seed transitive consumers')
    invoke(cli,'start','--spec','Discover every known direct and transitive shared Python consumer')
    invoke(cli,'spec-gate','SPEC_READY')
    invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    _,m=discover(cli,tmp_path,['packages/shared/src/common/context.py'])
    gates={g['id']:g for g in m['gates']}
    assert m['service_coverage']['assessment']['known_affected']==['services/example-service']
    assert gates['imports-unresolved']['applicability']=='UNRESOLVED'
    assert 'packages/bridge/src/bridge/api.py' in gates['imports-unresolved']['scope']


def test_discovery_preserves_declared_environment_dependencies(cli,repo,tmp_path):
    begin(cli,tmp_path,repo)
    state,m=discover(cli,tmp_path,['svc/app.py'])
    next(g for g in m['gates'] if g['id']=='loc')['env_names']=['AL_CHECK_VARIANT']
    file=tmp_path/'declared.json';file.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(file))
    _,again=discover(cli,tmp_path,['svc/app.py'])
    assert next(g for g in again['gates'] if g['id']=='loc')['env_names']==['AL_CHECK_VARIANT']


def test_supported_repository_selection_includes_configured_consumers_and_inputs(cli,repo,tmp_path):
    contract(repo)
    files={'services/quiet-service/pyproject.toml':'[tool.setuptools.packages.find]\nwhere=["src"]\n[tool.importlinter]\nroot_packages = ["quiet"]\n',
           'services/quiet-service/src/quiet/main.py':'VALUE = 1\n',
           'packages/shared/src/shared/context.py':'VALUE = 1\n',
           'packages/shared/pyproject.toml':'[tool.setuptools.packages.find]\nwhere=["src"]\n',
           }
    for path,content in files.items():
        target=repo/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
    _git(repo,'add','.');_git(repo,'commit','-qm','seed supported selection contract')
    invoke(cli,'start','--spec','Select supported configured consumers for a shared package edit')
    invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    _,m=discover(cli,tmp_path,['packages/shared/src/shared/context.py'])
    gates={g['id']:g for g in m['gates']}
    assert gates['imports-services-quiet-service']['applicability']=='APPLICABLE' and 'imports-unresolved' not in gates
    for dependency in ('.al/repository.json','ci/scripts/check-architecture.sh','services/quiet-service/pyproject.toml'):
        assert dependency in gates['imports-services-quiet-service']['scope']
