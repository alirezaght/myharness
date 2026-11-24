"""Code facts (#34): one import analyser for AL and the Harness knowledge graph.

Per-file edges are pure facts of one blob; the repository layout resolves who consumes a path,
module or endpoint. The `facts` command is read-only and prints canonical JSON."""
import json
import subprocess
import sys

import pytest

import al_analysis_index as index
import al_blob_analysis as blob
import al_code_facts as facts
from conftest import _git

LAYOUT = {
    'services/a/pyproject.toml': '',
    'services/a/src/application/__init__.py': '',
    'services/a/src/application/core.py': '',
    'services/a/tests/test_core.py': '',
    'services/b/pyproject.toml': '',
    'services/b/src/application/__init__.py': '',
    'services/b/src/application/use.py': '',
    'packages/ui/package.json': json.dumps({'name': '@acme/ui', 'exports': {'.': './src/index.ts', './*': './src/*.tsx'}}),
    'packages/ui/src/index.ts': '',
    'packages/ui/src/Button.tsx': '',
    'apps/web/package.json': json.dumps({'name': 'web'}),
    'apps/web/tsconfig.json': '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]},},}',
    'apps/web/src/lib/format.ts': '',
}

REPOSITORY = {
    'pyproject.toml': '[tool.uv.workspace]\nmembers = ["services/*", "libs/*"]\n',
    'services/orders/pyproject.toml': '[project]\nname = "orders"\n',
    'services/orders/src/application/__init__.py': '',
    'services/orders/src/application/pricing.py': 'def price():\n    return 1\n',
    'services/orders/src/application/api.py': ("from application.pricing import price\n"
                                               "@router.get('/orders/{order_id}')\ndef read(order_id):\n    return 1\n"),
    'libs/shared/pyproject.toml': '[project]\nname = "shared-lib"\n',
    'libs/shared/src/shared_lib/__init__.py': '',
    'libs/shared/src/shared_lib/money.py': 'def cents():\n    return 100\n',
    'services/billing/pyproject.toml': '[project]\nname = "billing"\n',
    'services/billing/src/billing/use.py': ("from shared_lib.money import cents\nimport httpx\n"
                                            "def call(i):\n    return httpx.get(f'{BASE}/api/orders/{i}')\n"),
    'package.json': json.dumps({'name': 'root', 'private': True}),
    'pnpm-workspace.yaml': "packages:\n  - 'packages/*'\n  - 'apps/*'\n",
    'packages/ui/package.json': json.dumps({'name': '@acme/ui', 'exports': {'.': './src/index.ts'}}),
    'packages/ui/src/index.ts': "export * from './Button';\n",
    'packages/ui/src/Button.tsx': 'export const Button = () => null;\n',
    'apps/web/package.json': json.dumps({'name': 'web'}),
    'apps/web/tsconfig.json': '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
    'apps/web/src/lib/format.ts': 'export const format = 1;\n',
    'apps/web/src/page.tsx': ("import { Button } from '@acme/ui';\nimport { format } from '@/lib/format';\n"
                              "export const load = (id) => fetch(`/api/orders/${id}`);\n"),
    'apps/web/dist/bundle.js': "import x from '@acme/ui';\n",
}


def _layout():
    return facts.Layout(LAYOUT, LAYOUT.__getitem__)


def test_python_src_layout_names_and_shadowing():
    shape = _layout()
    assert dict(shape.python_names('services/a/src/application/core.py'))['services/a/src'] == 'application.core'
    assert shape.python_owner('services/a/tests/test_core.py', 'application.core') == 'services/a/src'
    assert shape.python_owner('services/b/src/application/use.py', 'application.core') == 'services/b/src'
    assert shape.unit('services/a/src/application/core.py') == 'services/a'


def test_script_specifiers_from_workspace_exports_and_tsconfig_paths():
    shape = _layout()
    assert ('@acme/ui/Button', '') in shape.script_specifiers('packages/ui/src/Button.tsx')
    assert ('@acme/ui', '') in shape.script_specifiers('packages/ui/src/index.ts')
    specs = shape.script_specifiers('apps/web/src/lib/format.ts')
    assert ('@/lib/format', 'apps/web') in specs and ('src/lib/format', 'apps/web') in specs


def test_unreadable_manifest_is_reported_not_raised():
    shape = facts.Layout(LAYOUT)
    assert shape.script_specifiers('packages/ui/src/Button.tsx') == set()
    assert 'packages/ui/package.json' in shape.unreadable


