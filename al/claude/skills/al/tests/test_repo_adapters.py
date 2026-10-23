"""Independent physical enumeration, bounded scans and bundled adapters."""
import json
import pytest
from test_service_coverage import seed,start,discover,put,unit
from al_repository_facts import Snapshot,LIMITS
from launcher_context import admit_planning


@pytest.mark.parametrize('defect,code',[
    ('manifest','missing-manifest'),('source','missing-source-root'),('layout','undeclared-source-root'),('symlink','symlink-boundary'),('setup','executable-layout')])
def test_unsupported_boundary_cannot_be_omitted(cli,repo,tmp_path,defect,code):
    seed(repo)
    if defect=='manifest':(repo/'services/b/pyproject.toml').unlink()
    elif defect=='source':(repo/'services/b/src/app.py').unlink();(repo/'services/b/src').rmdir()
    elif defect=='layout':put(repo,'services/b/lib/hidden.py','VALUE=1\n')
    elif defect=='symlink':(repo/'services/b/link').symlink_to(repo/'packages/shared',target_is_directory=True)
    elif defect=='setup':put(repo,'services/b/setup.py','raise RuntimeError("never execute discovery")\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py']);assessment=m['service_coverage']['assessment']
    assert assessment['outcome']=='incomplete' and any(g['code']==code for g in assessment['gaps'])


def test_generic_works_without_contract_and_missing_new_unit_is_seen(cli,repo,tmp_path):
    seed(repo);start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['adapter']=={'id':'python','version':1}
    assert m['service_coverage']['assessment']['outcome']=='identified'
    put(repo,'services/new/src/main.py','VALUE=1\n');_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['path']=='services/new/pyproject.toml' for g in m['service_coverage']['assessment']['gaps'])


def test_torn_and_limited_scan_never_certify(cli,repo,tmp_path,monkeypatch):
    seed(repo);start(cli);original=Snapshot.stable
    monkeypatch.setattr(Snapshot,'stable',lambda self:False);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    monkeypatch.setattr(Snapshot,'stable',original);monkeypatch.setitem(LIMITS,'files',2)
    _,m=discover(cli,tmp_path,['services/a/src/app.py']);assert m['service_coverage']['assessment']['outcome']=='incomplete'


CONTRACT={
    'schema_version':1,'kind':'al-repository-contract-v1',
    'units':[{'path':'apps/pdf','role':'pdf'},{'path':'packages/pyshared','role':'shared_role'},
             {'path':'services/a-service','role':'a'},{'path':'services/b-service','role':'b'},
             {'path':'services/go-service','role':'go_role','language':'go'}],
    'containers':['packages/nested'],
    'architecture_check':{'argv':['make','lint-imports','SVC={unit_name}'],'unit_name':{'strip_suffix':'-service'},
                          'applies_to':'python-services','chain':['Makefile','ci/scripts/check-architecture.sh'],
                          'env_names':['PATH','UV_PROJECT_ENVIRONMENT','UV_PYTHON']},
    'selection':[{'changed':['packages/**'],'selects':'python-services'}],
    'independent_languages':['go']}


def contract(repo,data=None):
    """A synthetic repository that opts into the declared contract adapter."""
    (repo/'pyproject.toml').unlink(missing_ok=True)
    (repo/'svc/app.py').unlink(missing_ok=True)
    (repo/'web/app.js').unlink(missing_ok=True)
    unit(repo,'packages/pyshared')
    unit(repo,'services/a-service',extra='[tool.importlinter]\nroot_packages=["app"]\n')
    unit(repo,'services/b-service',extra='[tool.importlinter]\nroot_packages=["app"]\n')
    put(repo,'Makefile','lint-imports:\n\tbash ci/scripts/check-architecture.sh services/$(SVC)-service\n')
    put(repo,'ci/scripts/check-architecture.sh','#!/bin/bash\nset -euo pipefail\ncd "$1" && lint-imports\n')
    put(repo,'packages/tooling/package.json','{"name":"tooling"}')
    put(repo,'services/go-service/go.mod','module example.invalid/go\n')
    unit(repo,'apps/pdf');unit(repo,'packages/nested/inner')
    put(repo,'.al/repository.json',json.dumps(CONTRACT if data is None else data))
    from conftest import _git
    _git(repo,'add','.');_git(repo,'commit','-qm','declared repository contract')


def test_contract_nested_package_and_mixed_language_enumeration(cli,repo,tmp_path):
    contract(repo);start(cli);_,m=discover(cli,tmp_path,['packages/pyshared/src/app.py'])
    coverage=m['service_coverage'];units={u['id']:u for u in coverage['semantic_requirements']['units']}
    assert coverage['adapter']=={'id':'contract','version':1}
    assert coverage['assessment']['outcome']=='identified',coverage['assessment']['gaps']
    assert {'apps/pdf','packages/pyshared','packages/nested/inner','services/go-service'}<=set(units)
    assert 'packages/nested' not in units
    assert 'services/go-service' not in coverage['assessment']['potential_services']
    assert units['packages/pyshared']['roles']==['shared_role']


