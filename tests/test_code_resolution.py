"""The resolvers are pure: paths and file content in, edges and specifiers out (#11)."""

import json

from harness.code_resolution import Layout, endpoint_path, python_endpoint_edges, script_edges

FILES = {
    "services/a/pyproject.toml": "",
    "services/a/src/application/__init__.py": "",
    "services/a/src/application/core.py": "",
    "services/a/tests/test_core.py": "",
    "services/b/pyproject.toml": "",
    "services/b/src/application/__init__.py": "",
    "services/b/src/application/use.py": "",
    "packages/ui/package.json": json.dumps(
        {"name": "@acme/ui", "exports": {".": "./src/index.ts", "./*": "./src/*.tsx"}}
    ),
    "packages/ui/src/index.ts": "",
    "packages/ui/src/Button.tsx": "",
    "apps/web/package.json": json.dumps({"name": "web"}),
    "apps/web/tsconfig.json": (
        '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]},},}'
    ),
    "apps/web/src/lib/format.ts": "",
}


def layout():
    return Layout(FILES, FILES.__getitem__)


def test_python_src_layout_names_and_shadowing():
    shape = layout()
    names = dict(shape.python_names("services/a/src/application/core.py"))
    assert names["services/a/src"] == "application.core"
    assert shape.python_owner("services/a/tests/test_core.py", "application.core") == (
        "services/a/src"
    )
    assert shape.python_owner("services/b/src/application/use.py", "application.core") == (
        "services/b/src"
    )
    assert shape.unit("services/a/src/application/core.py") == "services/a"


def test_script_specifiers_from_workspace_exports_and_tsconfig_paths():
    shape = layout()
    assert ("@acme/ui/Button", "") in shape.script_specifiers("packages/ui/src/Button.tsx")
    assert ("@acme/ui", "") in shape.script_specifiers("packages/ui/src/index.ts")
    specs = shape.script_specifiers("apps/web/src/lib/format.ts")
    assert ("@/lib/format", "apps/web") in specs
    assert ("src/lib/format", "apps/web") in specs


def test_unreadable_manifest_is_reported_not_raised():
    shape = Layout(FILES)
    assert shape.script_specifiers("packages/ui/src/Button.tsx") == set()
    assert "packages/ui/package.json" in shape.unreadable


def test_edges_from_content():
    edges = script_edges("web/a.ts", "import x from '../lib/y';\nfetch('/api/v1/items?q=1');\n")
    assert [(e.relation, e.target) for e in edges] == [
        ("imports", "path:lib/y"),
        ("calls-endpoint", "endpoint:/api/v1/items"),
    ]
    raw = b"@app.route('/items/<int:item_id>')\ndef item(item_id):\n    return 1\n"
    (edge,) = python_endpoint_edges(raw)
    assert (edge.relation, edge.target) == ("serves-endpoint", "endpoint:/items/{}")
    assert raw[edge.start : edge.end] == b"app.route('/items/<int:item_id>')"
    assert endpoint_path("https://host:8080/a/:id/") == "/a/{}"
    assert endpoint_path("not a path") is None
