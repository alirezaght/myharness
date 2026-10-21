"""Independent physical enumeration, bounded scans and bundled adapters."""
import json
import pytest
from test_service_coverage import seed,start,discover,put,unit
from al_repository_facts import Snapshot,LIMITS


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


def test_generic_works_without_amber_markers_and_missing_new_unit_is_seen(cli,repo,tmp_path):
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


def amber(repo):
    (repo/'pyproject.toml').unlink(missing_ok=True)
    (repo/'svc/app.py').unlink(missing_ok=True)
    (repo/'web/app.js').unlink(missing_ok=True)
    from al_repo_amber import HELPER
    unit(repo,'packages/pyshared')
    unit(repo,'services/a-service',extra='[tool.importlinter]\nroot_packages=["app"]\n')
    unit(repo,'services/b-service',extra='[tool.importlinter]\nroot_packages=["app"]\n')
    put(repo,'Makefile','lint-imports:\n\t@test -n "$(SVC)" || { echo "SVC is required, e.g. make $@ SVC=shortlist"; exit 1; }\n\tpnpm -w lint:imports services/$(SVC)-service\n')
    put(repo,'package.json',json.dumps({'scripts':{'lint:imports':'bash ci/scripts/fast-check-lint-imports.sh'}}))
    put(repo,'ci/scripts/fast-check-lint-imports.sh','#!/bin/bash\n'+HELPER+'\n')
    put(repo,'packages/tooling/ruff/architecture.toml','[lint]\nselect=["TID251"]\n')
    put(repo,'packages/tooling/package.json','{"name":"tooling"}')
    put(repo,'ci/manifest.yml','services:\n  a:\n    slug: a\n  b:\n    slug: b\n  pdf:\n    package_dir: apps/pdf\n    slug: pdf\n  shared_role:\n    package_dir: packages/pyshared\n    slug: shared_role\n  go_role:\n    package_dir: services/go-service\n    language: go\n')
    put(repo,'services/go-service/go.mod','module example.invalid/go\n')
    unit(repo,'apps/pdf');put(repo,'pnpm-workspace.yaml','packages:\n  - "services/*"\n  - "apps/*"\n  - "packages/**"\n')
    unit(repo,'packages/nested/inner')
    from conftest import _git
    _git(repo,'add','.');_git(repo,'commit','-qm','Amber supported layout')


def test_amber_nested_package_dir_and_mixed_language_enumeration(cli,repo,tmp_path):
    amber(repo);start(cli);_,m=discover(cli,tmp_path,['packages/pyshared/src/app.py'])
    coverage=m['service_coverage'];units={u['id']:u for u in coverage['semantic_requirements']['units']}
    assert coverage['adapter']['id']=='amber'
    assert coverage['assessment']['outcome']=='identified',coverage['assessment']['gaps']
    assert {'apps/pdf','packages/pyshared','packages/nested/inner','services/go-service'}<=set(units)
    assert 'services/go-service' not in coverage['assessment']['potential_services']
    assert units['packages/pyshared']['roles']==['shared_role']


@pytest.mark.parametrize('mutation',['helper','script','workspace','manifest'])
def test_amber_malformed_chain_cannot_fall_back(cli,repo,tmp_path,mutation):
    amber(repo)
    if mutation=='helper':put(repo,'ci/scripts/fast-check-lint-imports.sh','true\n')
    elif mutation=='script':put(repo,'package.json','{"scripts":{"lint:imports":"true"}}')
    elif mutation=='workspace':put(repo,'pnpm-workspace.yaml','packages:\n  - "outside/**"\n')
    else:put(repo,'ci/manifest.yml','services: !unsupported data\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    assert m['service_coverage']['adapter']['id']=='amber'
    assert m['service_coverage']['assessment']['outcome']=='incomplete'


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


def test_amber_shared_nonpython_source_is_explicitly_unsupported(cli,repo,tmp_path):
    amber(repo);put(repo,'packages/js/package.json','{"name":"shared-js"}');put(repo,'packages/js/source.js','export const value=1;\n')
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


def test_deleting_all_amber_markers_cannot_downgrade_pinned_profile(cli,repo,tmp_path):
    amber(repo);start(cli)
    (repo/'ci/manifest.yml').unlink();(repo/'ci/scripts/import_review/checker.py').unlink(missing_ok=True)
    _,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    assert m['service_coverage']['adapter']=={'id':'amber','version':1}
    assert m['service_coverage']['assessment']['outcome']=='incomplete'
    assert any(g['path']=='ci/manifest.yml' for g in m['service_coverage']['assessment']['gaps'])


def test_retained_amber_profile_cannot_downgrade_after_uncommitted_markers_removed(cli,repo,tmp_path):
    seed(repo);start(cli);put(repo,'ci/manifest.yml','services: !unsupported data\n')
    _,m=discover(cli,tmp_path,['services/a/src/app.py']);assert m['service_coverage']['adapter']['id']=='amber'
    (repo/'ci/manifest.yml').unlink()
    _,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['adapter']['id']=='amber' and m['service_coverage']['assessment']['outcome']=='incomplete'
