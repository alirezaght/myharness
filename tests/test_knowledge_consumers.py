"""Consumer inventory (#11/#12): import and runtime edges resolved against the repository layout."""

import json
import subprocess

import pytest

from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.consumers import consumers, coverage_summary
from harness.knowledge.extract import extract_source
from harness.knowledge.integration import refresh_until_settled
from harness.knowledge.models import RefreshRequest, SourceVersion
from harness.knowledge.policy import current_operator_scope
from harness.knowledge.sources import eligible, enumerate_sources, priority

FILES = {
    # Python: two services both own a top-level `application` package (src layout).
    "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*", "libs/*"]\n',
    "services/orders/pyproject.toml": '[project]\nname = "orders"\n',
    "services/orders/src/application/__init__.py": "",
    "services/orders/src/application/pricing.py": "def price():\n    return 1\n",
    "services/orders/src/application/api.py": (
        "from application.pricing import price\n"
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "@router.get('/orders/{order_id}')\n"
        "def read(order_id):\n    return price()\n"
    ),
    "services/orders/src/application/sub/__init__.py": "",
    "services/orders/src/application/sub/rel.py": "from ..pricing import price\n",
    "services/orders/tests/test_pricing.py": "from application import pricing\n",
    "services/billing/pyproject.toml": '[project]\nname = "billing"\n',
    "services/billing/src/application/__init__.py": "",
    "services/billing/src/application/pricing.py": "def price():\n    return 2\n",
    "services/billing/src/application/use.py": (
        "from application.pricing import price\n"
        "from shared_lib.money import cents\n"
        "import httpx\n\n"
        "def call(order_id):\n"
        "    return httpx.get(f'{BASE}/api/orders/{order_id}')\n"
    ),
    "libs/shared/pyproject.toml": '[project]\nname = "shared-lib"\n',
    "libs/shared/src/shared_lib/__init__.py": "",
    "libs/shared/src/shared_lib/money.py": "def cents():\n    return 100\n",
    # TypeScript: workspace packages, exports map, tsconfig paths with extends, relative imports.
    "package.json": json.dumps(
        {"name": "root", "private": True, "workspaces": ["apps/*", "packages/*"]}
    ),
    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
    "packages/ui/package.json": json.dumps(
        {"name": "@acme/ui", "exports": {".": "./src/index.ts", "./*": "./src/*.tsx"}}
    ),
    "packages/ui/src/index.ts": "export * from './Button';\n",
    "packages/ui/src/Button.tsx": "export const Button = () => null;\n",
    "packages/legacy/package.json": json.dumps({"name": "legacy-utils", "main": "lib/index.js"}),
    "packages/legacy/lib/index.js": "module.exports = {};\n",
    "packages/legacy/lib/strings.js": "module.exports = {};\n",
    # JSONC: comments and trailing commas are accepted.
    "tsconfig.base.json": (
        '{\n  // shared\n  "compilerOptions": {"baseUrl": ".",'
        ' "paths": {"@shared/*": ["packages/ui/src/*"],},},\n}\n'
    ),
    "apps/web/package.json": json.dumps({"name": "web"}),
    "apps/web/tsconfig.json": json.dumps(
        {
            "extends": "../../tsconfig.base.json",
            "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}},
        }
    ),
    "apps/web/src/lib/format.ts": "export const format = 1;\n",
    "apps/web/src/pages/home.tsx": (
        "import { Button } from '@acme/ui';\n"
        "import { Button as B } from '@acme/ui/Button';\n"
        "import { format } from '@/lib/format';\n"
        "// import { dead } from './dead';\n"
        "const utils = require('legacy-utils');\n"
        "const strings = require('legacy-utils/lib/strings');\n"
        "export const load = (id) => fetch(`/api/orders/${id}`);\n"
    ),
    "apps/web/src/pages/about.tsx": "import { format } from '../lib/format.js';\n",
    "apps/admin/package.json": json.dumps({"name": "admin"}),
    "apps/admin/src/lib/format.ts": "export const format = 2;\n",
    # Not under apps/web, so the `@/` alias of apps/web does not apply here.
    "apps/admin/src/page.tsx": "import { format } from '@/lib/format';\n",
    "apps/web/dist/bundle.js": "import x from '@acme/ui';\n",
}