@pytest.mark.parametrize('mutation',['chain','field','json','placeholder','path','version','missing-unit'])
def test_contract_defects_cannot_fall_back(cli,repo,tmp_path,mutation):
    data=json.loads(json.dumps(CONTRACT))
    if mutation=='field':data['commands']=['true']
    elif mutation=='placeholder':data['architecture_check']['argv']=['make','{target}']
    elif mutation=='path':data['units'].append({'path':'../outside'})
    elif mutation=='version':data['schema_version']=2
    elif mutation=='missing-unit':data['units'].append({'path':'services/absent-service'})
    contract(repo,data)
    if mutation=='chain':(repo/'ci/scripts/check-architecture.sh').unlink()
    elif mutation=='json':put(repo,'.al/repository.json','{"schema_version": 1,')
    start(cli);_,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    assert m['service_coverage']['adapter']['id']=='contract'
    assert m['service_coverage']['assessment']['outcome']=='incomplete'


def test_contract_validation_is_closed():
    from al_repo_contract import validate
    assert validate(CONTRACT)==[]
    assert validate({**CONTRACT,'schema_version':True})
    assert validate({**CONTRACT,'units':[{'path':'/abs'}]})
    assert validate({**CONTRACT,'independent_languages':['python']})
    assert validate({**CONTRACT,'selection':[{'changed':['a/**'],'selects':'everything'}]})
    assert validate({**CONTRACT,'architecture_check':{**CONTRACT['architecture_check'],'env_names':['BAD-NAME']}})


def test_uv_workspace_container_and_unsupported_members(cli,repo,tmp_path):
    seed(repo);put(repo,'pyproject.toml','[tool.uv.workspace]\nmembers=["services/*","packages/*"]\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='identified'
    assert '.' not in m['service_coverage']['assessment']['potential_services']
    put(repo,'pyproject.toml','[tool.uv.workspace]\nmembers=["backend/**"]\n')
    _,m=discover(cli,tmp_path,['services/a/src/app.py']);assert m['service_coverage']['assessment']['outcome']=='incomplete'


def test_unmanifested_foreign_source_root_blocks_empty_universe(cli,repo,tmp_path):
    (repo/'pyproject.toml').unlink();(repo/'svc/app.py').unlink();(repo/'web/app.js').unlink()
    put(repo,'backend/src/mod.py','VALUE=1\n');start(cli)
    _,m=discover(cli,tmp_path,['backend/src/mod.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    from hook_regression_helpers import invoke
    admit_planning(cli)
    invoke(cli,'phase','IMPLEMENTATION',ok=False)


def test_source_free_legacy_manifest_can_prove_absence(cli,repo,tmp_path):
    (repo/'pyproject.toml').unlink();(repo/'svc/app.py').unlink();(repo/'web/app.js').unlink()
    from conftest import _git
    _git(repo,'add','.');_git(repo,'commit','-qm','source-free fixture')
    start(cli);_,m=discover(cli,tmp_path,['docs/readme.md'])
    assert m['service_coverage']['assessment']['outcome']=='identified'
    assert not m['service_coverage']['assessment']['potential_services']
    m.pop('service_coverage');p=tmp_path/'legacy.json';p.write_text(json.dumps(m))
    from hook_regression_helpers import invoke
    invoke(cli,'gates','plan','--file',str(p))


def test_contract_shared_nonpython_source_is_explicitly_unsupported(cli,repo,tmp_path):
    contract(repo);put(repo,'packages/js/package.json','{"name":"shared-js"}');put(repo,'packages/js/source.js','export const value=1;\n')
    start(cli);_,m=discover(cli,tmp_path,['packages/js/source.js'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['code']=='unsupported-shared-language' for g in m['service_coverage']['assessment']['gaps'])


def test_actual_mutation_during_read_is_a_torn_scan(cli,repo,tmp_path,monkeypatch,state_file):
    seed(repo);start(cli);original=Snapshot.text;changed=False
    def read(self,path,**kwargs):
        nonlocal changed
        result=original(self,path,**kwargs)
        if not changed:
            changed=True;(repo/'services/b/src/app.py').write_text('VALUE=2\n')
        return result
    monkeypatch.setattr(Snapshot,'text',read)
    from hook_regression_helpers import invoke
    p=tmp_path/'race-paths.json';p.write_text(json.dumps(['services/a/src/app.py']));before=state_file().read_bytes()
    result=invoke(cli,'gates','discover','--paths-file',str(p),ok=False)
    assert 'service coverage requirements changed' in str(result)
    assert state_file().read_bytes()==before
    _,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='identified'


def test_deleting_committed_contract_cannot_downgrade_to_generic(cli,repo,tmp_path):
    contract(repo);start(cli)
    (repo/'.al/repository.json').unlink()
    _,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    assert m['service_coverage']['adapter']=={'id':'contract','version':1}
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['path']=='.al/repository.json' for g in m['service_coverage']['assessment']['gaps'])


def test_retained_contract_cannot_downgrade_after_uncommitted_contract_removed(cli,repo,tmp_path):
    seed(repo);start(cli);put(repo,'.al/repository.json','{"kind": "unsupported"}')
    _,m=discover(cli,tmp_path,['services/a/src/app.py']);assert m['service_coverage']['adapter']['id']=='contract'
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    (repo/'.al/repository.json').unlink()
    _,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['adapter']['id']=='contract' and m['service_coverage']['assessment']['outcome']=='incomplete'


def test_persisted_amber_adapter_is_refused():
    from al_service_coverage import validate_declaration
    from al_repository_facts import validate_facts
    with pytest.raises(ValueError,match='unsupported coverage declaration'):
        validate_declaration({'schema_version':1,'policy_version':1,'adapter':{'id':'amber','version':1}},[])
    with pytest.raises(ValueError,match='unsupported repository adapter'):
        validate_facts({'schema_version':1,'policy_version':1,'adapter':{'id':'amber','version':1}})
