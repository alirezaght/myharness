"""Consumer inventory (#11/#12/#34): AL's import and runtime edges, seen through the graph."""

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
from tests.al_facts_support import al_facts

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
    _FACTS["index"] = tmp_path / "al-index"
    yield conn, current_operator_scope(conn, root)
    conn.close()


_FACTS = {}


def _found(conn, scope, target):
    result = consumers(conn, scope, target, facts=al_facts(scope.repository, _FACTS["index"]))
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


def test_consumers_follow_re_exports_transitively(mono):
    """#31: an index.ts barrel re-exporting a component makes its importers consumers too."""
    conn, scope = mono
    found, result = _found(conn, scope, "packages/ui/src/Button.tsx")
    assert ("apps/web/src/pages/home.tsx", "imports-via-reexport", "cross-unit") in found
    row = next(c for c in result["consumers"] if c["relation"] == "imports-via-reexport")
    assert row["through"] == ["packages/ui/src/index.ts"] and row["via"] == "@acme/ui"
    # Two levels of barrels; a plain import (not a re-export) is not followed further.
    files = {
        "packages/ui/src/all.ts": "export * from './index';\n",
        "packages/ui/src/screen.ts": "import { Button } from './all';\nexport const s = 1;\n",
        "packages/ui/src/app.ts": "import { s } from './screen';\n",
    }
    for name, text in files.items():
        (scope.repository / name).write_text(text)
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh_until_settled(HarnessController(conn), scope)
    scope = current_operator_scope(conn, scope.repository)
    _, result = _found(conn, scope, "packages/ui/src/Button.tsx")
    rows = {c["locator"]: c for c in result["consumers"] if c["relation"] == "imports-via-reexport"}
    assert rows["packages/ui/src/screen.ts"]["through"] == [
        "packages/ui/src/index.ts",
        "packages/ui/src/all.ts",
    ]
    assert "packages/ui/src/app.ts" not in {c["locator"] for c in result["consumers"]}


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
    found = consumers(
        conn, current_operator_scope(conn, root), "m7", facts=al_facts(root, tmp_path / "idx")
    )
    assert [c["locator"] for c in found["consumers"]] == ["m6.py"]
    conn.close()


def _extract(locator, text, kind="text"):
    raw = text.encode()
    source = SourceVersion("s", "v", locator, kind, "", raw, {})
    return [(a.predicate, a.object) for a in extract_source(source).assertions]


def test_knowledge_extraction_keeps_text_and_symbols_without_import_edges():
    """#34: one analyser. Import and endpoint edges are AL's code facts, not knowledge rows."""
    edges = _extract("web/src/a.ts", "import a from 'pkg';\nfetch('/api/items');\n")
    assert [predicate for predicate, _ in edges] == ["described-by"]
    edges = _extract(
        "svc/pkg/api.py",
        "from pkg.domain import models\n@router.post('/items')\ndef create():\n    pass\n",
        "python",
    )
    assert ("defines", "symbol:svc.pkg.api:create") in edges
    assert not any(p in {"imports", "serves-endpoint", "calls-endpoint"} for p, _ in edges)


def test_without_al_the_view_degrades_instead_of_guessing(mono, monkeypatch):
    from tests.al_facts_support import real_resolve

    conn, scope = mono
    for name in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("harness.knowledge.al_facts.resolve", real_resolve)
    assert real_resolve(scope.repository) is None
    explicit = real_resolve(scope.repository, platform="codex", session="r1", launcher="alctl")
    # The plain `run` form the resident AL service serves (no --existing-run).
    assert explicit.argv("edges") == [
        "alctl",
        "run",
        "--repository",
        str(scope.repository),
        "--platform",
        "codex",
        "--session",
        "r1",
        "--",
        "facts",
        "edges",
    ]
    monkeypatch.setenv("CLAUDE_SESSION_ID", "conversation")
    assert real_resolve(scope.repository).session is None
    monkeypatch.delenv("CLAUDE_SESSION_ID")
    result = consumers(conn, scope, "packages/ui/src/Button.tsx")
    assert result["status"] == "unavailable" and result["consumers"] == []
    assert result["gaps"][0]["reason"] == "al-facts-unavailable"

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, b"", b"no protected run binding")

    from harness.knowledge.al_facts import ALFacts

    broken = ALFacts(scope.repository, "claude", "gone", launcher="alctl", runner=failing)
    result = consumers(conn, scope, "packages/ui/src/Button.tsx", facts=broken)
    assert result["status"] == "unavailable"
    assert "no protected run binding" in result["gaps"][0]["detail"]


def test_unindexed_repository_costs_no_al_query(tmp_path):
    """#12: the graph stays opt-in; an unindexed repository never asks AL."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    conn = connect(tmp_path / "state.db")
    calls = []
    facts = al_facts(root, tmp_path / "idx", calls)
    with pytest.raises(PermissionError):
        consumers(conn, current_operator_scope(conn, root), "x.py", facts=facts)
    assert calls == []
    conn.close()


def test_refresh_stores_al_edges_once_available_and_keeps_them_without_al(tmp_path, monkeypatch):
    """#34: without AL a refresh indexes text and symbols only; once AL answers, code files get
    AL's edges; a later refresh without AL keeps them (per-file edges depend only on bytes)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("import b\n")
    (root / "b.py").write_text("X = 1\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    conn = connect(tmp_path / "state.db")
    controller = HarnessController(conn)

    def imports():
        return conn.execute(
            "SELECT count(*) FROM knowledge_assertions a JOIN knowledge_projects p "
            "ON p.project_id=a.project_id WHERE a.predicate='imports'"
        ).fetchone()[0]

    monkeypatch.setattr("harness.knowledge.al_facts.resolve", lambda repository, **_: None)
    assert refresh_until_settled(controller, current_operator_scope(conn, root)).status == (
        "published"
    )
    assert imports() == 0
    monkeypatch.setattr(
        "harness.knowledge.al_facts.resolve",
        lambda repository, **_: al_facts(repository, tmp_path / "idx"),
    )
    assert refresh_until_settled(controller, current_operator_scope(conn, root)).status == (
        "published"
    )
    assert imports() == 1
    monkeypatch.setattr("harness.knowledge.al_facts.resolve", lambda repository, **_: None)
    result = refresh_until_settled(controller, current_operator_scope(conn, root))
    assert result.status == "unchanged"
    conn.close()