@pytest.fixture
def mono(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_KNOWLEDGE", raising=False)
    root = tmp_path / "repo"
    for name, text in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    conn = connect(tmp_path / "state.db")
    controller = HarnessController(conn)
    scope = current_operator_scope(conn, root)
    result = refresh_until_settled(controller, scope)
    assert result.status == "published"
    yield conn, current_operator_scope(conn, root)
    conn.close()


def _found(conn, scope, target):
    result = consumers(conn, scope, target)
    return {(c["locator"], c["relation"], c["scope"]) for c in result["consumers"]}, result


def test_python_src_layout_imports_resolve_to_the_owning_unit(mono):
    conn, scope = mono
    found, result = _found(conn, scope, "services/orders/src/application/pricing.py")
    assert result["status"] == "complete"
    assert found == {
        ("services/orders/src/application/api.py", "imports", "intra-unit"),
        ("services/orders/src/application/sub/rel.py", "imports", "intra-unit"),
        ("services/orders/tests/test_pricing.py", "imports", "intra-unit"),
    }
    # billing's identically named `application` package shadows orders' one for its importers.
    found, _ = _found(conn, scope, "services/billing/src/application/pricing.py")
    assert found == {("services/billing/src/application/use.py", "imports", "intra-unit")}


def test_python_workspace_member_imports_are_cross_unit(mono):
    conn, scope = mono
    found, _ = _found(conn, scope, "shared_lib.money")
    assert found == {("services/billing/src/application/use.py", "imports", "cross-unit")}


def test_typescript_workspace_packages_aliases_and_relative_imports(mono):
    conn, scope = mono
    found, _ = _found(conn, scope, "packages/ui/src/Button.tsx")
    assert ("apps/web/src/pages/home.tsx", "imports", "cross-unit") in found
    assert ("packages/ui/src/index.ts", "imports", "intra-unit") in found
    assert not any(locator.startswith("apps/web/dist/") for locator, _, _ in found)
    found, _ = _found(conn, scope, "packages/ui/src/index.ts")
    assert found == {("apps/web/src/pages/home.tsx", "imports", "cross-unit")}
    found, _ = _found(conn, scope, "apps/web/src/lib/format.ts")
    assert found == {
        ("apps/web/src/pages/home.tsx", "imports", "intra-unit"),
        ("apps/web/src/pages/about.tsx", "imports", "intra-unit"),
    }
    found, _ = _found(conn, scope, "apps/admin/src/lib/format.ts")
    assert found == set()
    found, _ = _found(conn, scope, "packages/legacy/lib/strings.js")
    assert found == {("apps/web/src/pages/home.tsx", "imports", "cross-unit")}
    found, _ = _found(conn, scope, "packages/legacy/lib/index.js")
    assert found == {("apps/web/src/pages/home.tsx", "imports", "cross-unit")}


def test_tsconfig_extends_paths_resolve_for_importers_in_scope(mono):
    conn, scope = mono
    (scope.repository / "apps/web/src/pages/shared.tsx").write_text(
        "import { Button } from '@shared/Button';\n"
    )
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh_until_settled(HarnessController(conn), scope)
    scope = current_operator_scope(conn, scope.repository)
    found, _ = _found(conn, scope, "packages/ui/src/Button.tsx")
    assert ("apps/web/src/pages/shared.tsx", "imports", "cross-unit") in found


def test_runtime_http_consumers_cross_languages_and_units(mono):
    conn, scope = mono
    found, result = _found(conn, scope, "services/orders/src/application/api.py")
    assert result["resolved"]["endpoints"] == ["/orders/{}"]
    assert ("apps/web/src/pages/home.tsx", "calls-endpoint", "cross-unit") in found
    assert ("services/billing/src/application/use.py", "calls-endpoint", "cross-unit") in found
    found, _ = _found(conn, scope, "/api/orders/42")
    assert {locator for locator, _, _ in found} == {
        "apps/web/src/pages/home.tsx",
        "services/billing/src/application/use.py",
    }


def test_directory_target_and_unknown_target(mono):
    conn, scope = mono
    found, result = _found(conn, scope, "packages/legacy")
    assert result["resolved"]["files"] == [
        "packages/legacy/lib/index.js",
        "packages/legacy/lib/strings.js",
    ]
    assert {locator for locator, _, _ in found} == {"apps/web/src/pages/home.tsx"}
    found, result = _found(conn, scope, "does.not.exist")
    assert found == set() and result["resolved"] == {"files": [], "endpoints": []}


def test_coverage_summary_reports_languages_and_share(mono):
    conn, scope = mono
    summary = coverage_summary(conn, scope)
    assert summary["status"] == "complete" and summary["percent"] == 100
    assert summary["languages"] == ["python", "typescript/javascript"]
    assert summary["line"].startswith("knowledge graph: complete, 100% of ")


def test_generated_material_is_ineligible_and_code_is_indexed_first():
    assert not eligible("pnpm-lock.yaml")
    assert not eligible("apps/web/dist/bundle.js")
    assert not eligible("vendor/lib.min.js")
    assert eligible("apps/web/src/page.astro") and eligible("src/a.mts")
    assert sorted(["docs/a.md", "data/x.json", "package.json", "src/a.ts"], key=priority) == [
        "src/a.ts",
        "package.json",
        "docs/a.md",
        "data/x.json",
    ]


def test_no_file_count_cap_and_cached_sources_stream(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(30):
        (root / f"m{index}.py").write_text(f"import m{index + 1}\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    conn = connect(tmp_path / "state.db")
    scope = current_operator_scope(conn, root)
    inventory = enumerate_sources(scope, RefreshRequest())
    assert len(inventory.sources) == 30 and not inventory.coverage
    cached = frozenset(s.version_id for s in inventory.sources[:10])
    streamed = enumerate_sources(scope, RefreshRequest(), cached)
    assert [s.raw_retained for s in streamed.sources].count(False) == 10
    assert all(s.raw == b"" for s in streamed.sources if not s.raw_retained)
    capped = enumerate_sources(scope, RefreshRequest(max_files=5))
    assert len(capped.sources) == 5
    assert any(c.get("reason") == "file-count-capped" for c in capped.coverage)
    conn.close()


def test_incremental_batches_settle_without_a_cap(tmp_path, monkeypatch):
    from importlib import import_module

    monkeypatch.setattr(import_module("harness.knowledge.refresh"), "MAX_PUBLISH_SOURCES", 7)
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(30):
        (root / f"m{index}.py").write_text(f"import m{index + 1}\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    conn = connect(tmp_path / "state.db")
    controller = HarnessController(conn)
    scope = current_operator_scope(conn, root)
    first = refresh_until_settled(controller, scope, rounds=1)
    assert first.status == "published-partial"
    settled = refresh_until_settled(controller, current_operator_scope(conn, root))
    assert settled.status == "published"
    assert coverage_summary(conn, scope)["indexed_files"] == 30
    found = consumers(conn, current_operator_scope(conn, root), "m7")
    assert [c["locator"] for c in found["consumers"]] == ["m6.py"]
    conn.close()


def _extract(locator, text, kind="text"):
    raw = text.encode()
    source = SourceVersion("s", "v", locator, kind, "", raw, {})
    return [
        (a.predicate, a.object, raw[a.start_byte : a.end_byte].decode())
        for a in extract_source(source).assertions
        if a.predicate != "described-by"
    ]


def test_script_extraction_records_specifiers_and_endpoints():
    edges = _extract(
        "web/src/a.ts",
        "import a from 'pkg';\n/* import b from 'gone'; */\nexport * from './b';\n"
        "const c = require(\"../c\");\nconst é = 1; await import('./d');\n"
        "app.get('/health', (req, res) => res.end());\naxios.post('/api/items', body);\n",
    )
    assert ("imports", "import:pkg", "import a from 'pkg'") in edges
    assert ("imports", "path:web/src/b", "export * from './b'") in edges
    assert ("imports", "path:web/c", 'require("../c"') in edges
    assert ("imports", "path:web/src/d", "import('./d'") in edges
    assert not any("gone" in str(edge) for edge in edges)
    assert ("serves-endpoint", "endpoint:/health", "app.get('/health', (req, res) =>") in edges
    assert ("calls-endpoint", "endpoint:/api/items", "axios.post('/api/items'") in edges


def test_python_extraction_records_imported_names_and_endpoints():
    edges = _extract(
        "svc/src/pkg/api.py",
        "from pkg.domain import models\n@router.post('/items/<item_id>')\ndef create():\n"
        "    return requests.get('https://other/api/v1/users/' + user)\n",
        "python",
    )
    assert ("imports", "module:pkg.domain", "from pkg.domain import models\n") in edges
    assert ("serves-endpoint", "endpoint:/items/{}", "router.post('/items/<item_id>')") in edges
    assert any(edge[:2] == ("calls-endpoint", "endpoint:/api/v1/users/{}") for edge in edges)
