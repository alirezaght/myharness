"""Changed artifacts route to their actual consumers without unrelated import blockers."""
from conftest import _git
from test_service_coverage import seed, start, discover, put


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


def test_unreferenced_shared_fixture_is_an_explicit_exhaustive_requirement(cli, repo, tmp_path):
    seed(repo);put(repo, 'packages/shared/fixtures/lonely.json', '{}\n');commit(repo, 'lonely fixture');start(cli)
    put(repo, 'packages/shared/fixtures/lonely.json', '{"changed": true}\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/lonely.json'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'exhaustive'
    assert any(u['code'] == 'uncertain-artifact-consumer' for u in assessment['dependency_uncertainties'])
    assert assessment['selected_services'] == assessment['potential_services']


def test_changed_importable_module_keeps_exhaustive_dynamic_import_fallback(cli, repo, tmp_path):
    seed(repo);unknown_dynamic_import(repo);commit(repo, 'dynamic import');start(cli)
    put(repo, 'packages/shared/src/common.py', 'VALUE=2\n')
    _, m = discover(cli, tmp_path, ['packages/shared/src/common.py'])
    assessment = m['service_coverage']['assessment']
    assert assessment['outcome'] == 'exhaustive'
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
    assert assessment['outcome'] == 'exhaustive'
    assert any(u['path'] == 'services/b/src/loader.py' for u in assessment['dependency_uncertainties'])


def test_unclassified_data_change_keeps_the_exhaustive_fallback(cli, repo, tmp_path):
    seed(repo);unknown_dynamic_import(repo)
    put(repo, 'packages/shared/fixtures/orders.csv', 'id\n')
    put(repo, 'services/a/src/report.py', 'SOURCE = "orders.csv"\n')
    commit(repo, 'csv fixture');start(cli)
    put(repo, 'packages/shared/fixtures/orders.csv', 'id\n1\n')
    _, m = discover(cli, tmp_path, ['packages/shared/fixtures/orders.csv'])
    assert m['service_coverage']['assessment']['outcome'] == 'exhaustive'


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
