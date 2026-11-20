"""Changed artifacts route to their actual consumers without unrelated import blockers."""
from conftest import _git
from test_service_coverage import seed, start, discover, put, unit



def reports_uncertain_dependents(assessment):
    """Unresolved imports are reported: either every potential service is already selected, or the
    remaining ones are an unverified `uncertain-dependents` gap (AL no longer selects them all)."""
    if assessment['outcome']=='exhaustive':return True
    return assessment['outcome']=='incomplete' and any(g['code']=='uncertain-dependents' for g in assessment['gaps'])

def unrelated_dynamic_import(repo):
    # services/b dynamically imports a literal module no affected module can be; it stays deferred.
    put(repo, 'services/b/src/loader.py', 'import importlib\nimportlib.import_module("vendor_plugin")\n')


def unknown_dynamic_import(repo):
    put(repo, 'services/b/src/loader.py', 'def load(name):\n    return __import__(name)\n')


def commit(repo, message):
    _git(repo, 'add', '.');_git(repo, 'commit', '-qm', message)


def test_shared_fixture_selects_its_literal_consumers_and_defers_unrelated_uncertainty(cli, repo, tmp_path):
    seed(repo);unrelated_dynamic_import(repo)
    put(repo, 'packages/shared/fixtures/orders.json', '{"orders": []}\n')
    put(repo, 'services/a/src/orders.py', 'FIXTURE = "orders.json"\n')
    commit(repo, 'fixture consumer');start(cli)
    put(repo, 'packages/shared/fixtures/orders.json', '{"orders": [1]}\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/orders.json'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'identified', assessment
    assert assessment['selected_services'] == ['services/a']
    deps = m['service_coverage']['semantic_requirements']['dependencies']
    assert any(u['path'] == 'services/b/src/loader.py' for u in deps['deferred_uncertainties'])
    row = next(r for r in deps['artifact_consumers'] if r['path'] == 'packages/shared/fixtures/orders.json')
    assert row['kind'] == 'data-fixture' and row['consumers'] == ['services/a/src/orders.py']


def test_root_readme_does_not_broaden_unrelated_dynamic_imports(cli, repo, tmp_path):
    seed(repo);unknown_dynamic_import(repo)
    put(repo, 'pyproject.toml', '[project]\nname="root"\ndependencies=[]\n[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n')
    put(repo, 'README.md', '# Project\n')
    commit(repo, 'root package and docs');start(cli)
    put(repo, 'README.md', '# Project details\n')
    _, m = discover(cli, tmp_path, ['README.md'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'identified', assessment
    assert assessment['selected_services'] == ['.']
    dependencies = m['service_coverage']['semantic_requirements']['dependencies']
    assert any(u['path'] == 'services/b/src/loader.py' for u in dependencies['deferred_uncertainties'])
    assert any(b['units'] == ['.'] for b in assessment['required_bindings'])
    readme = next(r for r in dependencies['artifact_consumers'] if r['path'] == 'README.md')
    assert readme == {'path': 'README.md', 'kind': 'documentation', 'owner': '.', 'producers': [], 'consumers': []}


def test_narrative_doc_under_docs_does_not_select_unrelated_services(cli, repo, tmp_path):
    seed(repo);unknown_dynamic_import(repo)
    untouched = {f'services/untouched-{index:02}' for index in range(25)}
    for service in untouched:unit(repo, service)
    put(repo, 'pyproject.toml', '[project]\nname="root"\ndependencies=[]\n[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n')
    put(repo, 'docs/ordinary-narrative.md', '# Narrative\n')
    commit(repo, 'root package and docs');start(cli)
    put(repo, 'docs/ordinary-narrative.md', '# Updated narrative\n')
    _, m = discover(cli, tmp_path, ['docs/ordinary-narrative.md'])
    assessment = m['service_coverage']['assessment']
    assert untouched <= set(assessment['potential_services'])
    assert untouched.isdisjoint(assessment['selected_services'])
    assert assessment['outcome'] == 'identified', assessment
    assert assessment['selected_services'] == ['.']
    dependencies = m['service_coverage']['semantic_requirements']['dependencies']
    assert any(u['path'] == 'services/b/src/loader.py' for u in dependencies['deferred_uncertainties'])
    assert any(b['units'] == ['.'] for b in assessment['required_bindings'])


def test_changed_root_python_manifest_keeps_its_quality_obligation(cli, repo, tmp_path):
    seed(repo)
    put(repo, 'pyproject.toml', '[project]\nname="root"\ndependencies=[]\n[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n')
    commit(repo, 'root package');start(cli)
    put(repo, 'pyproject.toml', '[project]\nname="root"\ndependencies=[]\n[tool.al.architecture]\nforbidden=["forbidden_dependency", "another_forbidden"]\n')
    _, m = discover(cli, tmp_path, ['pyproject.toml'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'identified', assessment
    assert assessment['selected_services'] == ['.']
    assert any(binding['units'] == ['.'] and any(source['path'] == 'pyproject.toml'
               for source in binding['source_chain']) for binding in assessment['required_bindings'])


def test_markdown_with_a_literal_runtime_consumer_selects_that_service(cli, repo, tmp_path):
    seed(repo);unrelated_dynamic_import(repo)
    put(repo, 'packages/shared/fixtures/guide.md', '# Guide\n')
    put(repo, 'services/a/src/guide_loader.py', 'GUIDE = "guide.md"\n')
    commit(repo, 'markdown resource consumer');start(cli)
    put(repo, 'packages/shared/fixtures/guide.md', '# Updated guide\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/guide.md'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'identified', assessment
    assert assessment['selected_services'] == ['services/a']
    row = next(r for r in m['service_coverage']['semantic_requirements']['dependencies']['artifact_consumers']
               if r['path'] == 'packages/shared/fixtures/guide.md')
    assert row == {'path': 'packages/shared/fixtures/guide.md', 'kind': 'documentation', 'owner': 'packages/shared',
                   'producers': [], 'consumers': ['services/a/src/guide_loader.py']}


def test_unreferenced_shared_fixture_is_an_explicit_exhaustive_requirement(cli, repo, tmp_path):
    seed(repo);put(repo, 'packages/shared/fixtures/lonely.json', '{}\n');commit(repo, 'lonely fixture');start(cli)
    put(repo, 'packages/shared/fixtures/lonely.json', '{"changed": true}\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/lonely.json'])
    assessment = m['service_coverage']['assessment']
    assert reports_uncertain_dependents(assessment)
    assert any(u['code'] == 'uncertain-artifact-consumer' for u in assessment['dependency_uncertainties'])
    assert set(assessment['selected_services']) <= set(assessment['potential_services'])


def test_unreferenced_shared_markdown_has_no_generic_v2_fallback(cli, repo, tmp_path):
    seed(repo);put(repo, 'packages/shared/fixtures/guide.md', '# Guide\n');commit(repo, 'shared markdown')
    start(cli);put(repo, 'packages/shared/fixtures/guide.md', '# Updated guide\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/guide.md'])
    assessment = m['service_coverage']['assessment']
    assert assessment['schema_version'] == 2
    assert assessment['selected_services'] == []
    assert not any(u['code'] == 'uncertain-artifact-consumer' and u['path'] == 'packages/shared/fixtures/guide.md'
                   for u in assessment['dependency_uncertainties'])


def test_changed_importable_module_keeps_exhaustive_dynamic_import_fallback(cli, repo, tmp_path):
    seed(repo);unknown_dynamic_import(repo);commit(repo, 'dynamic import');start(cli)
    put(repo, 'packages/shared/src/common.py', 'VALUE=2\n')
    _, m = discover(cli, tmp_path, ['packages/shared/src/common.py'])
    assessment = m['service_coverage']['assessment']
    assert reports_uncertain_dependents(assessment)
    assert any(u['path'] == 'services/b/src/loader.py' for u in assessment['dependency_uncertainties'])


def test_openapi_schema_reaches_generated_code_importers(cli, repo, tmp_path):
    seed(repo);unrelated_dynamic_import(repo)
    put(repo, 'packages/shared/api/orders.yaml', 'openapi: 3.1.0\ninfo:\n  title: orders\n')
    put(repo, 'packages/shared/src/orders_client.py', '# Code generated from orders.yaml. DO NOT EDIT.\nCLIENT = 1\n')
    put(repo, 'services/b/src/app.py', 'import orders_client\n')
    commit(repo, 'generated client');start(cli)
    put(repo, 'packages/shared/api/orders.yaml', 'openapi: 3.1.0\ninfo:\n  title: orders v2\n')
    _, m = discover(cli, tmp_path, ['packages/shared/api/orders.yaml'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'identified', assessment
    assert assessment['selected_services'] == ['services/b']
    rows = {r['path']: r for r in m['service_coverage']['semantic_requirements']['dependencies']['artifact_consumers']}
    assert rows['packages/shared/api/orders.yaml']['kind'] == 'openapi-schema'
    assert rows['packages/shared/api/orders.yaml']['consumers'] == ['packages/shared/src/orders_client.py']


def test_generated_module_change_records_its_producing_schema(cli, repo, tmp_path):
    seed(repo)
    put(repo, 'packages/shared/api/orders.yaml', 'openapi: 3.1.0\n')
    put(repo, 'packages/shared/src/orders_client.py', '# @generated from orders.yaml\nCLIENT = 1\n')
    put(repo, 'services/a/src/client.py', 'import orders_client\n')
    commit(repo, 'generated module');start(cli)
    put(repo, 'packages/shared/src/orders_client.py', '# @generated from orders.yaml\nCLIENT = 2\n')
    _, m = discover(cli, tmp_path, ['packages/shared/src/orders_client.py'])
    rows = {r['path']: r for r in m['service_coverage']['semantic_requirements']['dependencies']['artifact_consumers']}
    assert rows['packages/shared/src/orders_client.py'] == {'path': 'packages/shared/src/orders_client.py', 'kind': 'generated-code', 'owner': 'packages/shared',
                                                         'producers': ['packages/shared/api/orders.yaml'], 'consumers': []}
    assert 'services/a' in m['service_coverage']['assessment']['selected_services']


def test_unknown_dynamic_import_can_reach_a_fixture_consumer_module(cli, repo, tmp_path):
    seed(repo);unknown_dynamic_import(repo)
    put(repo, 'packages/shared/fixtures/orders.json', '{}\n')
    put(repo, 'packages/shared/src/orders_reader.py', 'FIXTURE = "orders.json"\n')
    commit(repo, 'reader module');start(cli)
    put(repo, 'packages/shared/fixtures/orders.json', '{"changed": true}\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/orders.json'])
    assessment = m['service_coverage']['assessment']
    assert reports_uncertain_dependents(assessment)
    assert any(u['path'] == 'services/b/src/loader.py' for u in assessment['dependency_uncertainties'])


def test_unclassified_data_change_keeps_the_exhaustive_fallback(cli, repo, tmp_path):
    seed(repo);unknown_dynamic_import(repo)
    put(repo, 'packages/shared/fixtures/orders.csv', 'id\n')
    put(repo, 'services/a/src/report.py', 'SOURCE = "orders.csv"\n')
    commit(repo, 'csv fixture');start(cli)
    put(repo, 'packages/shared/fixtures/orders.csv', 'id\n1\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/orders.csv'])
    assert reports_uncertain_dependents(m['service_coverage']['assessment'])


def test_directory_reference_counts_as_a_consumer(cli, repo, tmp_path):
    seed(repo);unrelated_dynamic_import(repo)
    put(repo, 'packages/shared/fixtures/orders.json', '{}\n')
    put(repo, 'services/a/src/orders.py', 'FIXTURE = "orders.json"\n')
    put(repo, 'services/b/src/globber.py', 'import glob\nFILES = glob.glob("fixtures/*.json")\n')
    commit(repo, 'glob consumer');start(cli)
    put(repo, 'packages/shared/fixtures/orders.json', '{"changed": true}\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/orders.json'])
    assert set(m['service_coverage']['assessment']['selected_services']) == {'services/a', 'services/b'}


def test_unrelated_literal_dynamic_import_does_not_block_a_module_change(cli, repo, tmp_path):
    seed(repo);unrelated_dynamic_import(repo);commit(repo, 'literal dynamic import');start(cli)
    put(repo, 'packages/shared/src/common.py', 'VALUE=2\n')
    _, m = discover(cli, tmp_path, ['packages/shared/src/common.py'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'identified', assessment
    assert any(u['target'] == 'vendor_plugin' for u in m['service_coverage']['semantic_requirements']['dependencies']['deferred_uncertainties'])


def test_unowned_root_document_keeps_its_literal_runtime_consumer(repo):
    import al_repository_facts as Facts
    import al_service_coverage as Coverage
    seed(repo)
    put(repo,'docs/prompt.md','# Runtime prompt\n')
    put(repo,'services/b/src/prompt.py','PROMPT = "docs/prompt.md"\n')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/prompt.md'],opaque=[])
    assert Coverage.assess(facts)['selected_services']==['services/b']
    assert facts['dependencies']['artifact_consumers'][0]['consumers']==['services/b/src/prompt.py']


def test_unowned_root_schema_keeps_its_literal_runtime_consumer(repo):
    import al_repository_facts as Facts
    import al_service_coverage as Coverage
    seed(repo)
    put(repo,'docs/schema.json','{"type":"object"}\n')
    put(repo,'services/b/src/schema.py','SCHEMA = "docs/schema.json"\n')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/schema.json'],opaque=[])
    assert Coverage.assess(facts)['selected_services']==['services/b']


def test_unowned_runtime_source_under_docs_stays_conservative(repo):
    import al_repository_facts as Facts
    import al_service_coverage as Coverage
    seed(repo)
    put(repo,'docs/runtime.py','def load(name):\n    return __import__(name)\n')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/runtime.py'],opaque=[])
    assessment=Coverage.assess(facts)
    assert assessment['dependency_uncertainties']
    assert assessment['selected_services']==assessment['potential_services']


def test_unattributed_runtime_consumer_does_not_become_a_docs_exclusion(repo):
    import al_repository_facts as Facts
    import al_service_coverage as Coverage
    seed(repo)
    put(repo,'docs/prompt.md','# Runtime prompt\n')
    put(repo,'tooling/consumer.py','PROMPT = "docs/prompt.md"\n')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/prompt.md'],opaque=[])
    assessment=Coverage.assess(facts)
    assert assessment['outcome']=='incomplete'
    assert any(g['code']=='unclassified-runtime-consumer' for g in assessment['gaps'])
