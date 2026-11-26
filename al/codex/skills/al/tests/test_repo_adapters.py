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
    if defect=='layout':
        # Python beside a service's declared package is its tooling: recorded, not a gap.
        exclusions=m['service_coverage']['semantic_requirements']['enumeration']['exclusions']
        assert any(e['path']=='services/b/lib' for e in exclusions)
        return
    if defect=='symlink':
        coverage=m['service_coverage']
        assert assessment['outcome']=='identified'
        retained=[r for r in coverage['projection']['records'] if r['record'].get('code')==code]
        assert retained and all(r['record_id'] in assessment['deferred_record_ids'] for r in retained)
        assert coverage['global_inventory']['status']=='incomplete'
        _,relevant=discover(cli,tmp_path,['services/b/link'])
        assert relevant['service_coverage']['assessment']['outcome']=='incomplete'
        assert any(g['code']==code for g in relevant['service_coverage']['assessment']['gaps'])
        return
    assert assessment['outcome']=='incomplete' and any(g['code']==code for g in assessment['gaps'])


def test_generic_works_without_contract_and_missing_new_unit_is_seen(cli,repo,tmp_path):
    seed(repo);start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['adapter']=={'id':'python','version':1}
    assert m['service_coverage']['assessment']['outcome']=='identified'
    put(repo,'services/new/src/main.py','VALUE=1\n');_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['path']=='services/new/pyproject.toml' for g in m['service_coverage']['assessment']['gaps'])


def test_torn_and_limited_scan_never_certify(cli,repo,tmp_path,monkeypatch,state_file):
    from al_repository_capture import CaptureDrift
    from hook_regression_helpers import invoke
    seed(repo);start(cli);original=Snapshot.revalidate;attempts=[]
    def torn(self,*args,**kwargs):
        attempts.append(1);raise CaptureDrift('source-content',['services/a/src/app.py'])
    paths=tmp_path/'paths.json';paths.write_text(json.dumps(['services/a/src/app.py']))
    monkeypatch.setattr(Snapshot,'revalidate',torn);before=state_file().read_bytes()
    result=invoke(cli,'gates','discover','--paths-file',str(paths),ok=False)
    assert 'capture drift' in ''.join(map(str,result))
    assert len(attempts)==2 and state_file().read_bytes()==before
    monkeypatch.setattr(Snapshot,'revalidate',original);monkeypatch.setitem(LIMITS,'files',2)
    before=state_file().read_bytes()
    result=invoke(cli,'gates','discover','--paths-file',str(paths),ok=False)
    assert 'limit' in ''.join(map(str,result))
    assert state_file().read_bytes()==before


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
    assert coverage['adapter']=={'id':'contract','version':2}
    assert coverage['assessment']['outcome']=='identified',coverage['assessment']['gaps']
    assert {'apps/pdf','packages/pyshared','packages/nested/inner','services/go-service'}<=set(units)
    assert 'packages/nested' not in units
    assert 'services/go-service' in coverage['assessment']['potential_services']
    assert 'services/go-service' not in coverage['assessment']['selected_services']
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
    # A member outside services/, packages/ or apps/ is tooling, recorded rather than a gap.
    _,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert any(e['path']=='backend/**' for e in m['service_coverage']['semantic_requirements']['enumeration']['exclusions'])