def test_per_file_edges_are_pure_facts_of_path_and_bytes():
    value = blob.code_edges('web/a.ts', b"import x from '../lib/y';\n/* import z from 'gone'; */\nfetch('/api/v1/items?q=1');\n")
    assert value['status'] == 'ok'
    assert [(e[0], e[1]) for e in value['edges']] == [('imports', 'path:lib/y'), ('calls-endpoint', 'endpoint:/api/v1/items')]
    raw = b"from .models import Item\n@app.route('/items/<int:item_id>')\ndef item(item_id):\n    return 1\n"
    edges = blob.code_edges('svc/pkg/api.py', raw)['edges']
    assert edges[0][:3] == ['imports', 'module:svc.pkg.models', {'names': ['Item']}]
    served = edges[1]
    assert served[:2] == ['serves-endpoint', 'endpoint:/items/{}'] and raw[served[3]:served[4]] == b"app.route('/items/<int:item_id>')"
    assert blob.code_edges('x.py', b'def (:\n')['status'] == 'syntax-error'
    assert blob.code_edges('x.ts', b'\xff\xfe')['status'] == 'undecodable'
    assert blob.endpoint_path('https://host:8080/a/:id/') == '/a/{}' and blob.endpoint_path('not a path') is None


@pytest.fixture
def monorepo(repo):
    for name, text in REPOSITORY.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, 'add', '.')
    return repo


def _consumers(repo, target, commit=None):
    return {(c['locator'], c['relation'], c['scope']) for c in facts.consumers(facts.View(repo, commit), target)['consumers']}


def test_consumers_across_python_workspaces_and_typescript_packages(monorepo):
    assert _consumers(monorepo, 'services/orders/src/application/pricing.py') == {
        ('services/orders/src/application/api.py', 'imports', 'intra-unit')}
    assert _consumers(monorepo, 'shared_lib.money') == {('services/billing/src/billing/use.py', 'imports', 'cross-unit')}
    button = _consumers(monorepo, 'packages/ui/src/Button.tsx')
    assert ('packages/ui/src/index.ts', 'imports', 'intra-unit') in button
    assert ('apps/web/src/page.tsx', 'imports-via-reexport', 'cross-unit') in button
    assert not any(locator.startswith('apps/web/dist/') for locator, _, _ in button)
    assert _consumers(monorepo, 'apps/web/src/lib/format.ts') == {('apps/web/src/page.tsx', 'imports', 'intra-unit')}
    callers = {('apps/web/src/page.tsx', 'calls-endpoint'), ('services/billing/src/billing/use.py', 'calls-endpoint')}
    assert {(locator, relation) for locator, relation, _ in _consumers(monorepo, '/api/orders/7')} == callers
    served = _consumers(monorepo, 'services/orders/src/application/api.py')
    assert {(locator, relation) for locator, relation, scope in served if scope == 'cross-unit'} == callers


def test_commit_view_answers_at_the_commit(monorepo):
    _git(monorepo, 'config', 'user.name', 'fixture')
    _git(monorepo, 'config', 'user.email', 'fixture@example.invalid')
    _git(monorepo, 'commit', '-qm', 'monorepo')
    (monorepo / 'apps/web/src/other.ts').write_text("import { format } from './lib/format';\n")
    _git(monorepo, 'add', '.')
    assert ('apps/web/src/other.ts', 'imports', 'intra-unit') in _consumers(monorepo, 'apps/web/src/lib/format.ts')
    assert ('apps/web/src/other.ts', 'imports', 'intra-unit') not in _consumers(monorepo, 'apps/web/src/lib/format.ts', 'HEAD')


def test_edges_are_stored_facts_reused_by_the_next_query(monorepo):
    first = facts.edge_rows(facts.View(monorepo))
    index.flush()
    index._STORES.clear()
    view = facts.View(monorepo)
    second = facts.edge_rows(view)
    assert first == second and second['status'] == 'complete'
    store = index.store(monorepo)
    assert store.hits and not store.misses


def test_facts_command_is_read_only_canonical_json(monorepo):
    script = facts.__file__.replace('al_code_facts.py', 'al_state.py')
    state = monorepo / '.claude' / 'al-runtime'
    before = sorted(p.name for p in state.rglob('*')) if state.exists() else []
    result = subprocess.run([sys.executable, script, 'facts', 'consumers', 'packages/ui/src/index.ts'], cwd=monorepo,
                            capture_output=True, check=True)
    value = json.loads(result.stdout)
    assert result.stdout == (facts.canonical(value) + '\n').encode()
    assert value['schema'] == 1 and value['status'] == 'complete'
    assert [c['locator'] for c in value['consumers']] == ['apps/web/src/page.tsx']
    after = sorted(p.name for p in state.rglob('*')) if state.exists() else []
    assert before == after
