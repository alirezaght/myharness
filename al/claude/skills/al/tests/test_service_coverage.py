"""Public discovery exercises certainty independently of coverage completeness."""
import json
from pathlib import Path
import pytest
import al_artifacts as A
from conftest import _git
from hook_regression_helpers import invoke,manifest_for



def reports_uncertain_dependents(assessment):
    """Unresolved imports are reported: either every potential service is already selected, or the
    remaining ones are an unverified `uncertain-dependents` gap (AL no longer selects them all)."""
    if assessment['outcome']=='exhaustive':return True
    return assessment['outcome']=='incomplete' and any(g['code']=='uncertain-dependents' for g in assessment['gaps'])

def put(repo,path,text):
    file=repo/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)


def unit(repo,path,source='VALUE=1\n',check=True,extra=''):
    put(repo,path+'/pyproject.toml','[project]\nname="'+path.split('/')[-1]+'"\ndependencies=[]\n[tool.setuptools.packages.find]\nwhere=["src"]\n'+('[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n' if check else '')+extra)
    put(repo,path+'/src/app.py',source)


def seed(repo):
    (repo/'pyproject.toml').unlink(missing_ok=True)
    (repo/'svc/app.py').unlink(missing_ok=True)
    (repo/'web/app.js').unlink(missing_ok=True)
    unit(repo,'packages/shared')
    unit(repo,'services/a','import common\n')
    unit(repo,'services/b')
    put(repo,'packages/shared/src/common.py','VALUE=1\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','coverage fixture')


def start(cli):
    invoke(cli,'start','--spec','Prove complete service coverage before implementation admission.')
    invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    return json.loads(cli('show')[1])


def discover(cli,tmp,paths):
    p=tmp/'paths.json';p.write_text(json.dumps(paths));invoke(cli,'gates','discover','--paths-file',str(p))
    st=json.loads(cli('show')[1]);return st,A.read_json(st['gate_manifests'][-1])


def plan(cli,repo,tmp,paths):
    st=json.loads(cli('show')[1]);m=manifest_for(st,repo,paths);p=tmp/'plan.json';p.write_text(json.dumps(m));invoke(cli,'gates','plan','--file',str(p));return m


def test_identified_transitive_consumers_and_base_removed_source(cli,repo,tmp_path):
    seed(repo);unit(repo,'packages/bridge','from common import VALUE\n');put(repo,'packages/bridge/src/bridge.py','from common import VALUE\n');put(repo,'services/a/src/app.py','from bridge import VALUE\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','transitive graph');start(cli)
    _,m=discover(cli,tmp_path,['packages/shared/src/common.py'])
    coverage=m['service_coverage']['assessment'];assert coverage['outcome']=='identified'
    assert coverage['selected_services']==['services/a']
    (repo/'packages/shared/src/common.py').unlink()
    _,removed=discover(cli,tmp_path,['packages/shared/src/common.py'])
    assert removed['service_coverage']['assessment']['selected_services']==['services/a']


def test_unknown_dynamic_import_broadens_with_complete_checks(cli,repo,tmp_path):
    seed(repo);put(repo,'packages/shared/src/common.py','def load(name):\n    return __import__(name)\n');start(cli)
    _,m=discover(cli,tmp_path,['packages/shared/src/common.py'])
    coverage=m['service_coverage']['assessment'];assert reports_uncertain_dependents(coverage)
    assert set(coverage['selected_services'])<=set(coverage['potential_services'])
    # Checks of selected services run; the possible further dependents form one unverified gate.
    assert all(g['applicability']=='APPLICABLE' for g in m['gates'] if g['category']=='lint-imports' and g['id']!='imports-unresolved')