def test_unmanifested_foreign_source_root_blocks_admission(cli,repo,tmp_path,state_file):
    (repo/'pyproject.toml').unlink();(repo/'svc/app.py').unlink();(repo/'web/app.js').unlink()
    put(repo,'backend/src/mod.py','VALUE=1\n');start(cli)
    _,m=discover(cli,tmp_path,['backend/src/mod.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    from hook_regression_helpers import invoke
    from launcher_context import _reports
    packet=json.loads(invoke(cli,'planning','packet')[1]);report=tmp_path/'planning.json'
    report.write_text(json.dumps(_reports().report_for(packet)))
    materialized=json.loads(invoke(cli,'planning','report','--file',str(report))[1])
    # Incomplete coverage is recorded as verification debt instead of refusing admission.
    invoke(cli,'planning','admit','--file',materialized['materialized_obligations']['stored_path'])
    obligations=json.loads(cli('show')[1])['implementation_readiness']['obligations']
    assert any(o.startswith('coverage incomplete') for o in obligations)
    invoke(cli,'writer','start','--id','scoped-source','--role','al-engineer','--files','backend/src/mod.py')
    invoke(cli,'writer','finish','--id','scoped-source','--outcome','success')
    invoke(cli,'phase','VERIFICATION');before=state_file().read_bytes()
    invoke(cli,'review-start',ok=False)
    assert state_file().read_bytes()==before
    assert json.loads(cli('gates','status')[1])['coverage_blockers']


def test_source_free_manifest_still_requires_v2_declaration(cli,repo,tmp_path,state_file):
    (repo/'pyproject.toml').unlink();(repo/'svc/app.py').unlink();(repo/'web/app.js').unlink()
    from conftest import _git
    _git(repo,'add','.');_git(repo,'commit','-qm','source-free fixture')
    start(cli);_,m=discover(cli,tmp_path,['docs/readme.md'])
    assert m['service_coverage']['assessment']['outcome']=='identified'
    assert not m['service_coverage']['assessment']['potential_services']
    m.pop('service_coverage');p=tmp_path/'legacy.json';p.write_text(json.dumps(m))
    from hook_regression_helpers import invoke
    before=state_file().read_bytes()
    result=invoke(cli,'gates','plan','--file',str(p),ok=False)
    assert 'v2 declaration required' in ''.join(map(str,result))
    assert state_file().read_bytes()==before


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
    # A discovery decision whose working copy moved is evaluated again from a fresh
    # observation (#25 decision windows), so the manifest answers the moved tree, never a torn view.
    _,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert changed and m['service_coverage']['assessment']['outcome']=='identified'
    # The moved file is now a task change: the question was answered on the moved tree.
    assert 'services/b/src/app.py' in m['paths'] and 'services/b' in m['units']


def test_working_copy_that_keeps_moving_refuses_discovery(cli,repo,tmp_path,monkeypatch,state_file):
    seed(repo);start(cli);original=Snapshot.text;reads=[0]
    def read(self,path,**kwargs):
        result=original(self,path,**kwargs)
        reads[0]+=1;(repo/'services/b/src/app.py').write_text('VALUE=%d\n'%(reads[0]+1))
        return result
    monkeypatch.setattr(Snapshot,'text',read)
    from hook_regression_helpers import invoke
    p=tmp_path/'race-paths.json';p.write_text(json.dumps(['services/a/src/app.py']));before=state_file().read_bytes()
    result=invoke(cli,'gates','discover','--paths-file',str(p),ok=False)
    assert 'coverage capture drift: repeated-drift: services/b/src/app.py' in str(result[0])
    assert state_file().read_bytes()==before


def test_deleting_committed_contract_cannot_downgrade_to_generic(cli,repo,tmp_path):
    contract(repo);start(cli)
    (repo/'.al/repository.json').unlink()
    _,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    assert m['service_coverage']['adapter']=={'id':'contract','version':2}
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


def test_worktree_contract_edit_cannot_weaken_coverage(cli,repo,tmp_path):
    contract(repo);start(cli)
    weaker=json.loads(json.dumps(CONTRACT));weaker['containers'].append('services/b-service');weaker['architecture_check']['argv']=['true']
    put(repo,'.al/repository.json',json.dumps(weaker))
    _,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    assessment=m['service_coverage']['assessment']
    assert assessment['outcome']=='incomplete' and any(g['code']=='contract-changed' for g in assessment['gaps'])


def test_container_with_sources_and_option_like_unit_names_are_gaps(cli,repo,tmp_path):
    data=json.loads(json.dumps(CONTRACT));data['containers'].append('services/b-service')
    contract(repo,data);start(cli)
    _,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    assert any(g['code']=='contract-container-has-source' for g in m['service_coverage']['assessment']['gaps'])


def test_service_roots_scope_the_architecture_check():
    from al_repo_contract import validate
    assert validate({**CONTRACT,'service_roots':['apps']})==[]
    assert validate({**CONTRACT,'service_roots':['../x']}) and validate({**CONTRACT,'service_roots':[]})
    assert validate({**CONTRACT,'units':[{'path':'services/a','language':['go']}]})


def test_option_like_unit_name_is_never_bound_into_argv(cli,repo,tmp_path):
    contract(repo)
    unit(repo,'services/-x-service',extra='[tool.importlinter]\nroot_packages=["app"]\n')
    from conftest import _git
    _git(repo,'add','.');_git(repo,'commit','-qm','option-like unit')
    start(cli);_,m=discover(cli,tmp_path,['services/-x-service/src/app.py'])
    assert not any(g.get('argv') and any(a.startswith('SVC=-') for a in g['argv']) for g in m['gates'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'



def test_go_review_unsupported_consumer_in_both_snapshots_cannot_disappear(cli,repo,tmp_path,monkeypatch):
    from test_go_repository_coverage import go_repository,module,pinned,supported_go_environment
    supported_go_environment(monkeypatch);go_repository(repo);module(repo,'a','example.invalid/a');module(repo,'b','example.invalid/b')
    put(repo,'b/go.mod','module example.invalid/b\ngo 1.22\nrequire example.invalid/a v1.0.0\nreplace example.invalid/a => ../a\nretract v1.0.0\n')
    put(repo,'.gitlab-ci.yml','test-a:\n  script: cd a && go test ./...\n');pinned(repo);start(cli)
    _,docs=discover(cli,tmp_path,['docs/readme.md'])
    assert docs['service_coverage']['assessment']['outcome']=='identified'
    assert docs['service_coverage']['global_inventory']['status']=='incomplete'
    _,changed=discover(cli,tmp_path,['a/main.go']);c=changed['service_coverage']
    assert c['assessment']['outcome']=='incomplete'
    assert 'b' in c['assessment']['selected_services']
    assert c['assessment']['dependency_uncertainties']


@pytest.mark.parametrize('manifest',[
    'module example.invalid/x\nreplace example.invalid/a => .\n',
    'module example.invalid/x\nreplace example.invalid/a => ..\n',
    'module example.invalid/x\nexclude (\nexample.invalid/a v1.0.0\nexample.invalid/a v1.1.0\n)\n',
])
def test_go_review_literal_relative_replacements_and_excluded_versions(manifest):
    from al_repo_go import parse_mod
    assert parse_mod(manifest)['module']=='example.invalid/x'


def nested_python_workspace(repo):
    """Only a declared, manifested child can account for the parent's Python files."""
    from conftest import _git

    for path in ('pyproject.toml', 'svc/app.py', 'web/app.js'):
        (repo / path).unlink(missing_ok=True)
    unit(repo, 'services/render/contracts')
    put(repo, 'services/render/contracts/src/contract_schema.py', 'VALUE=1\n')
    unit(repo, 'services/consumer', 'from contract_schema import VALUE\n',
         extra='[tool.uv.sources]\ncontracts={path="../render/contracts",editable=true}\n')
    consumer_config = repo / 'services/consumer/pyproject.toml'
    consumer_config.write_text(consumer_config.read_text().replace(
        'dependencies=[]', 'dependencies=["contracts"]'))
    put(repo, 'package.json', json.dumps({'private': True,
        'workspaces': ['services/render/contracts']}))
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'declared child source ownership')


def test_manifested_workspace_child_owns_python_without_phantom_parent(cli, repo, tmp_path):
    nested_python_workspace(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ['services/render/contracts/src/contract_schema.py'])
    coverage = manifest['service_coverage']
    facts = coverage['semantic_requirements']
    units = {u['id']: u for u in facts['units']}
    assert 'services/render/contracts' in units
    assert units['services/render/contracts']['source_roots'] == ['services/render/contracts/src']
    assert units['services/consumer']['module_contexts'] == [{
        'source_root': 'services/consumer/src',
        'local_dependencies': ['services/render/contracts'],
    }]
    assert 'services/render' not in units
    assert any(e['path'] == 'services/render' and 'container' in e['reason']
               for e in facts['enumeration']['exclusions'])
    assert coverage['assessment']['outcome'] == 'identified', coverage['assessment']['gaps']
    assert 'services/consumer' in coverage['assessment']['selected_services']
    assert not any(g['code'] == 'missing-manifest' and g['path'] == 'services/render/pyproject.toml'
                   for g in facts['enumeration']['gaps'])


@pytest.mark.parametrize('defect', ['residual', 'residual-js', 'invalid', 'deleted', 'symlink'])
def test_unproven_child_ownership_cannot_certify_parent_container(cli, repo, tmp_path, defect):
    nested_python_workspace(repo)
    config = repo / 'services/render/contracts/pyproject.toml'
    changed = 'services/render/contracts/src/contract_schema.py'
    if defect == 'residual':
        changed = 'services/render/bootstrap.py'
        put(repo, changed, 'VALUE=2\n')
    elif defect == 'residual-js':
        changed = 'services/render/bootstrap.js'
        put(repo, changed, 'const value = 2;\n')
    elif defect == 'invalid':
        config.write_text('[project\n')
    elif defect == 'deleted':
        config.unlink()
    else:
        config.unlink()
        outside = tmp_path / 'untrusted-manifest.toml'
        outside.write_text('[project]\nname="not-owned"\n')
        config.symlink_to(outside)
    start(cli)
    _, manifest = discover(cli, tmp_path, [changed])
    coverage = manifest['service_coverage']
    facts = coverage['semantic_requirements']
    assert coverage['assessment']['outcome'] == 'incomplete'
    assert not any(e['path'] == 'services/render' and 'container' in e['reason']
                   for e in facts['enumeration']['exclusions'])
    expected = {'residual': ('missing-manifest', 'services/render/pyproject.toml'),
                'residual-js': ('unclassified-unit', 'services/render'),
                'invalid': ('invalid-manifest', 'services/render/contracts/pyproject.toml'),
                'deleted': ('missing-manifest', 'services/render/contracts/pyproject.toml'),
                'symlink': ('symlink-boundary', 'services/render/contracts/pyproject.toml')}[defect]
    assert any((g['code'], g['path']) == expected for g in facts['enumeration']['gaps'])



def test_explicit_parent_project_is_not_erased_by_child_ownership(cli, repo, tmp_path):
    nested_python_workspace(repo)
    put(repo, 'services/render/pyproject.toml',
        '[project]\nname="parent"\n[tool.setuptools.packages.find]\nwhere=["own-src"]\n')
    start(cli)
    _, manifest = discover(cli, tmp_path, ['services/render/pyproject.toml'])
    facts = manifest['service_coverage']['semantic_requirements']
    parent = next(u for u in facts['units'] if u['id'] == 'services/render')
    assert 'python' in parent['languages']
    assert parent['source_roots'] == ['services/render/own-src']
    assert any(g['code'] == 'missing-source-root' and g['path'] == 'services/render/own-src'
               for g in facts['enumeration']['gaps'])
    assert not any(e['path'] == 'services/render' and 'container' in e['reason']
                   for e in facts['enumeration']['exclusions'])