@pytest.mark.parametrize('source,uncertain',[
    ('from importlib import import_module as load\nload("common")\n',False),
    ('import importlib as lib\nlib.import_module("json")\n',False),
    ('import importlib\nimportlib.import_module("elasticsearch")\n',False),
    ('from importlib import import_module as load\nload=lambda x: x\nload("common")\n',True),
    ('from importlib import import_module as load\ndef f(load):\n    return load("common")\n',True),
    ('def f(name):\n    return __import__(name)\n',True),
    ('other.import_module("common")\n',True),
    ('import importlib\nf=getattr(importlib,"import_module")\nf(target)\n',True),
    ('from importlib import import_module\nloaders=[import_module]\nloaders[0](target)\n',True),
    ('a=other.import_module\na("json")\n',True),
    ('__import__=other\na=__import__\na("json")\n',True),
    ('import importlib as lib\nlib=other\ndef f():\n    return lib.import_module("json")\n',True),
    ('__import__=other\ndef f():\n    return __import__("json")\n',True),
    ('a=__import__\ndef f(name):\n    return a(name)\n',True),
    ('__import__("json",level=1)\n',True),
    ('import sys\nsys.path=["unknown"]\nimport importlib\nimportlib.import_module("json")\n',True),
    ('import importlib\nif conditional:\n    from importlib import import_module as load\nload("json")\n',True),
])
def test_dynamic_bindings_are_contextual(cli,repo,tmp_path,source,uncertain):
    seed(repo);put(repo,'services/a/src/app.py',source)
    config=repo/'services/a/pyproject.toml';config.write_text(config.read_text().replace('dependencies=[]','dependencies=["elasticsearch>=8"]'))
    put(repo,'services/a/uv.lock','[[package]]\nname="elasticsearch"\nversion="8.0.0"\nsource={registry="https://pypi.org/simple"}\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assessment=m['service_coverage']['assessment'];assert bool(assessment['dependency_uncertainties'])==uncertain
    assert reports_uncertain_dependents(assessment) if uncertain else assessment['outcome']=='identified'


def test_duplicate_modules_and_external_shadow_do_not_overwrite_owners(cli,repo,tmp_path):
    seed(repo);put(repo,'services/a/src/domain.py','VALUE=1\n');put(repo,'services/b/src/domain.py','VALUE=2\n');put(repo,'services/a/src/app.py','import domain\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','isolated module contexts')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/domain.py'])
    assert m['service_coverage']['assessment']['selected_services']==['services/a']
    put(repo,'packages/shared/src/elasticsearch.py','VALUE=1\n');put(repo,'services/a/src/app.py','import importlib\nimportlib.import_module("elasticsearch")\n')
    _,m=discover(cli,tmp_path,['services/a/src/app.py']);assert m['service_coverage']['assessment']['outcome']=='identified' # local literal has a known owner


def test_relative_package_initializer_imports(cli,repo,tmp_path):
    seed(repo);put(repo,'packages/shared/src/pkg/__init__.py','from .leaf import VALUE\n');put(repo,'packages/shared/src/pkg/leaf.py','VALUE=1\n');put(repo,'services/a/src/app.py','import pkg\n')
    start(cli);_,m=discover(cli,tmp_path,['packages/shared/src/pkg/leaf.py']);assert m['service_coverage']['assessment']['selected_services']==['services/a']


def test_incompatible_external_lock_and_target_stdlib_are_uncertainty(cli,repo,tmp_path):
    seed(repo);put(repo,'services/a/src/app.py','from importlib import import_module\nimport_module("elasticsearch")\n')
    p=repo/'services/a/pyproject.toml';p.write_text(p.read_text().replace('dependencies=[]','dependencies=["elasticsearch>=8"]'))
    put(repo,'services/a/uv.lock','[[package]]\nname="elasticsearch"\nversion="1.0.0"\nsource={registry="https://pypi.org/simple"}\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py']);assert reports_uncertain_dependents(m['service_coverage']['assessment'])
    put(repo,'services/a/src/app.py','__import__("json")\n');p.write_text(p.read_text().replace('dependencies=["elasticsearch>=8"]','dependencies=[]\nrequires-python="==2.7.*"'))
    _,m=discover(cli,tmp_path,['services/a/src/app.py']);assert reports_uncertain_dependents(m['service_coverage']['assessment'])


def test_changed_mixed_language_shared_package_broadens_or_blocks_missing_checks(cli,repo,tmp_path):
    seed(repo);put(repo,'packages/js/package.json','{"name":"js-shared"}')
    put(repo,'packages/js/src/shared.js','export const value=1;\n')
    start(cli);_,m=discover(cli,tmp_path,['packages/js/src/shared.js'])
    assessment=m['service_coverage']['assessment']
    assert reports_uncertain_dependents(assessment) and set(assessment['selected_services'])<=set(assessment['potential_services'])
    assert any(u['code']=='uncertain-language-impact' for u in assessment['dependency_uncertainties'])
    unit(repo,'services/b',check=False);_,m=discover(cli,tmp_path,['packages/js/src/shared.js'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'


def test_current_local_override_is_not_external_identity_despite_registry_lock(cli,repo,tmp_path):
    seed(repo);unit(repo,'packages/local_elastic')
    put(repo,'services/a/src/app.py','from importlib import import_module\nimport_module("elasticsearch")\n')
    p=repo/'services/a/pyproject.toml';p.write_text(p.read_text().replace('dependencies=[]','dependencies=["elasticsearch>=8"]')+'[tool.uv.sources]\nelasticsearch={path="../../packages/local_elastic",editable=true}\n')
    put(repo,'services/a/uv.lock','[[package]]\nname="elasticsearch"\nversion="8.0.0"\nsource={registry="https://pypi.org/simple"}\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    a=m['service_coverage']['assessment'];assert reports_uncertain_dependents(a)
    assert any(u['target']=='elasticsearch' for u in a['dependency_uncertainties'])
